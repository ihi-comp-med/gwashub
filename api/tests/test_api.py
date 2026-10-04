"""API tests: a fresh local deployment per module (temp data dir, jobs run inline)."""
import importlib
import json
import os
import shutil

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


def upload(client, synth, entry, project="hermes3", **override):
    meta = {k: entry[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "n_cases", "n_controls", "column_mapping")}
    meta.update(override)
    with open(os.path.join(synth["dir"], "fake_gwas", entry["file"]), "rb") as f:
        r = client.post(f"/api/projects/{project}/datasets", files={"file": (entry["file"], f)}, data={"meta": json.dumps(meta)})
    assert r.status_code == 201, r.text
    return r.json()


def test_login_and_projects(client):
    assert client.post("/api/auth/login", json={"email": "nobody@x"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401
    as_user(client, "demo@gwashub.org")
    roles = {p["id"]: p["role"] for p in client.get("/api/projects").json()}
    assert roles == {"hermes3": "reviewer", "hcm": "uploader"}
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


def test_upload_runs_qc_and_enforces_dataset_permissions(client, synth_small):
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] in ("passed", "review") and d["run"]["status"] == "completed", d["run"]
    assert [s["state"] for s in d["run"]["steps"]].count("skipped") == 0
    assert client.get(f"/api/datasets/{up['id']}/files/qc").status_code == 200
    assert client.get(f"/api/datasets/{up['id']}/files/report").text.startswith("<!doctype html>")
    art = client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": "steps/08_plots/manhattan.png"})
    assert art.status_code == 200 and art.headers["content-type"] == "image/png"
    assert client.get(f"/api/datasets/{up['id']}/files/artifact", params={"path": "../../../gwashub.db"}).status_code == 404

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
    mapping = {k: v for k, v in entry["column_mapping"].items() if k != "se"}
    up = upload(client, synth_small, entry, column_mapping=mapping)
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] == "failed" and d["run"]["status"] == "blocked"
    assert client.get(f"/api/datasets/{up['id']}/files/qc").status_code == 409
    assert client.get(f"/api/datasets/{up['id']}/files/report").status_code == 200  # report still produced


def test_summary_counts(client):
    as_user(client, "mei@example.org")
    s = client.get("/api/projects/hermes3/summary", params={"by": "ancestry"}).json()
    assert s["totals"]["total"] == 2 and s["totals"]["failed"] == 1 and s["totals"]["passed"] == 1


def test_pipeline_permissions_and_versions(client):
    as_user(client, "jonas@example.org")
    p = client.get("/api/projects/hermes3/pipeline").json()
    assert p["version"] == 1 and p["role"] == "uploader"
    assert any(c["key"] == "gwashub/maf_filter" for c in p["catalog"])
    assert {r["key"] for r in p["resources"]} >= {"hg19ToHg38.over.chain.gz", "ref_panel_grch38.tsv.gz"}
    steps = p["steps"][:4] + [{"step": "gwashub/maf_filter", "params": {"maf_threshold": 0.05}}] + p["steps"][4:]
    assert client.put("/api/projects/hermes3/pipeline", json={"steps": steps}).status_code == 403
    as_user(client, "ama@example.org")
    assert client.put("/api/projects/hermes3/pipeline", json={"steps": [{"step": "gwashub/nope"}]}).status_code == 422
    r = client.put("/api/projects/hermes3/pipeline", json={"steps": steps, "note": "MAF 5%"})
    assert r.json()["version"] == 2
    assert client.get("/api/projects/hermes3/pipeline").json()["history"][0]["note"] == "MAF 5%"


def test_rerun_uses_new_pipeline_version(client):
    as_user(client, "ama@example.org")
    ds = client.get("/api/projects/hermes3/datasets").json()
    ok = next(d for d in ds if d["status"] == "passed")
    assert client.post(f"/api/datasets/{ok['id']}/rerun").status_code == 200
    d = client.get(f"/api/datasets/{ok['id']}").json()
    assert d["run"]["pipeline_version"] == 2
    assert any(s["key"] == "gwashub/maf_filter" for s in d["run"]["steps"])
    as_user(client, "jonas@example.org")
    assert client.post(f"/api/datasets/{ok['id']}/rerun").status_code == 403


def test_code_viewer_files(client):
    as_user(client, "jonas@example.org")
    r = client.get("/api/projects/hermes3/steps/files", params={"key": "gwashub/liftover"}).json()
    assert r["files"][0]["path"] == "pipeline/qc_steps/gwashub/liftover/liftover.py"
    assert any(f["path"] == "pipeline/qc_steps/_lib/chain.py" for f in r["files"])
    as_user(client, "demo@gwashub.org")
    r = client.get("/api/projects/hcm/steps/files", params={"key": "hcm/sarcomere_filter"}).json()
    assert r["files"][0]["path"] == "pipeline/qc_steps/hcm/sarcomere_filter/sarcomere_filter.R" and "data.table" in r["files"][0]["content"]


def test_pipeline_lists_consortium_steps_and_images(client):
    as_user(client, "tom@example.org")
    pl = client.get("/api/projects/hcm/pipeline").json()
    step = next(c for c in pl["catalog"] if c["key"] == "hcm/sarcomere_filter")
    assert step["owner"] == "hcm" and step["source"] == "consortium" and step["image"] == "gwashub/base-r"
    assert {"gwashub/base", "gwashub/base-r"} <= {i["ref"] for i in pl["images"]}
    # The HCM pipeline (R step in the middle) is three image groups.
    assert [g["image"] for g in pl["groups"]] == ["gwashub/base", "gwashub/base-r", "gwashub/base"]
    assert client.post("/api/projects/hcm/custom-steps", json={}).status_code in (404, 405)  # git-repo steps are gone
    bad = client.put("/api/projects/hcm/pipeline", json={"steps": [{"step": "gwashub/row_check", "image": "nobody/nothing"}]})
    assert bad.status_code == 422 and "unknown image" in bad.json()["detail"]


@pytest.mark.skipif(not shutil.which("Rscript"), reason="needs R")
def test_hcm_pipeline_runs_the_r_step(client, synth_small):
    as_user(client, "demo@gwashub.org")  # uploader in HCM
    up = upload(client, synth_small, synth_small["manifest"][0], project="hcm", phenotype="HCM")
    d = client.get(f"/api/datasets/{up['id']}").json()
    step = next(s for s in d["run"]["steps"] if s["key"] == "hcm/sarcomere_filter")
    assert step["state"] == "pass" and step["metrics"]["regions"] == 2 and step["image"] == "gwashub/base-r"


def test_resources_upload(client, tmp_path):
    f = tmp_path / "exclude.bed"
    f.write_text("chr6\t28000000\t34000000\tMHC\n")
    meta = {"key": "exclude.bed", "title": "Exclusions", "build": "GRCh38", "attest": True}
    as_user(client, "jonas@example.org")
    assert client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)}).status_code == 403
    as_user(client, "ama@example.org")
    assert client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))},
                       data={"meta": json.dumps({**meta, "attest": False})}).status_code == 422
    r1 = client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)})
    r2 = client.post("/api/projects/hermes3/resources", files={"file": ("exclude.bed", f.open("rb"))}, data={"meta": json.dumps(meta)})
    assert (r1.json()["version"], r2.json()["version"]) == (1, 2)
    shared = client.post("/api/projects/hermes3/resources", files={"file": ("x", f.open("rb"))},
                         data={"meta": json.dumps({**meta, "key": "ref_panel_grch38.tsv.gz"})})
    assert shared.status_code == 409


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
    r = client.post("/api/projects/hermes3/meta", json={"name": "HF all", "dataset_ids": eligible, "settings": {"method": "random"}})
    assert r.status_code == 201
    ma = client.get(f"/api/meta/{r.json()['id']}").json()
    assert ma["status"] == "completed", ma["error"]
    assert ma["summary"]["n_datasets"] == len(eligible) and ma["summary"]["n_loci"] >= 3
    assert ma["lead_loci"] and ma["forest"] and ma["plot_data"]["manhattan"]["x"]
    assert ma["settings"]["method"] == "random"
    assert client.get(f"/api/meta/{ma['id']}/files/meta_results.tsv.gz").status_code == 200

    as_user(client, "jonas@example.org")
    assert client.get(f"/api/meta/{ma['id']}").status_code == 200  # uploaders can view results
    assert client.get(f"/api/meta/{ma['id']}/files/manhattan.png").status_code == 200
    assert client.get(f"/api/meta/{ma['id']}/files/meta_results.tsv.gz").status_code == 403
    assert client.put("/api/projects/hermes3/meta/settings", json={"method": "random"}).status_code == 403
    as_user(client, "ama@example.org")
    s = client.put("/api/projects/hermes3/meta/settings", json={"method": "random", "maf_threshold": 0.05}).json()["settings"]
    assert s["method"] == "random" and s["maf_threshold"] == 0.05
    assert client.put("/api/projects/hermes3/meta/settings", json={"method": "bayes"}).status_code == 422


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
    assert validate({"case_type": "Incident", "n_variants_for_imputation": "40000", "contact_person": " "}) == {"case_type": "Incident", "n_variants_for_imputation": 40000}

    as_user(client, "jonas@example.org")
    assert len(client.get("/api/metadata-schema").json()["sections"]) == 6
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
    run_dir = os.path.join(settings().data_dir, "runs", "qc", str(d["run"]["id"]))
    upload_dir = os.path.join(settings().storage_dir, "uploads", "hermes3", str(mine["id"]))
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
    assert r.status_code == 200, r.text
    d = client.get(f"/api/datasets/{up['id']}").json()
    assert d["status"] in ("passed", "review") and d["run"]["status"] == "completed", d["run"]
    assert client.get(f"/api/datasets/{up['id']}/files/raw").content == data
    assert client.post(f"/api/datasets/{up['id']}/upload/complete", json={"parts": parts}).status_code == 409


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


def test_grouped_batch_events(client, synth_small):
    """A run of three Batch jobs: an early group's success doesn't finish it; any group's failure fails it."""
    from gwashub_api import jobs
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import QCRun
    as_user(client, "jonas@example.org")
    up = upload(client, synth_small, synth_small["manifest"][0])
    db = SessionLocal()
    try:
        done = db.get(QCRun, client.get(f"/api/datasets/{up['id']}").json()["run"]["id"])
        run = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="queued",
                    run_dir=done.run_dir, batch_job_id="last-job")
        db.add(run)
        db.commit()
        ev = lambda job, k, status, **kw: {"detail": {"jobId": job, "jobName": f"gwashub-qc-{run.id}-g{k}", "status": status, **kw}}  # noqa: E731
        assert "running" in jobs.handle_batch_event(db, ev("first-job", 0, "RUNNING"))
        assert "finished a group" in jobs.handle_batch_event(db, ev("first-job", 0, "SUCCEEDED"))
        db.refresh(run)
        assert run.status == "running"
        assert "recorded: completed" in jobs.handle_batch_event(db, ev("last-job", 2, "SUCCEEDED"))

        run2 = QCRun(dataset_id=done.dataset_id, pipeline_version_id=done.pipeline_version_id, status="running",
                     run_dir=done.run_dir, batch_job_id="r2-last")
        db.add(run2)
        db.commit()
        jobs.handle_batch_event(db, {"detail": {"jobId": "r2-mid", "jobName": f"gwashub-qc-{run2.id}-g1", "status": "FAILED",
                                                "statusReason": "OutOfMemoryError: Container killed due to memory usage"}})
        db.refresh(run2)
        assert run2.status == "failed" and "OutOfMemoryError" in run2.error
        # The dependent last job then fails too; that's ignored, the first reason stays.
        assert "already recorded" in jobs.handle_batch_event(db, {"detail": {"jobId": "r2-last", "jobName": f"gwashub-qc-{run2.id}-g2",
                                                                             "status": "FAILED", "statusReason": "Dependent Job failed"}})
    finally:
        db.close()


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
