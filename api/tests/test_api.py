"""API tests: a fresh local deployment per module (temp data dir, jobs run inline)."""
import importlib
import json
import os
import re

import pytest
from fastapi.testclient import TestClient



@pytest.fixture(scope="module")
def client(synth_small, tmp_path_factory):
    data = tmp_path_factory.mktemp("api")
    os.environ.update({"GWASHUB_DATA_DIR": str(data / "local"), "GWASHUB_SHARED_RESOURCES": synth_small["resources"],
                       "GWASHUB_INLINE_JOBS": "1", "MPLCONFIGDIR": "/tmp/gwashub-mpl"})
    if os.environ.get("GWASHUB_TEST_DB_URL"):  # e.g. a throwaway Postgres: tables are dropped first
        os.environ["GWASHUB_DB_URL"] = os.environ["GWASHUB_TEST_DB_URL"]
    from gwashub_api import config, db
    config.settings.cache_clear()
    db.reset_engine()
    if os.environ.get("GWASHUB_TEST_DB_URL"):
        from sqlalchemy import text
        with db.engine().begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        db.reset_engine()
    seed = importlib.import_module("devtools.seed")
    seed.main()
    from gwashub_api.main import app
    with TestClient(app) as c:
        yield c
    db.reset_engine()
    config.settings.cache_clear()
    for k in ("GWASHUB_DATA_DIR", "GWASHUB_SHARED_RESOURCES", "GWASHUB_INLINE_JOBS", "GWASHUB_DB_URL"):
        os.environ.pop(k, None)


def as_user(client, email):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    return client


def upload(client, synth, entry, project="hermes3", start=True, **override):
    """Upload a synthetic file (it lands as pending) and, unless start=False, start its QC as the same user."""
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    meta.update(override)
    with open(os.path.join(synth["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post(f"/api/projects/{project}/datasets", files={"file": (entry["file"], f)}, data={"meta": json.dumps(meta)})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"
    if start:
        assert client.post(f"/api/datasets/{r.json()['id']}/rerun").status_code == 200
    return r.json()


def test_login_and_projects(client):
    assert client.post("/api/auth/login", json={"email": "nobody@x"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401
    as_user(client, "demo@gwashub.org")
    roles = {p["id"]: p["role"] for p in client.get("/api/projects").json()}
    assert roles == {"hermes3": "reviewer", "hcm": "uploader", "practice": "reviewer"}  # everyone is in the Practice project
    as_user(client, "noor@example.org")
    assert client.get("/api/projects/hermes3").status_code == 404  # not a member: don't reveal it


def test_column_suggestion(client, synth_small):
    as_user(client, "jonas@example.org")
    entry = synth_small["manifest"][2]  # METAL style, CSV, odds ratios
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post("/api/projects/hermes3/column-suggestion", files={"file": (entry["file"], f)})
    body = r.json()
    assert body["mapping"] == entry["column_mapping"]
    assert body["effect_type"] == "or"
    assert len(body["preview"]) == 5


def test_stop_qc(client, synth_small):
    """Stopping QC: the run is recorded as stopped (by whom), its unfinished steps as skipped, the dataset goes back to
    pending, and a late event from its Batch job doesn't overwrite any of that. Reviewers stop any dataset, uploaders
    their own; in bulk, the rest are skipped with a reason."""
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Dataset
    from gwashub_api.routers.datasets import start_run
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    a, b = (upload(client, synth_small, entry, start=False) for _ in range(2))
    with SessionLocal() as db:  # two runs waiting for a machine (tests run QC inline, so make them by hand)
        for i in (a["id"], b["id"]):
            ds = db.get(Dataset, i)
            ds.status = "processing"
            run = start_run(db, ds)
            run.progress, run.batch_job_id = [{"key": "gwashub/parse", "state": "running"}, {"key": "gwashub/filter", "state": "queued"}], f"job-{i}"
        db.commit()
    as_user(client, "mei@example.org")  # another uploader can't stop it
    assert client.post(f"/api/datasets/{a['id']}/stop").status_code == 403
    as_user(client, "jonas@example.org")
    assert client.post(f"/api/datasets/{a['id']}/stop").json()["status"] == "pending"
    d = client.get(f"/api/datasets/{a['id']}").json()
    assert d["status"] == "pending" and d["run"]["status"] == "stopped" and "Stopped by" in d["run"]["error"]
    assert client.post(f"/api/datasets/{a['id']}/stop").status_code == 409  # nothing left to stop
    with SessionLocal() as db:
        run = db.get(Dataset, a["id"]).runs[-1]
        assert [p["state"] for p in run.progress] == ["skipped", "skipped"]
        event = {"detail": {"jobId": "job-%d" % a["id"], "jobName": f"gwashub-qc-{run.id}-g0", "status": "FAILED", "statusReason": "terminated"}}
        assert "ignored" in jobs.handle_batch_event(db, event)
        assert db.get(Dataset, a["id"]).status == "pending"
    as_user(client, "ama@example.org")  # a reviewer, in bulk: b stops, a is no longer running
    r = client.post("/api/projects/hermes3/datasets/stop", json={"ids": [a["id"], b["id"]]}).json()
    assert r["stopped"] == 1 and r["skipped"] == [{"id": a["id"], "reason": "QC isn't running"}]
    assert client.get(f"/api/datasets/{b['id']}").json()["status"] == "pending"
    for i in (a["id"], b["id"]):
        assert client.delete(f"/api/datasets/{i}").status_code in (200, 204)


def test_running_step_shows_its_output_tail(client, synth_small):
    """While a run runs, the panel's steps come from its progress.json, including the running step's last output lines."""
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Dataset
    from gwashub_api.routers.datasets import start_run
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0], start=False)
    with SessionLocal() as db:
        ds = db.get(Dataset, up["id"])
        ds.status = "processing"
        run = start_run(db, ds)
        run.status, run.run_dir = "running", os.path.join(os.environ["GWASHUB_DATA_DIR"], "tail-test-run")
        os.makedirs(run.run_dir, exist_ok=True)
        with open(os.path.join(run.run_dir, "progress.json"), "w") as f:
            json.dump([{"key": "gwashub/parse", "title": "Parse", "state": "running",
                        "tail": {"lines": ["Read 40,000 rows"], "at": "2026-10-12T09:00:00+00:00"}}], f)
        db.commit()
    step = client.get(f"/api/datasets/{up['id']}").json()["run"]["steps"][0]
    assert step["state"] == "running" and step["tail"]["lines"] == ["Read 40,000 rows"]
    assert client.post(f"/api/datasets/{up['id']}/stop").status_code == 200
    assert client.delete(f"/api/datasets/{up['id']}").status_code in (200, 204)


def test_remap_columns_and_effect_type(client, synth_small):
    """The column mapping and effect type can be changed without uploading again: only to the file's own columns and
    with the columns QC needs; logged one field at a time; QC is advised to re-run, and refused while it runs."""
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Dataset
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, entry, start=False)
    cols = client.get(f"/api/datasets/{up['id']}/columns").json()["columns"]
    mapping = dict(entry["column_mapping"])
    assert set(mapping.values()) <= set(cols)
    extra = [k for k in ("n", "info", "rsid") if k in mapping][0]
    r = client.patch(f"/api/datasets/{up['id']}", json={"column_mapping": {**mapping, extra: None}})
    assert r.status_code == 200 and r.json()["changed"] == [f"column {extra}"]
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert extra not in d["column_mapping"] and d["edits"][-1]["changes"][f"column {extra}"] == [mapping[extra], None]
    bad = client.patch(f"/api/datasets/{up['id']}", json={"column_mapping": {**mapping, "se": "no_such_column"}})
    assert bad.status_code == 422 and "no column named no_such_column" in bad.json()["detail"]
    bad = client.patch(f"/api/datasets/{up['id']}", json={"column_mapping": {k: v for k, v in mapping.items() if k != "eaf"}})
    assert bad.status_code == 422 and "eaf" in bad.json()["detail"]
    other = "beta" if d["effect_type"] == "or" else "or"
    r = client.patch(f"/api/datasets/{up['id']}", json={"effect_type": other})
    assert r.status_code == 200 and r.json()["changed"] == ["effect_type"]
    assert client.patch(f"/api/datasets/{up['id']}", json={"effect_type": "log-odds"}).status_code == 422
    as_user(client, "mei@example.org")  # another uploader
    assert client.get(f"/api/datasets/{up['id']}/columns").status_code in (403, 404)
    as_user(client, "jonas@example.org")
    with SessionLocal() as db:
        db.get(Dataset, up["id"]).status = "processing"
        db.commit()
    assert client.patch(f"/api/datasets/{up['id']}", json={"column_mapping": mapping}).status_code == 409
    with SessionLocal() as db:
        db.get(Dataset, up["id"]).status = "pending"
        db.commit()
    assert client.delete(f"/api/datasets/{up['id']}").status_code in (200, 204)


def test_stop_meta_analysis(client):
    """Reviewers stop a queued or running meta-analysis: it's recorded as stopped (by whom), a late Batch event doesn't
    change that, and it can then be deleted. Uploaders can't stop one; a finished one can't be stopped."""
    from sqlalchemy import select
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import MetaAnalysis, User
    with SessionLocal() as db:  # waiting for a machine (tests run meta-analyses inline, so make it by hand)
        uid = db.scalar(select(User.id).where(User.email == "ama@example.org"))
        ma = MetaAnalysis(project_id="hermes3", name="To stop", settings={}, dataset_ids=[], method="gwashub/gwama",
                          created_by=uid, status="queued", batch_job_id="job-meta-stop")
        db.add(ma)
        db.commit()
        mid = ma.id
    as_user(client, "jonas@example.org")  # an uploader
    assert client.post(f"/api/meta/{mid}/stop").status_code == 403
    as_user(client, "ama@example.org")
    assert client.delete(f"/api/meta/{mid}").status_code == 409  # still running
    r = client.post(f"/api/meta/{mid}/stop").json()
    assert r["status"] == "stopped" and r["error"].startswith("Stopped by")
    assert client.post(f"/api/meta/{mid}/stop").status_code == 409
    with SessionLocal() as db:
        event = {"detail": {"jobId": "job-meta-stop", "jobName": f"gwashub-meta-{mid}", "status": "FAILED", "statusReason": "terminated"}}
        assert "ignored" in jobs.handle_batch_event(db, event)
        assert db.get(MetaAnalysis, mid).status == "stopped"
    assert client.delete(f"/api/meta/{mid}").status_code == 204


def test_running_meta_analysis_shows_its_output_tail(client):
    """A running meta-analysis's view carries its console's last lines and which part runs (progress.json); a
    finished one doesn't."""
    import json
    from sqlalchemy import select
    from gwashub_api import storage
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import MetaAnalysis, User
    run_dir = storage.location("results", f"meta/hermes3/tail-{storage.stamp()}")
    live = {"stage": "method", "tail": {"lines": ["GWAMA: study 3 of 12"], "at": "2026-10-12T10:00:00+00:00"}}
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "ama@example.org"))
        ma = MetaAnalysis(project_id="hermes3", name="Tail", settings={}, dataset_ids=[], method="gwashub/gwama",
                          created_by=uid, status="running", run_dir=run_dir)
        db.add(ma)
        db.commit()
        mid = ma.id
    os.makedirs(run_dir, exist_ok=True)
    with open(storage.join(run_dir, "progress.json"), "w") as f:
        json.dump(live, f)
    as_user(client, "ama@example.org")
    assert client.get(f"/api/meta/{mid}").json()["live"] == live
    with SessionLocal() as db:
        db.get(MetaAnalysis, mid).status = "stopped"
        db.commit()
    assert client.get(f"/api/meta/{mid}").json()["live"] is None


def test_contact_email_owns_a_dataset_like_its_uploader(client, synth_small):
    """Someone uploading for a cohort can name its analyst as contact email: that person (an uploader too) then sees and
    acts on the dataset as if they had uploaded it. Changing the contact moves that access."""
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, entry, start=False, cohort="Contact Cohort", study_meta={"contact_email": " MEI@example.org "})
    other = upload(client, synth_small, entry, start=False, cohort="Contact Cohort B", study_meta={})
    as_user(client, "mei@example.org")
    d = client.get(f"/api/datasets/{up['id']}")
    assert d.status_code == 200 and d.json()["can_edit"] and d.json()["mine"]
    assert client.get(f"/api/datasets/{up['id']}/files/raw").status_code in (200, 302)
    assert client.patch(f"/api/datasets/{up['id']}", json={"phenotype": entry["phenotype"], "n_cases": 101}).status_code == 200
    rows = {r["id"]: r for r in client.get("/api/projects/hermes3/datasets").json()}
    assert rows[up["id"]]["mine"] and rows[up["id"]]["can_open"] and not rows[other["id"]]["can_open"]
    assert client.get(f"/api/datasets/{other['id']}").status_code == 403  # no contact named: still only the uploader's
    as_user(client, "jonas@example.org")
    meta = client.get(f"/api/datasets/{up['id']}").json()["study_meta"]
    assert client.patch(f"/api/datasets/{up['id']}", json={"study_meta": {**meta, "contact_email": "someone@else.org"}}).status_code == 200
    as_user(client, "mei@example.org")
    assert client.get(f"/api/datasets/{up['id']}").status_code == 403
    as_user(client, "jonas@example.org")
    for x in (up, other):
        assert client.delete(f"/api/datasets/{x['id']}").status_code == 204


def test_column_suggestion_coded_allele_names():
    """The coded / non-coded allele naming (A_coded, A_noncoded, AFreq_coded) maps to EA, OA and EAF; n_events (the
    number of cases, as survival analyses name it) to cases."""
    from gwashub_api.colmap import suggest
    m = suggest(["SNP", "CHR", "POS", "A_coded", "A_noncoded", "AFreq_coded", "BETA", "SE", "P", "n_events"])["mapping"]
    assert (m["ea"], m["oa"], m["eaf"], m["ncase"]) == ("A_coded", "A_noncoded", "AFreq_coded", "n_events")


def test_upload_runs_qc_and_enforces_dataset_permissions(client, synth_small):
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] in ("passed", "review") and d["run"]["status"] == "completed", d["run"]
    assert [s["state"] for s in d["run"]["steps"]].count("skipped") == 0
    assert client.get(f"/api/datasets/{up['id']}/files/qc").status_code == 200
    assert client.get(f"/api/datasets/{up['id']}/files/report").text.startswith("<!doctype html>")
    plots = next(s["dir"] for s in d["run"]["steps"] if s["key"] == "gwashub/plots")
    art = client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": f"{plots}/manhattan.png"})
    assert art.status_code == 200 and art.headers["content-type"] == "image/png"
    assert client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": "../../../gwashub.db"}).status_code == 404
    # Table previews: the header and the first 100 rows, gzipped or not; the full file by download.
    import gzip
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Dataset
    with SessionLocal() as db:
        run_dir = db.get(Dataset, up["id"]).runs[-1].run_dir
    big = "\n".join(["a\tb", *(f"{i}\t{i * 2}" for i in range(500))]) + "\n"
    with gzip.open(os.path.join(run_dir, plots, "big.tsv.gz"), "wt") as f:
        f.write(big)
    prev = client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": f"{plots}/big.tsv.gz", "preview": 1})
    assert prev.status_code == 200 and prev.text.splitlines()[0] == "a\tb" and len(prev.text.splitlines()) == 101
    whole = client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": f"{plots}/big.tsv.gz", "download": 1})
    assert gzip.decompress(whole.content).decode() == big

    as_user(client, "mei@example.org")  # another uploader
    rows = client.get("/api/projects/hermes3/datasets").json()
    assert [r["can_open"] for r in rows if r["id"] == up["id"]] == [False]
    assert client.get(f"/api/datasets/{up['id']}").status_code == 403
    assert client.get(f"/api/datasets/{up['id']}/files/raw").status_code == 403
    assert client.post(f"/api/datasets/{up['id']}/review", json={"decision": "approve"}).status_code == 403

    as_user(client, "ama@example.org")  # reviewer sees everything
    assert client.get(f"/api/datasets/{up['id']}").status_code == 200
    r = client.post(f"/api/datasets/{up['id']}/review", json={"decision": "reject", "note": "test"})
    assert r.json()["status"] == "failed" and r.json()["review"]["decision"] == "reject"
    r = client.post(f"/api/datasets/{up['id']}/review", json={"decision": "approve"})
    assert r.json()["status"] == "passed"


def test_broken_upload_is_blocked(client, synth_small):
    as_user(client, "jonas@example.org")
    entry = synth_small["manifest"][1]
    # A required column not mapped: refused at upload.
    unmapped = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls")}
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post("/api/projects/hermes3/datasets", files={"file": (entry["file"], f)},
                        data={"meta": json.dumps({**meta, "column_mapping": unmapped})})
    assert r.status_code == 422 and "se" in r.json()["detail"]
    # Mapped to a column QC can't use (SE from the rsID column): parse removes every row and the run is blocked.
    mapping = {**entry["column_mapping"], "se": entry["column_mapping"]["rsid"]}
    up = upload(client, synth_small, entry, column_mapping=mapping)
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] == "failed" and d["run"]["status"] == "blocked"
    assert client.get(f"/api/datasets/{up['id']}/files/qc").status_code == 409
    assert client.get(f"/api/datasets/{up['id']}/files/report").status_code == 200  # report still produced


def test_uploads_wait_as_pending_until_qc_is_started(client, synth_small):
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    a, b = (upload(client, synth_small, entry, start=False) for _ in range(2))
    d = client.get(f"/api/datasets/{a['id']}").json()
    assert d["status"] == "pending" and d["run"] is None
    assert client.get("/api/projects/hermes3").json()["counts"]["pending"] >= 2
    # Editing a pending dataset's build doesn't start QC.
    r = client.patch(f"/api/datasets/{a['id']}", json={"build": "GRCh38" if entry["build"] == "GRCh37" else "GRCh37"}).json()
    assert not r["rerun_started"] and not r["rerun_advised"]
    assert client.patch(f"/api/datasets/{a['id']}", json={"build": entry["build"]}).status_code == 200
    as_user(client, "mei@example.org")  # another uploader can't start it
    assert client.post(f"/api/datasets/{a['id']}/rerun").status_code == 403
    as_user(client, "jonas@example.org")  # the uploader can, once; re-running is for reviewers
    r = client.post("/api/projects/hermes3/datasets/rerun", json={"ids": [a["id"]]}).json()
    assert r["started"] == 1
    assert client.get(f"/api/datasets/{a['id']}").json()["status"] in ("passed", "review")
    assert client.post(f"/api/datasets/{a['id']}/rerun").status_code == 403
    # Bulk delete under the single-delete rules: the uploader removes their own; another uploader can't.
    as_user(client, "mei@example.org")
    assert client.post("/api/projects/hermes3/datasets/delete", json={"ids": [b["id"]]}).json()["deleted"] == 0
    as_user(client, "jonas@example.org")
    r = client.post("/api/projects/hermes3/datasets/delete", json={"ids": [a["id"], b["id"]]}).json()
    assert r == {"deleted": 2, "skipped": []}
    assert client.get(f"/api/datasets/{b['id']}").status_code == 404


def test_api_tokens_upload_only_in_their_project(client, synth_small):
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")  # an uploader in hermes3, not in hcm
    assert client.post("/api/tokens", json={"project_id": "hcm", "name": "x"}).status_code == 404
    r = client.post("/api/tokens", json={"project_id": "hermes3", "name": "UKB files"})
    assert r.status_code == 201
    token, tid = r.json()["token"], r.json()["id"]
    assert token.startswith("gwh_") and r.json()["state"] == "active"
    assert "token" not in client.get("/api/tokens").json()[0]  # shown once only
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    data = open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb").read()
    client.cookies.clear()  # the script has no session, only the token
    h = {"X-GWASHub-Token": token}
    # Uploading works, end to end, and the dataset waits as pending with the token recorded.
    up = client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": entry["file"], "file_size": len(data)}, headers=h)
    assert up.status_code == 201, up.text
    up = up.json()
    urls = client.post(f"/api/datasets/{up['id']}/upload/urls", json={"parts": list(range(1, up["parts"] + 1))}, headers=h).json()["urls"]
    from gwashub_api import storage
    parts = []
    for n in range(1, up["parts"] + 1):
        resp = client.put(urls[str(n)], content=data[(n - 1) * storage.PART_SIZE:n * storage.PART_SIZE])
        parts.append({"n": n, "etag": resp.headers["etag"]})
    done = client.post(f"/api/datasets/{up['id']}/upload/complete", json={"parts": parts}, headers=h)
    assert done.status_code == 200 and done.json()["status"] == "pending"
    # Everything else refuses the token: reading, starting QC, deleting, editing, other projects, making tokens.
    for method, url, body in [("GET", f"/api/datasets/{up['id']}", None), ("GET", "/api/projects/hermes3/datasets", None),
                              ("POST", f"/api/datasets/{up['id']}/rerun", None), ("DELETE", f"/api/datasets/{up['id']}", None),
                              ("PATCH", f"/api/datasets/{up['id']}", {"build": "GRCh38"}), ("GET", "/api/tokens", None),
                              ("POST", "/api/tokens", {"project_id": "hermes3", "name": "more"})]:
        assert client.request(method, url, json=body, headers=h).status_code == 401, (method, url)
    other = client.post("/api/projects/hcm/uploads", json={"meta": meta, "file_name": "x.tsv", "file_size": 10}, headers=h)
    assert other.status_code == 404
    assert client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": "x", "file_size": 1},
                       headers={"X-GWASHub-Token": "gwh_not-a-token"}).status_code == 401
    # The owner sees which token brought it in; revoking stops the token at once.
    as_user(client, "jonas@example.org")
    assert client.get(f"/api/datasets/{up['id']}").json()["uploaded_with_token"].startswith("UKB files (gwh_")
    assert client.delete(f"/api/tokens/{tid}").status_code == 204
    assert client.get("/api/tokens").json()[0]["state"] == "revoked"
    client.cookies.clear()
    assert client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": "x", "file_size": 1}, headers=h).status_code == 401
    # An expired token is refused too.
    from datetime import datetime, timedelta, timezone
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import ApiToken
    as_user(client, "jonas@example.org")
    r = client.post("/api/tokens", json={"project_id": "hermes3", "name": "old"}).json()
    with SessionLocal() as db:
        db.get(ApiToken, r["id"]).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    client.cookies.clear()
    assert client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": "x", "file_size": 1},
                       headers={"X-GWASHub-Token": r["token"]}).status_code == 401
    as_user(client, "jonas@example.org")
    assert client.delete(f"/api/datasets/{up['id']}").status_code == 204


def test_bulk_template_and_check(client, synth_small):
    """The manifest check reports every problem by row and creates nothing; duplicates and cohort contradictions fail."""
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    p = client.get("/api/projects/hermes3").json()
    tpl = client.get("/api/projects/hermes3/bulk/template")
    assert tpl.status_code == 200 and tpl.headers["content-type"].startswith("application/vnd.openxmlformats")
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(tpl.content))
    assert wb.sheetnames[:2] == ["README", "Manifest"]
    header_line = [c.value for c in wb["Manifest"][1]]
    assert header_line[:3] == ["file", "cohort", "phenotype"]
    assert {"col_effect", "col_ncase", "col_ncontrol", "col_info", "col_rsid", "col_log10p"} <= set(header_line)  # optional columns too
    readme = "\n".join(str(c.value) for row in wb["README"].iter_rows() for c in row if c.value)
    assert "relative to the folder" in readme and "case/control" in readme
    cols = {("col_effect" if k == "beta" else f"col_{k}"): v for k, v in entry["column_mapping"].items() if v}
    file_header = list(entry["column_mapping"].values())
    counts = {"n_cases": str(entry["n_cases"]), "n_controls": str(entry["n_controls"])}

    def row(n, **v):
        base = {"file": f"f{n}.tsv", "cohort": "BulkCo", "phenotype": entry["phenotype"], "ancestry": entry["ancestry"],
                "sex": p["sexes"][n % len(p["sexes"])], "build": entry["build"], "effect_type": {"or": "odds_ratio"}.get(entry["effect_type"], entry["effect_type"]),
                **counts, **cols, "meta_country": "UK", "meta_case_definition": "ICD-10 I50"}
        return {"row": n, "values": {**base, **v}, "header": file_header, "file_size": 1000 + n}

    def check(rows, columns=None, **extra):
        columns = columns or list(rows[0]["values"])
        r = client.post("/api/projects/hermes3/bulk/check", json={"columns": columns, "rows": rows, **extra})
        assert r.status_code == 200, r.text
        return r.json()

    good = check([row(2), row(3, n_total=str(entry["n_cases"] + entry["n_controls"]))])  # a matching total is fine
    assert good["ok"] and good["new_cohorts"] == ["BulkCo"], good
    up = good["uploads"]["2"]  # what the upload endpoint gets: QC's column names and effect type
    assert up["column_mapping"]["beta"] == entry["column_mapping"]["beta"] and up["effect_type"] == entry["effect_type"]
    assert "BulkCo" not in {c["name"] for c in client.get("/api/projects/hermes3/cohorts").json()}  # nothing was created

    # An existing dataset to clash with.
    existing = upload(client, synth_small, entry, start=False, cohort="Clash cohort")
    bad_rows = [
        row(2, col_effect="NOPE"),                                # header not in the file
        row(3, phenotype="XYZ"),                                  # not a project phenotype
        row(4, meta_country="France"),                            # disagrees with row 2 for the new cohort
        row(5, n_cases="12a"),                                    # not a whole number
        {**row(6), "header": None, "file_error": "not found"},    # the script couldn't read the file
        row(7, cohort="Clash cohort", sex=entry["sex"]),          # same combination as the existing dataset
        row(8, cohort="Clash cohort", sex=[x for x in p["sexes"] if x != entry["sex"]][0], meta_country="Atlantis"),  # differs from the cohort record
        row(9, sex=p["sexes"][2 % len(p["sexes"])]),              # same combination as row 2
        row(10, effect_type="beta or", n_total="1200"),            # not an effect type; n_total isn't cases + controls
    ]
    r = check(bad_rows, columns=list(bad_rows[0]["values"]) + ["meta_nope"])
    assert not r["ok"]
    msgs = {(x["row"], x["message"].split(":")[0].split(" (")[0]) for x in r["problems"]}
    text = "\n".join(f"{x['row']}: {x['message']}" for x in r["problems"])
    assert any(x["row"] is None and "meta_nope" in x["message"] for x in r["problems"]), text
    assert "10: effect_type must be beta or odds_ratio, not 'beta or'" in text and "10: n_total (1,200) isn't n_cases + n_controls" in text
    assert "col_effect: the file has no column 'NOPE'" in text and "Phenotype 'XYZ'" in text and "n_cases must be a whole number" in text
    assert "6: File f6.tsv: not found" in text
    assert "4: meta_country (" in text and "differs from row 2 for new cohort BulkCo" in text
    assert f"7: Matches dataset {existing['id']}" in text and "delete it on the site first" in text
    assert "differs from cohort Clash cohort's record (empty)" in text and "\n8: meta_country" in text
    assert "Give the number of cases" not in text  # the bad count is reported once
    assert "9: The same cohort, phenotype, ancestry and sex as row 2" in text
    assert msgs
    # A resumed upload: the manifest's own datasets aren't duplicates of themselves.
    assert check([row(7, cohort="Clash cohort", sex=entry["sex"], meta_country=None)], own_ids=[existing["id"]])["ok"]
    # With a token too; not in another project.
    token = client.post("/api/tokens", json={"project_id": "hermes3", "name": "bulk"}).json()["token"]
    client.cookies.clear()
    h = {"X-GWASHub-Token": token}
    assert client.post("/api/projects/hermes3/bulk/check", json={"columns": list(row(2)["values"]), "rows": [row(2)]}, headers=h).json()["ok"]
    assert client.get("/api/projects/hcm/bulk/template", headers=h).status_code == 404
    st = client.get("/api/projects/hermes3/bulk/status", params={"ids": f"{existing['id']},999999"}, headers=h).json()
    assert st[0]["status"] == "pending" and st[1]["status"] == "not found"
    as_user(client, "jonas@example.org")
    assert client.delete(f"/api/datasets/{existing['id']}").status_code == 204


def test_upload_script_download_and_its_rows_pass_the_check(client, synth_small, tmp_path):
    """The script the site hands out has the site's address in it, and what it sends is what the check expects."""
    import importlib.util
    client.cookies.clear()
    assert client.get("/api/bulk/script").status_code == 401
    as_user(client, "jonas@example.org")
    r = client.get("/api/bulk/script")
    assert r.status_code == 200 and 'SERVER = "http' in r.text and "attachment" in r.headers["content-disposition"]
    spec = importlib.util.spec_from_file_location("gwashub_upload", os.path.join(os.path.dirname(__file__), "..", "..", "client", "gwashub_upload.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    entry = synth_small["manifest"][0]
    src = os.path.join(synth_small["dir"], "fake_gwas", entry["file"])
    os.symlink(src, tmp_path / "renamed.tsv.gz")
    # Fill in the real template as someone would in Excel: typed numbers and a date cell.
    import base64
    import io
    from datetime import date
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(client.get("/api/projects/hermes3/bulk/template").content))
    sheet = wb["Manifest"]
    header = [c.value for c in sheet[1]]
    cols = {("col_effect" if k == "beta" else f"col_{k}"): v for k, v in entry["column_mapping"].items() if v}
    values = {"file": "renamed.tsv.gz", "cohort": "Script cohort", "phenotype": entry["phenotype"], "ancestry": entry["ancestry"],
              "sex": entry["sex"], "build": entry["build"], "effect_type": {"or": "odds_ratio"}.get(entry["effect_type"], entry["effect_type"]),
              "n_cases": entry["n_cases"], "n_controls": entry["n_controls"], **cols}
    if "meta_data_collection_start" in header:
        values["meta_data_collection_start"] = date(2020, 1, 31)
    for k, v in values.items():
        sheet.cell(5, header.index(k) + 1, v)  # row 5: rows 2-4 left empty are skipped
    path = tmp_path / "manifest.xlsx"
    wb.save(path)
    parsed = client.post("/api/projects/hermes3/bulk/parse", json={"content": base64.b64encode(path.read_bytes()).decode()}).json()
    assert [x["row"] for x in parsed["rows"]] == [5]
    got = parsed["rows"][0]["values"]
    assert got["n_cases"] == str(entry["n_cases"]) and got.get("meta_data_collection_start", "2020-01-31") == "2020-01-31"
    rows = [(x["row"], x["values"]) for x in parsed["rows"]]
    r = client.post("/api/projects/hermes3/bulk/check", json={"columns": parsed["columns"], "rows": g.check_rows(str(path), rows)})
    assert r.json()["ok"], r.json()
    bad = client.post("/api/projects/hermes3/bulk/parse", json={"content": base64.b64encode(b"file\tcohort\n").decode()})
    assert bad.status_code == 422 and "xlsx" in bad.json()["detail"]


def test_practice_project_for_everyone(client):
    """Every user is a reviewer in the Practice project, sees only themselves in its members, and can't change them."""
    as_user(client, "noor@example.org")  # an uploader elsewhere
    pr = next(p for p in client.get("/api/projects").json() if p["is_practice"])
    assert pr["id"] == "practice" and pr["role"] == "reviewer"
    p = client.get("/api/projects/practice").json()
    assert p["is_practice"] and [m["name"] for m in p["members"]] == [next(m["name"] for m in p["members"] if m["is_me"])]
    assert client.post("/api/projects/practice/members", json={"email": "x@y.org", "name": "X", "role": "uploader"}).status_code == 409
    files = client.get("/api/practice/files").json()
    assert len(files) == 3 and all(f["phenotype"] in p["phenotypes"] for f in files)
    r = client.get(f"/api/practice/files/{files[0]['file']}")
    assert r.status_code == 200 and r.content[:2] == b"\x1f\x8b"
    assert client.get("/api/practice/files/..%2Fgwashub.db").status_code == 404
    client.cookies.clear()
    assert client.get("/api/practice/files").status_code == 401


def test_summary_counts(client):
    as_user(client, "mei@example.org")
    s = client.get("/api/projects/hermes3/summary", params={"by": "ancestry"}).json()
    assert s["totals"]["total"] == 2 and s["totals"]["failed"] == 1 and s["totals"]["passed"] == 1


def test_sample_sizes(client):
    """Cases, controls and N summed by ancestry and sex: every dataset, or with exclude_failed those that didn't fail QC."""
    as_user(client, "mei@example.org")
    every = [d for d in client.get("/api/projects/hermes3/datasets").json() if d["status"] != "uploading"]
    for exclude, ds in ((False, every), (True, [d for d in every if d["status"] != "failed"])):
        s = client.get("/api/projects/hermes3/sample-sizes", params={"exclude_failed": exclude}).json()
        assert s["fields"][-2:] == ["ancestry", "sex"]
        assert sum(r["datasets"] for r in s["rows"]) == len(ds)
        assert sum(r["n_total"] for r in s["rows"]) == sum(d["n_total"] or 0 for d in ds)
        assert all(r["n_cases"] + r["n_controls"] == r["n_total"] for r in s["rows"] if r["n_cases"] is not None)
    assert any(d["status"] == "failed" for d in every)  # so the two differ


def test_pipeline_permissions_and_versions(client):
    as_user(client, "jonas@example.org")
    p = client.get("/api/projects/hermes3/pipeline").json()
    assert p["version"] == 1 and p["role"] == "uploader"
    assert any(c["key"] == "gwashub/filter" for c in p["catalog"])
    assert {r["key"] for r in p["resources"]} >= {"hg19ToHg38.over.chain.gz", "dbSNP157_b38_clean.vcf.gz"}

    def with_filter(params):
        return [p["steps"][0], {"step": "gwashub/filter", "params": params}, *p["steps"][2:]]
    maf5 = {"maf": {"on": True, "min_on": True, "min": 0.05, "max_on": False, "max": 0.5}}
    steps = with_filter(maf5)
    assert client.put("/api/projects/hermes3/pipeline", json={"steps": steps}).status_code == 403
    as_user(client, "ama@example.org")
    assert client.put("/api/projects/hermes3/pipeline", json={"steps": [{"step": "gwashub/nope"}]}).status_code == 422
    maf = next(f for c in p["catalog"] if c["key"] == "gwashub/filter" for f in c["param_schema"] if f["key"] == "maf")
    assert maf["type"] == "range" and maf["max"] == 0.5 and maf["sides"] == ["min", "max"]
    for bad in ({"maf": {"on": True, "min_on": True, "min": 0.9}}, {"maf": "x"}, {"min_maf": 0.05}):  # out of range, wrong type, unknown
        r = client.put("/api/projects/hermes3/pipeline", json={"steps": with_filter(bad)})
        assert r.status_code == 422, bad
    r =client.put("/api/projects/hermes3/pipeline", json={"steps": steps, "note": "MAF 5%"})
    assert r.json()["version"] == 2
    assert client.get("/api/projects/hermes3/pipeline").json()["history"][0]["note"] == "MAF 5%"


def test_rerun_uses_new_pipeline_version(client):
    as_user(client, "ama@example.org")
    ds = client.get("/api/projects/hermes3/datasets").json()
    ok = next(d for d in ds if d["status"] == "passed")
    assert client.post(f"/api/datasets/{ok['id']}/rerun").status_code == 200
    d = client.get(f"/api/datasets/{ok['id']}").json()
    assert d["run"]["pipeline_version"] == 2
    assert next(s for s in d["run"]["steps"] if s["key"] == "gwashub/filter")["state"] in ("pass", "warn")
    as_user(client, "jonas@example.org")
    assert client.post(f"/api/datasets/{ok['id']}/rerun").status_code == 403


def test_named_pipelines_pinning_and_out_of_date(client, synth_small):
    as_user(client, "jonas@example.org")  # uploaders can't manage pipelines or pin datasets
    one, two = (upload(client, synth_small, synth_small["manifest"][i]) for i in (0, 1))
    assert client.post("/api/projects/hermes3/pipelines", json={"name": "Strict"}).status_code == 403
    as_user(client, "ama@example.org")
    pl = client.get("/api/projects/hermes3/pipeline").json()
    default = next(p for p in pl["pipelines"] if p["is_default"])
    assert default["name"] == "Default"
    r = client.post("/api/projects/hermes3/pipelines", json={"name": "Strict", "copy_from": default["id"]})
    assert r.status_code == 201
    strict = r.json()["id"]
    assert client.post("/api/projects/hermes3/pipelines", json={"name": "strict"}).status_code == 409
    # The copy starts with the default's current steps, as its own version (numbers are unique in the project).
    sp = client.get("/api/projects/hermes3/pipeline", params={"pipeline_id": strict}).json()
    assert sp["steps"] == pl["steps"]
    assert sp["version"] > pl["version"]
    assert client.patch(f"/api/pipelines/{strict}", json={"name": "Strict QC"}).json()["name"] == "Strict QC"
    assert client.patch(f"/api/pipelines/{default['id']}", json={"is_default": False}).status_code == 422

    one, two = (client.get(f"/api/datasets/{d['id']}").json() for d in (one, two))
    assert not one["pipeline"]["out_of_date"] and not one["pipeline"]["pinned"]
    # Pinning one dataset: re-run advised, not started; it becomes out of date until re-run.
    r = client.patch(f"/api/datasets/{one['id']}", json={"pipeline_id": strict}).json()
    assert r["rerun_advised"] and not r["rerun_started"] and r["changed"] == ["pipeline"]
    st = client.get(f"/api/datasets/{one['id']}").json()["pipeline"]
    assert st["pinned"] and st["pipeline_name"] == "Strict QC" and st["out_of_date"]
    view = {p["id"]: p for p in client.get("/api/projects/hermes3/pipeline").json()["pipelines"]}
    assert view[strict]["out_of_date_ids"] == [one["id"]] and view[strict]["pinned"] == 1
    # A re-run uses the pinned pipeline and clears the flag.
    assert client.post(f"/api/datasets/{one['id']}/rerun").status_code == 200
    d = client.get(f"/api/datasets/{one['id']}").json()
    assert d["run"]["pipeline_version"] == sp["version"] and not d["pipeline"]["out_of_date"]
    # Bulk: pin two, then send both back to the default (0); only the one whose run used Strict is out of date.
    as_user(client, "jonas@example.org")
    assert client.post("/api/projects/hermes3/datasets/pipeline", json={"ids": [two["id"]], "pipeline_id": strict}).status_code == 403
    as_user(client, "ama@example.org")
    r = client.post("/api/projects/hermes3/datasets/pipeline", json={"ids": [one["id"], two["id"]], "pipeline_id": strict}).json()
    assert r["changed"] == 1 and r["out_of_date"] == [two["id"]]
    r = client.post("/api/projects/hermes3/datasets/pipeline", json={"ids": [one["id"], two["id"]], "pipeline_id": 0}).json()
    assert r["changed"] == 2 and r["out_of_date"] == [one["id"]]
    edits = client.get(f"/api/datasets/{two['id']}").json()["edits"]
    assert edits[-1]["changes"]["pipeline"] == ["Strict QC", "project default"]
    # Making Strict the default moves every unpinned dataset to it (so they're out of date); switch back afterwards.
    assert client.patch(f"/api/pipelines/{strict}", json={"is_default": True}).status_code == 200
    st = client.get(f"/api/datasets/{two['id']}").json()["pipeline"]
    assert st["pipeline_name"] == "Strict QC" and not st["pinned"] and st["out_of_date"]
    assert client.patch(f"/api/pipelines/{default['id']}", json={"is_default": True}).status_code == 200
    assert client.get(f"/api/datasets/{two['id']}").json()["pipeline"]["out_of_date"] is False
    for d in (one, two):  # leave the project's datasets as they were for the tests after
        assert client.delete(f"/api/datasets/{d['id']}").status_code in (200, 204)


def test_code_viewer_files(client):
    as_user(client, "jonas@example.org")
    r = client.get("/api/projects/hermes3/steps/files", params={"key": "gwashub/harmonise_liftover"}).json()
    assert r["files"][0]["path"] == "pipeline/qc_steps/gwashub/harmonise_liftover/harmonise_liftover.py"
    paths = {f["path"] for f in r["files"]}
    assert {"pipeline/qc_steps/gwashub/harmonise_liftover/gwas2vcf/main.py", "pipeline/qc_steps/_lib/common.py"} <= paths
    r = client.get("/api/projects/hermes3/steps/files", params={"key": "gwashub/parse"}).json()
    assert r["files"][0]["path"] == "pipeline/qc_steps/gwashub/parse/parse.R" and "data.table" in r["files"][0]["content"]


def test_pipeline_lists_consortium_steps_and_images(client):
    as_user(client, "tom@example.org")
    pl = client.get("/api/projects/hcm/pipeline").json()
    step = next(c for c in pl["catalog"] if c["key"] == "custom_example/hello_world")
    assert step["owner"] == "custom_example" and step["source"] == "consortium" and step["image"] == "gwashub/base"
    assert {"gwashub/base", "gwashub/base-r", "gwashub/vcf", "gwashub/ldsc"} <= {i["ref"] for i in pl["images"]}
    # The default pipeline is one job per change of image.
    assert [g["image"] for g in pl["groups"]] == ["gwashub/base-r", "gwashub/vcf", "gwashub/ldsc", "gwashub/base"]
    assert client.post("/api/projects/hcm/custom-steps", json={}).status_code in (404, 405)  # git-repo steps are gone
    # A step runs in its own image (step.yaml): an image in the saved steps is dropped.
    r = client.put("/api/projects/hcm/pipeline", json={"steps": [*pl["steps"][:-1], {**pl["steps"][-1], "image": "nobody/nothing"}]})
    assert r.status_code == 200 and "image" not in client.get("/api/projects/hcm/pipeline").json()["steps"][-1]


def test_resources_upload(client, tmp_path):
    f = tmp_path / "exclude.bed"
    f.write_text("chr6\t28000000\t34000000\tMHC\n")
    meta = {"key": "exclude.bed", "title": "Exclusions", "build": "GRCh38", "attest": True}
    for who in ("jonas@example.org", "ama@example.org"):  # uploaders and reviewers can't add resources
        as_user(client, who)
        assert client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)}).status_code == 403
    as_user(client, "demo@gwashub.org")  # platform admin
    assert client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))},
                       data={"meta": json.dumps({**meta, "attest": False})}).status_code == 422
    r1 = client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)})
    r2 = client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)})
    assert (r1.json()["version"], r2.json()["version"]) == (1, 2)
    shared = client.post("/api/projects/hermes3/resources", files={"file": ("x", f.open("rb"))},
                         data={"meta": json.dumps({**meta, "key": "all_afreq_b38.tsv.gz"})})
    assert shared.status_code == 409


def _upload_resource_in_parts(client, body, data, part_size):
    r = client.post("/api/resources/uploads", json={**body, "file_size": len(data)})
    assert r.status_code == 201, r.text
    t = r.json()
    urls = client.post("/api/resources/uploads/urls", json={"ticket": t["ticket"], "parts": list(range(1, t["parts"] + 1))}).json()["urls"]
    parts = []
    for n in range(1, t["parts"] + 1):
        resp = client.put(urls[str(n)], content=data[(n - 1) * part_size:n * part_size])
        assert resp.status_code == 200
        parts.append({"n": n, "etag": resp.headers["etag"]})
    return client.post("/api/resources/uploads/complete", json={"ticket": t["ticket"], "parts": parts})


def test_admin_uploads_shared_and_project_resources_in_parts(client, monkeypatch):
    from gwashub_api import storage
    monkeypatch.setattr(storage, "PART_SIZE", 1000)
    data = b"CHR\tBP\tSNP\tL2\n" + b"1\t100\trs1\t2.5\n" * 300
    body = {"scope": "shared", "key": "ldscores_EUR.tsv", "title": "LD scores (EUR)", "build": "GRCh38", "attest": True}
    as_user(client, "ama@example.org")  # a reviewer can't
    assert client.post("/api/resources/uploads", json={**body, "file_size": 10}).status_code == 403
    as_user(client, "demo@gwashub.org")
    assert client.post("/api/resources/uploads", json={**body, "key": "../x", "file_size": 10}).status_code == 422
    r = _upload_resource_in_parts(client, body, data, 1000)
    assert r.status_code == 201 and r.json()["scope"] == "shared" and r.json()["size"] == len(data) and r.json()["version"] == 1
    # Every project now sees it; a project can't reuse the shared name.
    pl = client.get("/api/projects/hermes3/pipeline").json()
    assert any(x["key"] == "ldscores_EUR.tsv" and x["scope"] == "shared" for x in pl["resources"])
    assert client.post("/api/resources/uploads", json={**body, "scope": "hermes3", "file_size": 10}).status_code == 409
    r = _upload_resource_in_parts(client, {**body, "scope": "hermes3", "key": "regions.bed", "title": "Regions"}, b"chr1\t1\t2\n" * 50, 1000)
    assert r.status_code == 201 and r.json()["scope"] == "project"
    bad = client.post("/api/resources/uploads/complete", json={"ticket": "forged.abc", "parts": []})
    assert bad.status_code == 403


def test_meta_analysis(client, synth_small):
    as_user(client, "mei@example.org")
    upload(client, synth_small, synth_small["manifest"][1])
    upload(client, synth_small, synth_small["manifest"][2])
    cands = client.get("/api/projects/hermes3/meta/candidates").json()
    eligible = [c["id"] for c in cands if c["eligible"] and c["status"] in ("passed", "review")]
    assert len(eligible) >= 3
    assert client.post("/api/projects/hermes3/meta", json={"name": "x", "dataset_ids": eligible}).status_code == 403

    as_user(client, "ama@example.org")
    for c in cands:  # approve anything in review so default settings (passed only) accept it
        if c["status"] == "review":
            client.post(f"/api/datasets/{c['id']}/review", json={"decision": "approve"})
    assert client.post("/api/projects/hermes3/meta", json={"name": "x", "dataset_ids": eligible[:1]}).status_code == 422
    failed = next(c["id"] for c in cands if c["status"] == "failed")
    assert client.post("/api/projects/hermes3/meta", json={"name": "x", "dataset_ids": eligible + [failed]}).status_code == 422
    r = client.post("/api/projects/hermes3/meta", json={"name": "HF all", "dataset_ids": eligible,
                                                         "settings": {"params": {"model": "random", "genomic_control_output": True}}})
    assert r.status_code == 201
    ma = client.get(f"/api/meta/{r.json()['id']}").json()
    assert ma["status"] == "completed", ma["error"]
    assert re.search(rf"/meta/hermes3/{ma['id']}-\d{{8}}-\d{{6}}$", ma["location"]), ma["location"]
    assert ma["summary"]["n_datasets"] == len(eligible) and ma["summary"]["n_loci"] >= 3
    assert ma["lead_loci"] and ma["forest"] and ma["plot_data"]["manhattan"]["x"]
    assert ma["settings"]["method"] == "gwashub/gwama" and ma["settings"]["params"]["model"] == "random"  # the default method
    assert ma["method"]["title"] == "GWAMA" and ma["settings"]["common"]["gws"] == 5e-8
    assert client.get(f"/api/meta/{ma['id']}/files/meta_results.tsv.gz").status_code == 200
    assert set(ma["files"]) == {"README.txt", "datasets.tsv", "lead_loci.tsv", "signals.tsv", "meta_results.tsv.gz"}
    # GWAMA's own files are listed and downloadable by every member; nothing else from the run folder is.
    gwama_files = {f["path"] for f in ma["method_files"]}
    assert {"gwama_console.txt", "gwama_log.txt"} <= gwama_files
    assert "--genomic_control_output" in client.get(f"/api/meta/{ma['id']}/files/gwama_console.txt").text
    assert client.get(f"/api/meta/{ma['id']}/files/inputs.json").status_code == 404
    ds_tsv = client.get(f"/api/meta/{ma['id']}/files/datasets.tsv")
    assert ds_tsv.status_code == 200 and ds_tsv.text.startswith("position\tdataset_id")
    assert "Effect allele" in client.get(f"/api/meta/{ma['id']}/files/README.txt").text

    as_user(client, "jonas@example.org")
    view = client.get(f"/api/meta/{ma['id']}")
    assert view.status_code == 200 and view.json()["lead_loci"] is not None  # uploaders see the results on the page...
    for name in ("manhattan.png", "lead_loci.tsv", "signals.tsv", "datasets.tsv", "README.txt", "meta_results.tsv.gz",
                 "gwama_log.txt", "gwama_console.txt"):  # ...but download none of the files
        assert client.get(f"/api/meta/{ma['id']}/files/{name}").status_code == 403, name
    if view.json()["summary"].get("n_region_files"):  # except the locus plots' data, which the page reads to draw them
        locus = view.json()["forest"][0]["locus_id"]
        assert client.get(f"/api/meta/{ma['id']}/files/region_{locus}.json").status_code == 200
    assert client.put("/api/projects/hermes3/meta/settings", json={"method": "gwashub/gwama"}).status_code == 403
    as_user(client, "ama@example.org")
    s = client.put("/api/projects/hermes3/meta/settings", json={"method": "gwashub/metal", "params": {"gwashub/metal": {"scheme": "samplesize"}},
                                                                 "common": {"merge_kb": 200}}).json()["settings"]
    assert s["method"] == "gwashub/metal" and s["params"]["gwashub/metal"]["scheme"] == "samplesize" and s["common"]["merge_kb"] == 200
    assert client.put("/api/projects/hermes3/meta/settings", json={"method": "nobody/bayes"}).status_code == 422
    bad = client.put("/api/projects/hermes3/meta/settings", json={"method": "gwashub/gwama", "params": {"gwashub/gwama": {"model": "bayes"}}})
    assert bad.status_code == 422 and "Model" in bad.json()["detail"]
    assert client.put("/api/projects/hermes3/meta/settings", json={"method": "gwashub/gwama", "params": {}, "common": {}}).status_code == 200

    # Every method is offered with typed parameters; the example consortium method runs end to end.
    ms = client.get("/api/meta/methods").json()
    assert ms["default_method"] == "gwashub/gwama" and ms["methods"][0]["key"] == "gwashub/gwama"
    assert {m["key"] for m in ms["methods"]} == {"gwashub/gwama", "gwashub/metal", "custom_example/custom_meta_analysis"}
    r = client.post("/api/projects/hermes3/meta", json={"name": "Z-score", "dataset_ids": eligible, "settings": {
        "method": "custom_example/custom_meta_analysis", "params": {"weighting": "equal"}}})
    assert r.status_code == 201, r.text
    z = client.get(f"/api/meta/{r.json()['id']}").json()
    assert z["status"] == "completed", z["error"]
    assert z["settings"]["method"] == "custom_example/custom_meta_analysis" and z["settings"]["params"]["weighting"] == "equal"
    assert z["summary"]["n_loci"] >= 3 and z["forest"] and z["method"]["title"] == "Weighted Z-score (example)"
    assert client.post("/api/projects/hermes3/meta", json={"name": "bad", "dataset_ids": eligible, "settings": {
        "method": "custom_example/custom_meta_analysis", "params": {"weighting": "cubic"}}}).status_code == 422


def test_upload_options_are_enforced(client, synth_small):
    as_user(client, "jonas@example.org")
    body = {"phenotypes": ["All-cause HF"], "ancestries": ["EUR"], "sexes": ["Female", "Male"]}
    assert client.put("/api/projects/hermes3/upload-options", json=body).status_code == 403
    as_user(client, "ama@example.org")
    assert client.put("/api/projects/hermes3/upload-options", json={**body, "ancestries": ["MARS"]}).status_code == 422
    assert client.put("/api/projects/hermes3/upload-options", json={**body, "phenotypes": [" "]}).status_code == 422
    r = client.put("/api/projects/hermes3/upload-options", json={**body, "phenotypes": ["All-cause HF", "All-cause HF", "HFpEF"]}).json()
    assert r["phenotypes"] == ["All-cause HF", "HFpEF"] and r["sexes"] == ["Female", "Male"]
    p = client.get("/api/projects/hermes3").json()
    assert (p["ancestries"], p["sexes"]) == (["EUR"], ["Female", "Male"])
    entry = synth_small["manifest"][0]  # EUR, Combined
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post("/api/projects/hermes3/datasets", files={"file": (entry["file"], f)}, data={"meta": json.dumps(meta)})
    assert r.status_code == 422 and "Sex stratum" in r.json()["detail"]
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post("/api/projects/hermes3/datasets", files={"file": (entry["file"], f)},
                        data={"meta": json.dumps({**meta, "sex": "Male", "phenotype": "HFrEF"})})
    assert r.status_code == 422 and "Phenotype" in r.json()["detail"]
    # restore for other tests
    client.put("/api/projects/hermes3/upload-options", json={"phenotypes": ["All-cause HF", "HFrEF", "HFpEF"],
                                                             "ancestries": ["EUR", "AFR", "EAS", "SAS", "AMR", "Multi"], "sexes": ["Combined", "Female", "Male"]})


def test_study_metadata_upload_edit_and_audit(client, synth_small):
    from gwashub_api.metadata_schema import validate
    import pytest as _pt
    from fastapi import HTTPException
    with _pt.raises(HTTPException) as e:
        validate({"male_prop_total": 1.4, "data_collection_start": "2020-01-01", "data_collection_end": "2019-01-01", "nope": 1})
    assert "Proportion male" in e.value.detail and "before the start" in e.value.detail and "unknown field" in e.value.detail
    assert validate({"case_type": "Incident", "n_variants_for_imputation": "40000", "contact_name": " "}) == {"case_type": "Incident", "n_variants_for_imputation": 40000}
    # Decimal fields take scientific notation, as text or as a number; text that isn't a number is named.
    assert validate({"hwe_p": "1e-6", "maf": 0.01}) == {"hwe_p": 1e-6, "maf": 0.01}
    with _pt.raises(HTTPException) as e:
        validate({"hwe_p": "one in a million"})
    assert "HWE P threshold: must be a number" in e.value.detail
    # The contact is two fields: a name and an email address, checked as one.
    assert validate({"contact_name": " Jane Smith ", "contact_email": " jane.smith@bristol.ac.uk "}) == \
        {"contact_name": "Jane Smith", "contact_email": "jane.smith@bristol.ac.uk"}
    for bad in ("Jane Smith", "jane@", "jane smith@bristol.ac.uk", "Jane <jane@bristol.ac.uk>"):
        with _pt.raises(HTTPException) as e:
            validate({"contact_email": bad})
        assert "Contact email: must be an email address" in e.value.detail

    as_user(client, "jonas@example.org")
    assert len(client.get("/api/metadata-schema").json()["sections"]) == 7
    meta = {"case_ascertainment": "Research study", "imputation_reference": "HRC r1.1", "age_mean_total": 56.2}
    up = upload(client, synth_small, synth_small["manifest"][2], study_meta=meta)
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["study_meta"] == meta and d["can_edit"] and d["metadata_complete"]["filled"] == 3
    r = client.patch(f"/api/datasets/{up['id']}", json={"study_meta": {**meta, "case_type": "Incident", "age_mean_total": None}, "n_cases": 4200})
    assert r.status_code == 200 and r.json()["rerun_advised"]
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["n_cases"] == 4200 and "age_mean_total" not in d["study_meta"] and d["study_meta"]["case_type"] == "Incident"
    assert d["edits"][-1]["by"] == "Jonas Lindqvist" and d["edits"][-1]["changes"]["n_cases"][1] == 4200
    assert client.patch(f"/api/datasets/{up['id']}", json={"study_meta": {"case_type": "Sometimes"}}).status_code == 422
    assert client.patch(f"/api/datasets/{up['id']}", json={"phenotype": "Not a phenotype"}).status_code == 422

    as_user(client, "mei@example.org")  # another uploader: can't open, can't edit
    assert client.patch(f"/api/datasets/{up['id']}", json={"cohort": "x"}).status_code == 403
    as_user(client, "ama@example.org")  # reviewer can edit
    assert client.patch(f"/api/datasets/{up['id']}", json={"cohort": "BBJ (v2)"}).json()["changed"] == ["cohort"]


def test_cognito_mode_requires_a_real_secret_and_hides_dev_login(client, monkeypatch):
    from gwashub_api.config import Settings, settings
    monkeypatch.setenv("GWASHUB_AUTH", "cognito")
    for k, v in {"GWASHUB_COGNITO_DOMAIN": "https://x.auth.eu-west-2.amazoncognito.com", "GWASHUB_COGNITO_USER_POOL_ID": "eu-west-2_X",
                 "GWASHUB_COGNITO_CLIENT_ID": "c", "GWASHUB_COGNITO_CLIENT_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GWASHUB_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        Settings()
    monkeypatch.setenv("GWASHUB_SECRET", "x" * 40)
    assert Settings().auth_mode == "cognito"
    monkeypatch.setenv("GWASHUB_AUTH", "password")  # the interim server's mode is gone
    with pytest.raises(RuntimeError):
        Settings()
    monkeypatch.setattr(settings(), "auth_mode", "cognito")
    client.cookies.clear()
    assert client.get("/api/auth/users").status_code == 404
    assert client.post("/api/auth/login", json={"email": "jonas@example.org"}).status_code == 404


def test_change_build_reruns_qc(client, synth_small):
    as_user(client, "jonas@example.org")
    entry = synth_small["manifest"][0]
    up = upload(client, synth_small, entry)
    other = "GRCh38" if entry["build"] == "GRCh37" else "GRCh37"
    assert client.patch(f"/api/datasets/{up['id']}", json={"build": "hg19"}).status_code == 422
    r = client.patch(f"/api/datasets/{up['id']}", json={"build": other}).json()
    assert r["changed"] == ["build"] and r["rerun_started"] and not r["rerun_advised"]
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["build"] == other and len(d["runs"]) == 2 and d["edits"][-1]["changes"]["build"] == [entry["build"], other]


def test_delete_dataset(client, synth_small):
    from gwashub_api.config import settings
    as_user(client, "jonas@example.org")
    mine = upload(client, synth_small, synth_small["manifest"][0])
    d = client.get(f"/api/datasets/{mine['id']}").json()
    # Folders are named by project, dataset, run and time, and shown on the page for help requests.
    run_dir, upload_dir = d["run"]["location"], d["upload_location"]
    assert re.fullmatch(re.escape(os.path.join(settings().data_dir, "runs", "qc", "hermes3", str(mine["id"]), f"{d['run']['id']}-"))
                        + r"\d{8}-\d{6}", run_dir), run_dir
    assert re.fullmatch(re.escape(os.path.join(settings().storage_dir, "uploads", "hermes3", f"{mine['id']}-")) + r"\d{8}-\d{6}",
                        upload_dir), upload_dir
    assert os.path.isdir(run_dir) and os.path.isdir(upload_dir)

    as_user(client, "mei@example.org")  # another uploader can't
    assert client.delete(f"/api/datasets/{mine['id']}").status_code == 403

    as_user(client, "jonas@example.org")  # the uploader can
    assert client.delete(f"/api/datasets/{mine['id']}").status_code == 204
    assert client.get(f"/api/datasets/{mine['id']}").status_code == 404
    assert not os.path.exists(run_dir) and not os.path.exists(upload_dir)
    assert mine["id"] not in [r["id"] for r in client.get("/api/projects/hermes3/datasets").json()]

    theirs = upload(client, synth_small, synth_small["manifest"][0])
    as_user(client, "ama@example.org")  # a reviewer can delete anyone's
    assert client.delete(f"/api/datasets/{theirs['id']}").status_code == 204


def test_upload_in_parts(client, synth_small, monkeypatch):
    """The website's upload path: start, part URLs, PUT parts, complete (local storage stands in for S3)."""
    from gwashub_api import storage
    monkeypatch.setattr(storage, "PART_SIZE", 50_000)
    entry = synth_small["manifest"][0]
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        data = f.read()
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    as_user(client, "jonas@example.org")
    r = client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": "../" + entry["file"], "file_size": len(data)})
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["parts"] == -(-len(data) // 50_000) >= 2
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] == "uploading" and d["file_name"] == entry["file"]

    as_user(client, "mei@example.org")  # unfinished uploads are invisible to others
    assert up["id"] not in [x["id"] for x in client.get("/api/projects/hermes3/datasets").json()]
    as_user(client, "ama@example.org")  # reviewers can open it but not send parts
    assert client.post(f"/api/datasets/{up['id']}/upload/urls", json={"parts": [1]}).status_code == 403

    as_user(client, "jonas@example.org")
    urls = client.post(f"/api/datasets/{up['id']}/upload/urls", json={"parts": list(range(1, up["parts"] + 1))}).json()["urls"]
    bad = urls["1"].replace("token=", "token=0")
    assert client.put(bad, content=b"x").status_code == 403
    parts = []
    for n in range(1, up["parts"] + 1):
        chunk = data[(n - 1) * 50_000:n * 50_000]
        r = client.put(urls[str(n)], content=chunk)
        assert r.status_code == 200
        parts.append({"n": n, "etag": r.headers["etag"]})
    assert client.post(f"/api/datasets/{up['id']}/upload/complete", json={"parts": parts[:-1]}).status_code == 422
    r = client.post(f"/api/datasets/{up['id']}/upload/complete", json={"parts": parts})
    assert r.status_code == 200 and r.json()["status"] == "pending", r.text  # the website's uploads wait for Start QC too
    assert client.post(f"/api/datasets/{up['id']}/rerun").status_code == 200
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] in ("passed", "review") and d["run"]["status"] == "completed", d["run"]
    assert client.get(f"/api/datasets/{up['id']}/files/raw").content == data
    assert client.post(f"/api/datasets/{up['id']}/upload/complete", json={"parts": parts}).status_code == 409


def test_changes_requested_and_replacing_the_file(client, synth_small, monkeypatch):
    """A reviewer requests changes: the dataset gets its own status (kept out of meta-analysis), the uploader sees a
    badge count and can re-run QC or upload a corrected file. The corrected file must have the mapped columns; it
    replaces the old one, which is deleted, and QC starts."""
    import gzip as gz
    from gwashub_api import storage
    monkeypatch.setattr(storage, "PART_SIZE", 50_000)
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, entry)
    as_user(client, "ama@example.org")
    r = client.post(f"/api/datasets/{up['id']}/review", json={"decision": "request_changes", "note": "Wrong build?"})
    assert r.json()["status"] == "changes_requested" and r.json()["review"]["note"] == "Wrong build?"
    as_user(client, "jonas@example.org")
    assert client.get("/api/projects/hermes3").json()["my_changes_requested"] >= 1
    assert client.get("/api/projects/hermes3/summary").json()["totals"]["changes_requested"] >= 1
    old = client.get(f"/api/datasets/{up['id']}").json()["upload_location"]

    def replace(data: bytes, name: str):
        start = client.post(f"/api/datasets/{up['id']}/replacement", json={"file_name": name, "file_size": len(data)})
        assert start.status_code == 201, start.text
        n = start.json()["parts"]
        urls = client.post(f"/api/datasets/{up['id']}/replacement/urls", json={"parts": list(range(1, n + 1))}).json()["urls"]
        parts = []
        for k in range(1, n + 1):
            put = client.put(urls[str(k)], content=data[(k - 1) * 50_000:k * 50_000])
            assert put.status_code == 200
            parts.append({"n": k, "etag": put.headers["etag"]})
        return client.post(f"/api/datasets/{up['id']}/replacement/complete", json={"parts": parts})

    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        data = f.read()
    text = gz.decompress(data).decode() if data[:2] == b"\x1f\x8b" else data.decode()
    se_col = entry["column_mapping"]["se"]
    sep = "\t" if "\t" in text.splitlines()[0] else ","
    header = text.splitlines()[0].split(sep)
    renamed = sep.join("SE_RENAMED" if c == se_col else c for c in header) + "\n" + text.split("\n", 1)[1]
    r = replace(renamed.encode(), "corrected.tsv")  # a mapped column missing: refused, the dataset keeps its file
    assert r.status_code == 422 and se_col in r.json()["detail"]
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] == "changes_requested" and d["upload_location"] == old

    r = replace(data, "corrected_" + entry["file"])
    assert r.status_code == 200, r.text
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["file_name"] == "corrected_" + entry["file"] and d["review"] is None and d["status"] in ("passed", "review")
    assert d["upload_location"] != old and not os.path.exists(old)  # the old file is gone
    assert d["edits"][-1]["changes"]["file"] == [entry["file"], "corrected_" + entry["file"]]
    assert client.get(f"/api/datasets/{up['id']}/files/raw").content == data

    # Once it has passed again, only a reviewer can replace it; another uploader never can.
    assert client.post(f"/api/datasets/{up['id']}/replacement", json={"file_name": "x.tsv", "file_size": 10}).status_code == 403
    as_user(client, "ama@example.org")
    client.post(f"/api/datasets/{up['id']}/review", json={"decision": "request_changes"})
    as_user(client, "jonas@example.org")
    assert client.post(f"/api/datasets/{up['id']}/rerun").status_code == 200  # the uploader can re-run after a request
    as_user(client, "mei@example.org")
    assert client.post(f"/api/datasets/{up['id']}/replacement", json={"file_name": "x.tsv", "file_size": 10}).status_code in (403, 404)


def test_abandoned_upload_can_be_deleted(client, synth_small):
    entry = synth_small["manifest"][0]
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    as_user(client, "jonas@example.org")
    up = client.post("/api/projects/hermes3/uploads", json={"meta": meta, "file_name": "x.tsv", "file_size": 10}).json()
    assert client.get(f"/api/datasets/{up['id']}/files/raw").status_code == 409
    assert client.delete(f"/api/datasets/{up['id']}").status_code == 204


def test_batch_events_are_recorded(client, synth_small):
    """The worker's side of AWS Batch: job state changes update the run (outputs written as a job would)."""
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import QCRun
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    db = SessionLocal()
    try:
        done = db.get(QCRun, client.get(f"/api/datasets/{up['id']}").json()["run"]["id"])
        result = done.result
        # Pretend a second run is on Batch.
        run = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="queued",
                    run_dir=done.run_dir, batch_job_id="job-1")
        db.add(run)
        done.dataset.status = "processing"
        db.commit()
        ev = lambda status, **kw: {"detail": {"jobId": "job-1", "status": status, **kw}}  # noqa: E731
        assert "no matching run" in jobs.handle_batch_event(db, {"detail": {"jobId": "other", "status": "RUNNING"}})
        assert jobs.handle_batch_event(db, ev("RUNNING")) == "job-1 running"
        db.refresh(run)
        assert run.status == "running" and run.started_at
        assert "recorded: completed" in jobs.handle_batch_event(db, ev("SUCCEEDED"))
        db.refresh(run)
        assert run.status == "completed" and run.result["overall_verdict"] == result["overall_verdict"]
        assert run.dataset.status in ("passed", "review")
        assert "already recorded" in jobs.handle_batch_event(db, ev("FAILED"))

        run2 = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="running",
                     run_dir=done.run_dir + "_missing", batch_job_id="job-2")
        db.add(run2)
        db.commit()
        jobs.handle_batch_event(db, {"detail": {"jobId": "job-2", "status": "FAILED", "statusReason": "Host EC2 terminated",
                                                "container": {"logStreamName": "gwashub/default/abc"}}})
        db.refresh(run2)
        assert run2.status == "failed" and "Host EC2 terminated" in run2.error and "gwashub/default/abc" in run2.error
        assert run2.dataset.status == "failed"
    finally:
        db.close()


def test_session_tokens_expire():
    from gwashub_api import auth
    assert auth.verify(auth.sign(5)) == 5
    assert auth.verify(auth.sign(5, expires=int(__import__("time").time()) - 1)) is None
    assert auth.verify(auth.sign(5).replace("5.", "6.", 1)) is None
    assert auth.verify("5.abc") is None and auth.verify(None) is None


@pytest.fixture
def cognito_mode(client, monkeypatch):
    """Cognito settings plus a fake identity provider: our own RSA key signs the ID tokens."""
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from gwashub_api.config import settings
    from gwashub_api.routers import cognito
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = settings()
    for k, v in {"auth_mode": "cognito", "cognito_domain": "https://gh-test.auth.eu-west-2.amazoncognito.com",
                 "cognito_user_pool_id": "eu-west-2_TEST", "cognito_client_id": "client123",
                 "cognito_client_secret": "shh", "public_url": "https://site.example"}.items():
        monkeypatch.setattr(s, k, v)

    class Keys:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setitem(cognito._jwks, "client", Keys())
    issued = {}

    def make_token(sub, email, **claims):
        now = int(time.time())
        body = {"sub": sub, "email": email, "email_verified": True, "aud": "client123", "iss": cognito.issuer(),
                "token_use": "id", "iat": now, "exp": now + 300, **claims}
        return jwt.encode(body, key, algorithm="RS256")

    def exchange(code, verifier):
        assert verifier, "PKCE verifier must be sent"
        return {"id_token": issued[code]}
    monkeypatch.setattr(cognito, "exchange_code", exchange)
    from gwashub_api.main import app
    with TestClient(app, base_url="https://testserver") as https_client:  # Secure cookies need HTTPS
        yield https_client, issued, make_token


def _cognito_login(client, issued, token):
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == ["client123"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://site.example/api/auth/callback"]
    issued["code1"] = token
    return client.get("/api/auth/callback", params={"code": "code1", "state": q["state"][0]}, follow_redirects=False)


def test_cognito_sign_in(cognito_mode):
    client, issued, make_token = cognito_mode
    assert client.get("/api/auth/mode").json()["mode"] == "cognito"
    assert client.post("/api/auth/login", json={"email": "jonas@example.org"}).status_code == 404
    assert client.get("/api/auth/users").status_code == 404

    r = _cognito_login(client, issued, make_token("sub-jonas", "Jonas@Example.org"))
    assert r.status_code == 302 and r.headers["location"] == "/projects/hermes3"
    assert "gh_session=" in r.headers["set-cookie"] and "secure" in r.headers["set-cookie"].lower()
    assert client.get("/api/me").json()["email"] == "jonas@example.org"
    out = client.post("/api/auth/logout").json()
    assert out["logout_url"].startswith("https://gh-test.auth.eu-west-2.amazoncognito.com/logout?client_id=client123")
    client.cookies.clear()

    # The account is now linked by Cognito's sub: a later sign-in with the same sub works.
    r = _cognito_login(client, issued, make_token("sub-jonas", "jonas@example.org"))
    assert r.headers["location"] == "/projects/hermes3"
    client.cookies.clear()


def test_cognito_rejects_bad_sign_ins(cognito_mode):
    client, issued, make_token = cognito_mode
    r = _cognito_login(client, issued, make_token("sub-x", "stranger@example.org"))
    assert r.headers["location"] == "/login?error=not_registered" and "gh_session" not in r.headers.get("set-cookie", "")
    client.cookies.clear()
    r = _cognito_login(client, issued, make_token("sub-y", "mei@example.org", aud="someone-else"))
    assert r.headers["location"] == "/login?error=failed"
    client.cookies.clear()
    r = _cognito_login(client, issued, make_token("sub-z", "mei@example.org", email_verified=False))
    assert r.headers["location"] == "/login?error=not_registered"
    client.cookies.clear()
    # Wrong state (e.g. a link from someone else's sign-in) is refused.
    client.get("/api/auth/login", follow_redirects=False)
    issued["code2"] = make_token("sub-m", "mei@example.org")
    r = client.get("/api/auth/callback", params={"code": "code2", "state": "forged"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=expired"
    assert client.get("/api/me").status_code == 401


def test_admin_invite_and_projects(client, capsys):
    from gwashub_api import admin
    admin.main(["invite", "New.Person@Example.org", "New Person", "--institution", "Leeds"])
    admin.main(["add-project", "lvsys", "LV systolic function GWAS", "LVSF", "--phenotype", "LVEF"])
    admin.main(["add-member", "new.person@example.org", "lvsys", "reviewer"])
    out = capsys.readouterr().out
    assert "Created GWASHub user new.person@example.org" in out and "Created project lvsys" in out
    as_user(client, "new.person@example.org")
    p = client.get("/api/projects/lvsys").json()
    assert p["role"] == "reviewer" and client.get("/api/projects/lvsys/pipeline").json()["version"] == 1
    admin.main(["remove-member", "new.person@example.org", "lvsys"])
    assert client.get("/api/projects/lvsys").status_code == 404


def test_admin_add_resources_from_manifest(client, tmp_path, capsys):
    """add-resources registers files already in storage; the same sha256 again is a no-op, a new one a new version."""
    import json
    from gwashub_api import admin
    from gwashub_api.storage import sha256_file
    f = tmp_path / "genes_test.tsv.gz"
    f.write_bytes(b"gene data")
    entry = {"key": "genes_test.tsv.gz", "title": "Genes", "description": "v1", "build": "GRCh38", "source": "https://example.org",
             "sha256": sha256_file(str(f)), "size": f.stat().st_size, "storage_path": str(f)}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"resources": [entry]}))
    admin.main(["add-resources", str(manifest)])
    admin.main(["add-resources", str(manifest)])
    f.write_bytes(b"gene data, rebuilt")
    manifest.write_text(json.dumps({"resources": [{**entry, "sha256": sha256_file(str(f)), "size": f.stat().st_size}]}))
    admin.main(["add-resources", str(manifest)])
    out = capsys.readouterr().out
    assert "added as v1" in out and "unchanged (v1)" in out and "added as v2" in out
    from sqlalchemy import select
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Resource
    with SessionLocal() as db:
        rows = db.scalars(select(Resource).where(Resource.key == "genes_test.tsv.gz").order_by(Resource.version)).all()
        assert [(r.version, r.project_id, r.source) for r in rows] == [(1, None, "https://example.org"), (2, None, "https://example.org")]
    manifest.write_text(json.dumps({"resources": [{**entry, "storage_path": str(tmp_path / "absent")}]}))
    with pytest.raises(SystemExit):
        admin.main(["add-resources", str(manifest)])


def test_worker_records_messages_and_reconciles(client, synth_small, monkeypatch):
    from gwashub_api import jobs, worker
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import QCRun
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    db = SessionLocal()
    done = db.get(QCRun, client.get(f"/api/datasets/{up['id']}").json()["run"]["id"])
    a = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="queued", run_dir=done.run_dir, batch_job_id="w-1")
    b = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="running", run_dir=done.run_dir, batch_job_id="w-2")
    db.add_all([a, b])
    db.commit()
    ids = (a.id, b.id)
    db.close()

    msg = json.dumps({"source": "aws.batch", "detail-type": "Batch Job State Change", "detail": {"jobId": "w-1", "status": "SUCCEEDED"}})
    assert "recorded: completed" in worker.handle_message(msg)

    class FakeBatch:
        def describe_jobs(self, jobs):
            assert jobs == ["w-2"]  # only open runs are asked about
            return {"jobs": [{"jobId": "w-2", "status": "FAILED", "statusReason": "Essential container exited"}]}
    monkeypatch.setattr(jobs, "_batch", lambda: FakeBatch())
    worker.reconcile()
    db = SessionLocal()
    assert db.get(QCRun, ids[0]).status == "completed"
    assert db.get(QCRun, ids[1]).status == "failed" and "Essential container exited" in db.get(QCRun, ids[1]).error
    db.close()


def test_step_files_listing_and_download(client, synth_small):
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    d = client.get(f"/api/datasets/{up['id']}").json()
    run_id = d["run"]["id"]
    plots = next(s["dir"] for s in d["run"]["steps"] if s["key"] == "gwashub/plots")
    files = client.get(f"/api/datasets/{up['id']}/runs/{run_id}/files", params={"dir": plots}).json()
    paths = {f["path"] for f in files}
    assert {f"{plots}/manhattan.png", f"{plots}/step_log.json"} <= paths
    assert f"{plots}/output.txt" not in paths  # nothing printed, nothing kept
    assert all(f["size"] > 0 for f in files)
    r = client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": f"{plots}/manhattan.png", "download": 1})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/datasets/{up['id']}/runs/{run_id}/files", params={"dir": "../.."}).status_code == 404
    as_user(client, "mei@example.org")
    assert client.get(f"/api/datasets/{up['id']}/runs/{run_id}/files", params={"dir": plots}).status_code == 403


def test_grouped_batch_events(client, synth_small, tmp_path, monkeypatch):
    """QC groups run one Batch job at a time: a group's success submits the group its hand-over names (a repeated
    event changes nothing), the job that writes result.json records the run, and any group's failure fails it."""
    import shutil
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import QCRun
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    submitted = []
    monkeypatch.setattr(jobs, "_submit_batch", lambda spec, name, image, extra_args=(), put_spec=True, compute=None:
                        submitted.append((name, image, list(extra_args))) or f"job-{len(submitted)}")
    db = SessionLocal()
    try:
        done = db.get(QCRun, client.get(f"/api/datasets/{up['id']}").json()["run"]["id"])
        run_dir = tmp_path / "run"
        (run_dir / "_state").mkdir(parents=True)
        (run_dir / "spec.json").write_text(json.dumps({"groups": [{"image": "gwashub/base-r", "steps": [0, 1]},
                                                                  {"image": "gwashub/vcf", "steps": [2, 3]},
                                                                  {"image": "gwashub/base", "steps": [4]}]}))
        (run_dir / "_state" / "state.json").write_text(json.dumps({"status": "blocked", "next_group": 2}))  # skip group 1
        run = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="queued",
                    run_dir=str(run_dir), batch_job_id="first-job")
        db.add(run)
        db.commit()
        ev = lambda job, k, status, **kw: {"detail": {"jobId": job, "jobName": f"gwashub-qc-{run.id}-g{k}", "status": status, **kw}}  # noqa: E731
        assert "running" in jobs.handle_batch_event(db, ev("first-job", 0, "RUNNING"))
        assert "submitted group 2" in jobs.handle_batch_event(db, ev("first-job", 0, "SUCCEEDED"))
        db.refresh(run)
        assert run.status == "running" and run.batch_job_id == "job-1"
        assert submitted == [(f"gwashub-qc-{run.id}-g2", "gwashub/base", ["--group", "2"])]
        assert "finished a group" in jobs.handle_batch_event(db, ev("first-job", 0, "SUCCEEDED"))  # SQS delivered it twice
        assert len(submitted) == 1
        shutil.copyfile(os.path.join(done.run_dir, "result.json"), run_dir / "result.json")
        assert "recorded: completed" in jobs.handle_batch_event(db, ev("job-1", 2, "SUCCEEDED"))

        # A job that leaves neither result.json nor a hand-over fails the run.
        run3 = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="running",
                     run_dir=str(tmp_path / "empty"), batch_job_id="r3-first")
        db.add(run3)
        db.commit()
        jobs.handle_batch_event(db, {"detail": {"jobId": "r3-first", "jobName": f"gwashub-qc-{run3.id}-g0", "status": "SUCCEEDED"}})
        db.refresh(run3)
        assert run3.status == "failed" and "next group" in run3.error

        # A group's job failing fails the run; a later event for it is ignored, the first reason stays.
        run2 = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="running",
                     run_dir=done.run_dir, batch_job_id="r2-mid")
        db.add(run2)
        db.commit()
        jobs.handle_batch_event(db, {"detail": {"jobId": "r2-mid", "jobName": f"gwashub-qc-{run2.id}-g1", "status": "FAILED",
                                                "statusReason": "OutOfMemoryError: Container killed due to memory usage"}})
        db.refresh(run2)
        assert run2.status == "failed" and "OutOfMemoryError" in run2.error
        assert "already recorded" in jobs.handle_batch_event(db, {"detail": {"jobId": "r2-mid", "jobName": f"gwashub-qc-{run2.id}-g1",
                                                                             "status": "FAILED", "statusReason": "Essential container exited"}})
    finally:
        db.close()


def test_meta_analyses_go_to_the_on_demand_queue(monkeypatch):
    """On AWS, meta-analyses go to the image's On-Demand queue (a Spot reclaim would restart one from the beginning);
    QC jobs stay on the Spot queue, and an image with no On-Demand queue uses its own."""
    from gwashub_api import jobs
    from gwashub_api.config import settings
    monkeypatch.setitem(settings().images, "gwashub/gwama", {"job_definition": "jd-gwama", "job_queue": "spot-q", "meta_job_queue": "od-q"})
    monkeypatch.setitem(settings().images, "testco/plain", {"job_definition": "jd-plain", "job_queue": "spot-q"})
    sent = []

    class FakeBatch:
        def submit_job(self, **kw):
            sent.append(kw["jobQueue"])
            return {"jobId": f"job-{len(sent)}"}
    monkeypatch.setattr(jobs, "_batch", lambda: FakeBatch())
    spec = {"output": "s3://results/meta/x"}
    jobs._submit_batch(spec, "gwashub-meta-1", "gwashub/gwama", put_spec=False, meta=True)
    jobs._submit_batch(spec, "gwashub-qc-1-g0", "gwashub/gwama", put_spec=False)
    jobs._submit_batch(spec, "gwashub-meta-2", "testco/plain", put_spec=False, meta=True)
    assert sent == ["od-q", "spot-q", "spot-q"]


def test_reviewers_manage_members(client, monkeypatch):
    from gwashub_api import accounts
    sent = []
    monkeypatch.setattr(accounts, "cognito_invite", lambda email, name, resend=False: sent.append(email) or "invitation email sent")
    monkeypatch.setattr(accounts, "settings", lambda: type("S", (), {"auth_mode": "cognito"})())  # invitations go through Cognito
    as_user(client, "jonas@example.org")  # uploader: can't manage members, doesn't see emails
    assert client.post("/api/projects/hermes3/members", json={"email": "x@y.org", "name": "X", "role": "uploader"}).status_code == 403
    assert all("email" not in m for m in client.get("/api/projects/hermes3").json()["members"])

    as_user(client, "ama@example.org")  # reviewer
    assert all("email" in m for m in client.get("/api/projects/hermes3").json()["members"])
    r = client.post("/api/projects/hermes3/members", json={"email": " New.Uploader@Uni.ac.uk ", "name": "New Uploader",
                                                            "institution": "Uni", "role": "uploader"})
    assert r.status_code == 201 and r.json()["account"] == "invited" and sent == ["new.uploader@uni.ac.uk"]
    new_id = r.json()["id"]
    # Someone who already has an account is just added (no new invitation).
    r = client.post("/api/projects/hermes3/members", json={"email": "tom@example.org", "role": "reviewer"})
    assert r.status_code == 201 and r.json()["account"] == "existing" and len(sent) == 1
    assert client.post("/api/projects/hermes3/members", json={"email": "tom@example.org", "role": "uploader"}).status_code == 409
    assert client.post("/api/projects/hermes3/members", json={"email": "not-an-email", "name": "N", "role": "uploader"}).status_code == 422
    assert client.post("/api/projects/hermes3/members", json={"email": "a@b.org", "name": "A", "role": "admin"}).status_code == 422

    assert client.patch(f"/api/projects/hermes3/members/{new_id}", json={"role": "reviewer"}).json()["role"] == "reviewer"
    assert client.delete(f"/api/projects/hermes3/members/{new_id}").status_code == 204
    roles = {m["email"]: m["role"] for m in client.get("/api/projects/hermes3").json()["members"]}
    assert "new.uploader@uni.ac.uk" not in roles and roles["tom@example.org"] == "reviewer"
    # Not a reviewer of HCM-only things: managing another project's members is refused.
    assert client.post("/api/projects/hcm/members", json={"email": "z@y.org", "name": "Z", "role": "uploader"}).status_code == 404


def test_project_keeps_a_reviewer(client):
    as_user(client, "demo@gwashub.org")
    pid = client.post("/api/projects", json={"id": "solo", "name": "Solo project", "short": "Solo", "phenotypes": ["X"]}).json()["id"]
    me = next(m for m in client.get(f"/api/projects/{pid}").json()["members"] if m["is_me"])
    assert client.patch(f"/api/projects/{pid}/members/{me['id']}", json={"role": "uploader"}).status_code == 409
    assert client.delete(f"/api/projects/{pid}/members/{me['id']}").status_code == 409


def test_admins_delete_projects_with_everything_in_them(client, synth_small, monkeypatch):
    from gwashub_api import config, storage
    monkeypatch.setattr(storage, "PART_SIZE", 1000)
    entry = synth_small["manifest"][0]
    as_user(client, "demo@gwashub.org")
    name = "Project to delete"
    assert client.post("/api/projects", json={"id": "gone", "name": name, "short": "Gone", "phenotypes": [entry["phenotype"]]}).status_code == 201
    upload(client, synth_small, entry, project="gone")
    r = _upload_resource_in_parts(client, {"scope": "gone", "key": "regions.bed", "title": "Regions", "build": "GRCh38", "attest": True},
                                  b"chr1\t1\t2\n" * 50, 1000)
    assert r.status_code == 201
    s = config.settings()
    folders = [os.path.join(s.storage_dir, "uploads", "gone"), os.path.join(s.data_dir, "runs", "qc", "gone"),
               os.path.join(s.storage_dir, "resources", "gone")]
    assert all(os.listdir(f) for f in folders)
    url = "/api/projects/gone"
    assert client.request("DELETE", url, json={"confirm": "project to delete"}).status_code == 422  # the name exactly
    as_user(client, "ama@example.org")  # not a platform admin
    assert client.request("DELETE", url, json={"confirm": name}).status_code == 403
    as_user(client, "demo@gwashub.org")
    assert client.request("DELETE", url, json={"confirm": name}).status_code == 204
    assert client.get(url).status_code == 404 and "gone" not in {p["id"] for p in client.get("/api/projects").json()}
    assert not any(os.path.exists(f) for f in folders)
    # Other projects are untouched, and the id can be used again.
    assert client.get("/api/projects/hermes3").status_code == 200
    assert client.post("/api/projects", json={"id": "gone", "name": name, "short": "Gone", "phenotypes": ["X"]}).status_code == 201
    assert client.get("/api/projects/gone/datasets").json() == []


def test_platform_admins_create_projects(client):
    as_user(client, "ama@example.org")  # reviewer, not a platform admin
    body = {"id": "lvsf", "name": "LV systolic function GWAS", "short": "LVSF", "description": "Test", "phenotypes": ["LVEF", " ", "LVEF"]}
    assert client.post("/api/projects", json=body).status_code == 403
    as_user(client, "demo@gwashub.org")  # platform admin
    assert client.get("/api/me").json()["is_admin"]
    assert client.post("/api/projects", json={**body, "id": "Bad ID!"}).status_code == 422
    assert client.post("/api/projects", json={**body, "phenotypes": []}).status_code == 422
    assert client.post("/api/projects", json=body).status_code == 201
    assert client.post("/api/projects", json=body).status_code == 409
    p = client.get("/api/projects/lvsf").json()
    assert p["role"] == "reviewer" and p["phenotypes"] == ["LVEF"] and p["pipeline"]["version"] == 1



def test_new_projects_start_from_a_template(client):
    """A new project's first pipeline starts from a template (pipeline/qc_steps/templates): the default unless another is
    named; a template's meta_method becomes the project's default meta-analysis method."""
    tpls = {t["name"]: t for t in client.get("/api/templates").json()}
    assert list(tpls)[0] == "default" and tpls["default"]["meta_method"] == "gwashub/gwama"
    as_user(client, "demo@gwashub.org")
    body = {"id": "tpl-test", "name": "Template test", "short": "TPL", "phenotypes": ["HF"], "template": "default"}
    assert client.post("/api/projects", json={**body, "template": "nope"}).status_code == 422
    assert client.post("/api/projects", json=body).status_code == 201
    pl = client.get("/api/projects/tpl-test/pipeline").json()
    assert [s["step"] for s in pl["steps"]] == tpls["default"]["steps"]
    assert client.get("/api/projects/tpl-test/meta/settings").json()["settings"]["method"] == "gwashub/gwama"


def test_cohorts_hold_shared_metadata_and_datasets_override(client, synth_small):
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")
    first = upload(client, synth_small, entry, cohort="Cohort Alpha", study_meta={"imputation_reference": "HRC r1.1", "case_type": "Incident"})
    cohorts = {c["name"]: c for c in client.get("/api/projects/hermes3/cohorts").json()}
    alpha = cohorts["Cohort Alpha"]
    assert alpha["meta"] == {"imputation_reference": "HRC r1.1"} and alpha["datasets"] == 1 and alpha["can_edit"]
    # A second upload naming the same cohort (any spacing / case) joins it and inherits its metadata.
    second = upload(client, synth_small, entry, cohort="cohort-alpha", study_meta={})
    d2 = client.get(f"/api/datasets/{second['id']}").json()
    assert d2["cohort"] == "Cohort Alpha" and d2["cohort_id"] == alpha["id"]
    assert d2["study_meta"]["imputation_reference"] == "HRC r1.1" and d2["overrides"] == []
    # Override one cohort-level field for this dataset only.
    r = client.patch(f"/api/datasets/{second['id']}", json={"study_meta": {**d2["study_meta"], "imputation_reference": "TOPMed r2"}})
    assert r.status_code == 200
    d2 = client.get(f"/api/datasets/{second['id']}").json()
    assert d2["study_meta"]["imputation_reference"] == "TOPMed r2" and d2["overrides"] == ["imputation_reference"]
    # Editing the cohort flows through to datasets without an override.
    assert client.patch(f"/api/cohorts/{alpha['id']}", json={"meta": {"imputation_reference": "HRC r1.1", "genotyping_array": "GSA"}}).status_code == 200
    d1 = client.get(f"/api/datasets/{first['id']}").json()
    assert d1["study_meta"]["genotyping_array"] == "GSA" and client.get(f"/api/datasets/{second['id']}").json()["study_meta"]["imputation_reference"] == "TOPMed r2"
    assert client.patch(f"/api/cohorts/{alpha['id']}", json={"meta": {"case_type": "Incident"}}).status_code == 422  # dataset-level field
    # Similar names are flagged before creating; exact duplicates are refused.
    sim = client.get("/api/projects/hermes3/cohorts/similar", params={"name": "Cohort Alfa"}).json()
    assert any(c["name"] == "Cohort Alpha" for c in sim)
    assert client.post("/api/projects/hermes3/cohorts", json={"name": "COHORT alpha"}).status_code == 409
    assert client.post("/api/projects/hermes3/cohorts", json={"name": "Cohort Beta"}).status_code == 201
    # Renaming the cohort renames it on its datasets; only its creator or a reviewer may.
    as_user(client, "mei@example.org")
    assert client.patch(f"/api/cohorts/{alpha['id']}", json={"name": "Nope"}).status_code == 403
    as_user(client, "ama@example.org")
    assert client.patch(f"/api/cohorts/{alpha['id']}", json={"name": "Cohort A"}).status_code == 200
    assert client.get(f"/api/datasets/{first['id']}").json()["cohort"] == "Cohort A"
    # Datasets table rows carry the cohort, the pipeline version and headline QC metrics.
    row = next(x for x in client.get("/api/projects/hermes3/datasets").json() if x["id"] == first["id"])
    assert row["cohort_id"] == alpha["id"] and row["pipeline_version"] >= 1
    assert row["qc"]["lambda_gc"] > 0 and row["qc"]["n_variants"] > 0 and "lifted" in row["qc"] and "n_suggestive" in row["qc"]


def test_metadata_config_and_required_fields(client, synth_small):
    entry = synth_small["manifest"][0]
    as_user(client, "jonas@example.org")  # uploaders can't change the config
    assert client.put("/api/projects/hermes3/metadata-config", json={"shown": ["country"], "required": []}).status_code == 403
    as_user(client, "ama@example.org")
    assert client.put("/api/projects/hermes3/metadata-config", json={"shown": ["nope"]}).status_code == 422
    assert client.put("/api/projects/hermes3/metadata-config", json={"shown": ["country"], "required": ["case_type"]}).status_code == 422
    schema = client.get("/api/metadata-schema").json()
    shown = schema["default_config"]["shown"] + ["country", "data_freeze_date"]
    r = client.put("/api/projects/hermes3/metadata-config", json={"shown": shown, "required": ["country", "case_type"]})
    assert r.status_code == 200 and r.json()["required"] == ["country", "case_type"]
    p = client.get("/api/projects/hermes3").json()
    assert "country" in p["metadata_config"]["shown"]
    # Uploads now need the required fields (country from the cohort, case type per dataset).
    as_user(client, "jonas@example.org")
    meta = {k: entry[k] for k in ("phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post("/api/projects/hermes3/datasets", files={"file": (entry["file"], f)},
                        data={"meta": json.dumps({**meta, "cohort": "Cohort Gamma", "study_meta": {"case_type": "Incident"}})})
    assert r.status_code == 422 and "Country" in r.json()["detail"]
    up = upload(client, synth_small, entry, cohort="Cohort Gamma", study_meta={"case_type": "Incident", "country": "Norway"})
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["metadata_complete"]["missing_required"] == []
    # Existing datasets just show what's missing.
    old = [x for x in client.get("/api/projects/hermes3/datasets").json() if x["can_open"] and x["id"] != up["id"]]
    assert old and any("Country/countries of ascertainment" in x["metadata_complete"]["missing_required"] for x in old)
    as_user(client, "ama@example.org")  # put the defaults back for the other tests
    assert client.put("/api/projects/hermes3/metadata-config", json=schema["default_config"]).status_code == 200


def test_phenotype_descriptions(client):
    as_user(client, "ama@example.org")
    p = client.get("/api/projects/hermes3").json()
    body = {"phenotypes": p["phenotypes"], "ancestries": p["ancestries"], "sexes": p["sexes"],
            "phenotype_descriptions": {p["phenotypes"][0]: "All-cause heart failure", "not-a-code": "x"}}
    r = client.put("/api/projects/hermes3/upload-options", json=body)
    assert r.status_code == 200 and r.json()["phenotype_descriptions"] == {p["phenotypes"][0]: "All-cause heart failure"}
    assert client.get("/api/projects/hermes3").json()["phenotype_descriptions"][p["phenotypes"][0]] == "All-cause heart failure"
    # Saving without descriptions keeps the ones for codes that remain.
    body.pop("phenotype_descriptions")
    assert client.put("/api/projects/hermes3/upload-options", json=body).json()["phenotype_descriptions"] == {p["phenotypes"][0]: "All-cause heart failure"}


def test_bulk_rerun(client, synth_small):
    as_user(client, "jonas@example.org")
    a = upload(client, synth_small, synth_small["manifest"][0])
    b = upload(client, synth_small, synth_small["manifest"][2])
    r = client.post("/api/projects/hermes3/datasets/rerun", json={"ids": [a["id"]]}).json()  # uploaders only start pending ones
    assert r["started"] == 0 and r["skipped"] == [{"id": a["id"], "reason": "only reviewers can re-run QC"}]
    as_user(client, "ama@example.org")
    before = {i: len(client.get(f"/api/datasets/{i}").json()["runs"]) for i in (a["id"], b["id"])}
    r = client.post("/api/projects/hermes3/datasets/rerun", json={"ids": [a["id"], b["id"], 999999]}).json()
    assert r["started"] == 2 and r["skipped"] == [{"id": 999999, "reason": "not in this project"}]
    for i in (a["id"], b["id"]):
        assert len(client.get(f"/api/datasets/{i}").json()["runs"]) == before[i] + 1


def test_project_metadata_preset(client):
    as_user(client, "demo@gwashub.org")
    r = client.post("/api/projects", json={"id": "skin", "name": "Skin GWAS", "short": "SGC", "phenotypes": ["AD"], "metadata_preset": "all"})
    assert r.status_code == 201
    cfg = client.get("/api/projects/skin").json()["metadata_config"]
    assert "ethics_approval" in cfg["shown"] and "country" in cfg["shown"]


def test_existing_datasets_get_cohort_records(client, synth_small):
    """What happens on the first start after cohorts were introduced: names become Cohort records."""
    from gwashub_api import services
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Cohort, Dataset
    as_user(client, "jonas@example.org")
    a = upload(client, synth_small, synth_small["manifest"][0], cohort="Legacy Study", study_meta={"imputation_reference": "1000G", "case_type": "Incident"})
    b = upload(client, synth_small, synth_small["manifest"][2], cohort="Legacy Study", study_meta={"imputation_reference": "1000G"})
    db = SessionLocal()
    try:
        cid = db.get(Dataset, a["id"]).cohort_id
        for i in (a["id"], b["id"]):  # make them look like pre-cohort datasets
            ds = db.get(Dataset, i)
            ds.cohort_id, ds.study_meta = None, {"imputation_reference": "1000G", **({"case_type": "Incident"} if i == a["id"] else {})}
        db.delete(db.get(Cohort, cid))
        db.commit()
        services.link_cohorts(db)
        da, dbb = db.get(Dataset, a["id"]), db.get(Dataset, b["id"])
        assert da.cohort_id and da.cohort_id == dbb.cohort_id
        assert db.get(Cohort, da.cohort_id).meta == {"imputation_reference": "1000G"}
        assert da.study_meta == {"case_type": "Incident"} and dbb.study_meta == {}  # no needless overrides left
    finally:
        db.close()


def test_validation_page_lists_tests_and_serves_their_files(client):
    """The Validation page reads validation/<test>/ from the repository: signed-in users only, listed files only."""
    client.cookies.clear()
    assert client.get("/api/validation").status_code == 401
    as_user(client, "jonas@example.org")  # any signed-in user, not only reviewers
    tests = {t["test"]: t for t in client.get("/api/validation").json()}
    t = tests["01_gwama_default"]
    assert t["title"] and t["claim"] and t["status"] in ("not_run", "passed", "failed")
    assert "README.md" in t["files"] and "1_prepare.R" in t["files"]
    r = client.get("/api/validation/01_gwama_default/files/README.md")
    assert r.status_code == 200 and "GWAMA" in r.text
    assert client.get("/api/validation/01_gwama_default/files/..%2F..%2Fsources.yaml").status_code == 404
    assert client.get("/api/validation/_lib/files/common.R").status_code == 404
    assert client.get("/api/validation/nope/plot").status_code == 404


def test_quantitative_phenotypes_take_a_total_sample_size(client, synth_small):
    """A phenotype is case/control (cases + controls, total = their sum) or quantitative (total only, BETA effects);
    its type can't change once it has datasets, and a meta-analysis can't mix the two."""
    as_user(client, "tom@example.org")  # reviewer of hcm
    p = client.get("/api/projects/hcm").json()
    opts = {"phenotypes": p["phenotypes"] + ["LVEF"], "ancestries": p["ancestries"], "sexes": p["sexes"],
            "phenotype_types": {"LVEF": "quantitative"}}
    r = client.put("/api/projects/hcm/upload-options", json=opts)
    assert r.status_code == 200 and r.json()["phenotype_types"]["LVEF"] == "quantitative"
    assert r.json()["phenotype_types"]["HCM"] == "binary"
    entry = next(e for e in synth_small["manifest"] if e["effect_type"] == "beta")

    def post(**override):
        meta = {k: entry[k] for k in ("ancestry", "sex", "build", "column_mapping")} | {"cohort": "QuantCohort", "effect_type": "beta"}
        meta.update(override)
        with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
            return client.post("/api/projects/hcm/datasets", files={"file": (entry["file"], f)}, data={"meta": json.dumps(meta)})

    assert "total sample size" in post(phenotype="LVEF", n_cases=10, n_controls=20).json()["detail"]
    assert "odds ratios" in post(phenotype="LVEF", n_total=5000, effect_type="or").json()["detail"]
    assert "controls" in post(phenotype="HCM", n_cases=100).json()["detail"]
    q = post(phenotype="LVEF", n_total=5000, n_cases=10)
    assert q.status_code == 201, q.text
    d = client.get(f"/api/datasets/{q.json()['id']}").json()
    assert (d["trait_type"], d["n_total"], d["n_cases"], d["n_controls"]) == ("quantitative", 5000, None, None)
    b = post(phenotype="HCM", n_cases=100, n_controls=900)
    db_ = client.get(f"/api/datasets/{b.json()['id']}").json()
    assert (db_["trait_type"], db_["n_total"]) == ("binary", 1000)

    # Editing follows the type: a quantitative dataset's total can change; it can't be given an invalid total.
    assert client.patch(f"/api/datasets/{d['id']}", json={"n_total": 6000}).json()["changed"] == ["n_total"]
    assert client.patch(f"/api/datasets/{d['id']}", json={"n_total": 0}).status_code == 422
    # The type is fixed once a phenotype has datasets.
    r = client.put("/api/projects/hcm/upload-options", json={**opts, "phenotype_types": {}})
    assert r.status_code == 409 and "LVEF" in r.json()["detail"]
    # A meta-analysis can't mix case/control and quantitative datasets.
    for x in (d, db_):
        assert client.post(f"/api/datasets/{x['id']}/rerun").status_code == 200  # uploads wait as pending
        assert client.get(f"/api/datasets/{x['id']}").json()["status"] in ("passed", "review")
    r = client.post("/api/projects/hcm/meta", json={"name": "mixed", "dataset_ids": [d["id"], db_["id"]],
                                                   "settings": {"include_statuses": ["passed", "review"]}})
    assert r.status_code == 422 and "mix" in r.json()["detail"], r.text


def test_batch_states_show_waiting_and_requeued_steps(client, synth_small):
    """AWS Batch job events say whether a run's current job group is waiting for a machine, starting, running, or
    re-queued after its machine was taken back; the dataset panel shows it on the group's next step."""
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import QCRun
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    db = SessionLocal()
    done = db.get(QCRun, client.get(f"/api/datasets/{up['id']}").json()["run"]["id"])
    # Its own (empty) folder, as every run has: the finished run's progress.json would be read as this run's live steps.
    run = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="queued", run_dir=done.run_dir + "-requeued",
                progress=[{"key": s["step"], "title": s["step"], "state": "queued"} for s in done.pipeline_version.steps])
    db.add(run)
    db.commit()
    groups = next(x["groups"] for x in run.pipeline_version.resolved if "groups" in x)
    g = 1 if len(groups) > 1 else 0
    first = groups[g]["steps"][0]

    def event(status, attempts=()):
        return {"detail": {"jobId": f"job-{run.id}-{g}", "jobName": f"gwashub-qc-{run.id}-g{g}", "status": status,
                           "attempts": list(attempts), "retryStrategy": {"attempts": 4}}}

    def step():
        return client.get(f"/api/datasets/{run.dataset_id}").json()["run"]["steps"][first]

    db2 = SessionLocal()
    assert "runnable" in jobs.handle_batch_event(db2, event("RUNNABLE"))
    s = step()
    assert s["state"] == "queued" and s["note"] == "Waiting for a machine"
    jobs.handle_batch_event(db2, event("RUNNING"))
    assert "note" not in step()  # running on its first attempt: nothing extra to say
    # The machine is reclaimed: Batch retries, and the step shows re-queued even though its last progress said running.
    reclaimed = [{"statusReason": "Host EC2 (instance i-0f44cde632015ca8a) terminated."}]
    jobs.handle_batch_event(db2, event("RUNNABLE", reclaimed))
    s = step()
    assert s["state"] == "queued" and s["note"].startswith("Re-queued (the machine it was running on was taken back by AWS); attempt 2 of 4")
    assert "(older event, ignored)" in jobs.handle_batch_event(db2, event("RUNNING"))  # attempt 1's event arriving late
    jobs.handle_batch_event(db2, event("RUNNING", reclaimed))
    assert step()["note"].startswith("Attempt 2 of 4")
    db.close()
    db2.close()


def test_custom_metadata_fields(client, synth_small):
    """Reviewers add project fields (per cohort or dataset); they validate, inherit from the cohort and count towards
    required fields like built-in ones. Keys, levels and types are fixed; choices only grow; fields are hidden, not
    removed, and hidden values are kept."""
    url = "/api/projects/hcm/metadata-fields"
    as_user(client, "demo@gwashub.org")  # a platform admin, but only an uploader in hcm: reviewer things are refused
    assert client.put(url, json={"fields": [{"label": "X", "level": "cohort", "type": "text"}]}).status_code == 403
    assert client.put("/api/projects/hcm/metadata-config", json={"shown": [], "required": []}).status_code == 403
    as_user(client, "tom@example.org")  # reviewer of hcm
    fields = [{"label": "Hospital data collected from", "level": "cohort", "type": "text"},
              {"label": "Echo protocol", "level": "dataset", "type": "select", "options": ["A", "B"]},
              {"label": "Genotyped centrally", "level": "cohort", "type": "yesno"}]
    r = client.put(url, json={"fields": fields})
    assert r.status_code == 200, r.text
    custom = r.json()["metadata_custom"]
    keys = {c["label"]: c["key"] for c in custom}
    hosp, echo = keys["Hospital data collected from"], keys["Echo protocol"]
    assert hosp == "custom_hospital_data_collected_from" and hosp in r.json()["metadata_config"]["shown"]  # new fields start shown
    assert [s["key"] for s in r.json()["metadata_custom_sections"]] == ["custom_cohort", "custom_dataset"]
    # Required like a built-in field.
    cfg = r.json()["metadata_config"]
    assert client.put("/api/projects/hcm/metadata-config", json={"shown": cfg["shown"], "required": [echo]}).status_code == 200
    entry = next(e for e in synth_small["manifest"] if e["effect_type"] == "beta")

    def post(meta, cohort="CustomCo"):
        m = {k: entry[k] for k in ("ancestry", "sex", "build", "column_mapping")} | {"cohort": cohort, "phenotype": "HCM",
             "effect_type": "beta", "n_cases": 100, "n_controls": 900, "study_meta": meta}
        with open(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "rb") as f:
            return client.post("/api/projects/hcm/datasets", files={"file": (entry["file"], f)}, data={"meta": json.dumps(m)})

    assert "Echo protocol" in post({hosp: "Leeds"}).json()["detail"]  # required, missing
    assert "must be one of" in post({hosp: "Leeds", echo: "C"}).json()["detail"]
    up = post({hosp: "Leeds", echo: "A", keys["Genotyped centrally"]: "Yes"})
    assert up.status_code == 201, up.text
    d = client.get(f"/api/datasets/{up.json()['id']}").json()
    assert d["cohort_meta"][hosp] == "Leeds" and d["study_meta"][echo] == "A"  # cohort-level field stored on the cohort
    # Rename and add a choice: fine. Change type, remove a choice, or drop a field: refused.
    custom[0]["label"], custom[1]["options"] = "Hospital records from", ["A", "B", "C"]
    assert client.put(url, json={"fields": custom}).status_code == 200
    assert "can't change" in client.put(url, json={"fields": [{**custom[0], "type": "number"}, *custom[1:]]}).json()["detail"]
    assert "not removed" in client.put(url, json={"fields": [custom[0], {**custom[1], "options": ["A"]}, custom[2]]}).json()["detail"]
    assert "only hidden" in client.put(url, json={"fields": custom[:2]}).json()["detail"]
    # Hide: off the shown/required settings, but the saved value stays and an edit still validates.
    custom[1]["hidden"] = True
    r = client.put(url, json={"fields": custom})
    assert echo not in r.json()["metadata_config"]["shown"] and echo not in r.json()["metadata_config"]["required"]
    d = client.get(f"/api/datasets/{up.json()['id']}").json()
    assert d["study_meta"][echo] == "A"
    assert client.patch(f"/api/datasets/{d['id']}", json={"study_meta": d["study_meta"]}).status_code == 200
