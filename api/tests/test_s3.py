"""S3 storage and a full QC job through S3, against moto (no AWS account needed)."""
import json
import os

import boto3
import pytest

moto = pytest.importorskip("moto")


@pytest.fixture
def s3(monkeypatch, tmp_path):
    from gwashub_api import config, storage
    env = {"GWASHUB_STORAGE": "s3", "GWASHUB_UPLOADS_BUCKET": "gh-up", "GWASHUB_RESULTS_BUCKET": "gh-res",
           "GWASHUB_RESOURCES_BUCKET": "gh-ref", "AWS_REGION": "eu-west-2", "AWS_ACCESS_KEY_ID": "test",
           "AWS_SECRET_ACCESS_KEY": "test", "GWASHUB_DATA_DIR": str(tmp_path / "data")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.settings.cache_clear()
    with moto.mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        for b in ("gh-up", "gh-res", "gh-ref"):
            client.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
        for mod in (storage, __import__("pipeline.runner.jobspec", fromlist=["x"])):
            mod._s3.__dict__.pop("client", None)
        yield client
        for mod in (storage, __import__("pipeline.runner.jobspec", fromlist=["x"])):
            mod._s3.__dict__.pop("client", None)
    config.settings.cache_clear()


def test_storage_roundtrip(s3, tmp_path):
    import io

    from gwashub_api import storage
    loc, size, sha = storage.save_stream("uploads", "uploads/p/1/a.tsv", io.BytesIO(b"hello"))
    assert loc == "s3://gh-up/uploads/p/1/a.tsv" and size == 5 and len(sha) == 64
    assert storage.read_bytes(loc) == b"hello" and storage.exists(loc)
    url = storage.download_url(loc, "a b.tsv")
    assert url.startswith("https://gh-up.s3.") and "X-Amz-Signature" in url and "a%2520b.tsv" in url
    with pytest.raises(ValueError):
        storage.location("uploads", "../x")

    import gzip
    rows = "".join(f"{i}\n" for i in range(1000))
    s3.put_object(Bucket="gh-res", Key="qc/t.tsv.gz", Body=gzip.compress(rows.encode()))
    assert storage.head_lines("s3://gh-res/qc/t.tsv.gz", 3) == "0\n1\n2\n"  # streamed, gunzipped, first lines only
    assert storage.head_lines("s3://gh-res/qc/missing.tsv", 3) is None

    big = os.urandom(5 * 1024 * 1024 + 10)
    dest = storage.location("uploads", "uploads/p/2/big.bin")
    uid = storage.start_multipart(dest)
    assert "partNumber=1" in storage.part_url(dest, uid, 1, 2)
    etags = [s3.upload_part(Bucket="gh-up", Key="uploads/p/2/big.bin", UploadId=uid, PartNumber=n, Body=b)["ETag"]
             for n, b in ((1, big[:5 * 1024 * 1024]), (2, big[5 * 1024 * 1024:]))]
    assert storage.complete_multipart(dest, uid, [{"n": 2, "etag": etags[1]}, {"n": 1, "etag": etags[0]}]) == len(big)

    storage.delete_prefix("s3://gh-up/uploads/p")
    assert not storage.exists(loc) and not storage.exists(dest)


def test_qc_job_through_s3(s3, synth_small, tmp_path):
    """What a Batch job does: spec in, input and resources from S3, outputs and result.json back to S3."""
    from pipeline.runner import jobspec
    from gwashub_api.services import default_template
    entry = synth_small["manifest"][0]
    s3.upload_file(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "gh-up", f"uploads/p/1/{entry['file']}")
    resources = {}
    for key in os.listdir(synth_small["resources"]):
        s3.upload_file(os.path.join(synth_small["resources"], key), "gh-ref", f"resources/shared/{key}/v1/{key}")
        resources[key] = f"s3://gh-ref/resources/shared/{key}/v1/{key}"
    params = {k: entry[k] for k in ("column_mapping", "build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")}
    spec = {"kind": "qc", "input": f"s3://gh-up/uploads/p/1/{entry['file']}", "params": params, "steps": default_template(),
            "custom_steps": [], "resources": resources, "output": "s3://gh-res/qc/7"}
    s3.put_object(Bucket="gh-res", Key="qc/7/spec.json", Body=json.dumps(spec).encode())

    from pipeline.runner.aws import batch_entry
    batch_entry.main(["s3://gh-res/qc/7/spec.json"])

    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="gh-res", Prefix="qc/7/")["Contents"]}
    assert {"qc/7/result.json", "qc/7/gwas_qc.tsv", "qc/7/progress.json"} <= keys
    assert not any("/resources/" in k or k.endswith("working.tsv") for k in keys)  # reference files not copied per run
    result = json.loads(s3.get_object(Bucket="gh-res", Key="qc/7/result.json")["Body"].read())
    assert result["status"] == "completed", (result["stop_reason"], [(s["key"], s["state"], s.get("messages", [])[:2]) for s in result["steps"]])
    progress = json.loads(s3.get_object(Bucket="gh-res", Key="qc/7/progress.json")["Body"].read())
    assert all(p["state"] in ("pass", "warn") for p in progress)
    assert f"qc/7/{result['outputs']['qc_report']}" in keys


def test_qc_in_groups_matches_one_job(s3, synth_small, tmp_path):
    """Batch runs one job per image group; split the default pipeline in three and check the
    hand-over gives the same result as a single job, also when a group's job is a second attempt."""
    from pipeline.runner.aws import batch_entry
    from gwashub_api.services import default_template
    entry = synth_small["manifest"][0]
    s3.upload_file(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "gh-up", f"uploads/p/2/{entry['file']}")
    resources = {}
    for key in os.listdir(synth_small["resources"]):
        s3.upload_file(os.path.join(synth_small["resources"], key), "gh-ref", f"resources/shared/{key}/v1/{key}")
        resources[key] = f"s3://gh-ref/resources/shared/{key}/v1/{key}"
    params = {k: entry[k] for k in ("column_mapping", "build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")}
    steps = default_template()
    n = len(steps)
    base = {"kind": "qc", "input": f"s3://gh-up/uploads/p/2/{entry['file']}", "params": params, "steps": steps, "resources": resources}
    one = {**base, "output": "s3://gh-res/qc/20"}
    split = {**base, "output": "s3://gh-res/qc/21", "images": {"gwashub/base": "test-digest"},
             "groups": [{"image": "gwashub/base", "steps": [0, 1, 2]}, {"image": "gwashub/base", "steps": list(range(3, n - 1))},
                        {"image": "gwashub/base", "steps": [n - 1]}]}
    for spec in (one, split):
        s3.put_object(Bucket="gh-res", Key=spec["output"][len("s3://gh-res/"):] + "/spec.json", Body=json.dumps(spec).encode())
    batch_entry.main(["s3://gh-res/qc/20/spec.json"])
    for g in range(3):
        if g != 1:
            batch_entry.main(["s3://gh-res/qc/21/spec.json", "--group", str(g)])
        else:
            # As a reclaimed first attempt leaves it: the group's later steps still "running", with old output. The
            # next attempt starts at the group's first step, and shows the others as queued, not running.
            from pipeline.runner import jobspec
            stale = json.loads(s3.get_object(Bucket="gh-res", Key="qc/21/progress.json")["Body"].read())
            for i in split["groups"][1]["steps"][1:]:
                stale[i].update(state="running", tail={"lines": ["old output"], "at": "2026-10-12T16:04:26+00:00"}, messages=["old"])
            s3.put_object(Bucket="gh-res", Key="qc/21/progress.json", Body=json.dumps(stale).encode())
            first_running = []

            def on_step(i, state, res):
                if state == "running" and not first_running:
                    first_running.append(json.loads(s3.get_object(Bucket="gh-res", Key="qc/21/progress.json")["Body"].read()))
            jobspec.run_spec(split, str(tmp_path / "work-g1"), on_step=on_step, group=1)
            seen = first_running[0]
            later = split["groups"][1]["steps"][1:]
            assert all(seen[i]["state"] == "queued" and "tail" not in seen[i] and "messages" not in seen[i] for i in later)
        keys = {o["Key"] for o in s3.list_objects_v2(Bucket="gh-res", Prefix="qc/21/")["Contents"]}
        assert ("qc/21/result.json" in keys) == (g == 2)  # only the last group finishes the run
        assert any(k.startswith("qc/21/_state/") for k in keys) == (g < 2)  # hand-over state removed at the end
        # Live progress: every step of the groups run so far is finished (a group's upload must not set them back).
        progress = json.loads(s3.get_object(Bucket="gh-res", Key="qc/21/progress.json")["Body"].read())
        done = [i for gg in split["groups"][:g + 1] for i in gg["steps"]]
        assert all(progress[i]["state"] not in ("queued", "running") for i in done), [(p["key"], p["state"]) for p in progress]
        if g == 0:  # a finished step's whole record, with its files there to open, before the run ends
            parse = progress[0]
            assert len(parse["messages"]) > 2 and parse["artifacts"] and "metrics" in parse
            assert all(f"qc/21/{parse['dir']}/{a['path']}" in keys for a in parse["artifacts"])

    a = json.loads(s3.get_object(Bucket="gh-res", Key="qc/20/result.json")["Body"].read())
    b = json.loads(s3.get_object(Bucket="gh-res", Key="qc/21/result.json")["Body"].read())
    assert b["images"] == {"gwashub/base": "test-digest"}
    assert [(s["key"], s["state"], s.get("rows_after")) for s in a["steps"]] == [(s["key"], s["state"], s.get("rows_after")) for s in b["steps"]]
    assert a["overall_verdict"] == b["overall_verdict"] and a["outputs"]["rows"] == b["outputs"]["rows"]
    qc_a = s3.get_object(Bucket="gh-res", Key="qc/20/gwas_qc.tsv")["Body"].read()
    assert qc_a == s3.get_object(Bucket="gh-res", Key="qc/21/gwas_qc.tsv")["Body"].read()
    report = s3.get_object(Bucket="gh-res", Key=f"qc/21/{b['outputs']['qc_report']}")["Body"].read().decode()
    assert "Manhattan" in report or "manhattan" in report  # the report (last group) saw earlier groups' plots


def test_step_files_published_as_each_step_finishes(s3, synth_small, tmp_path):
    """A Batch job uploads each step's folder when the step finishes, not only when the job ends, so the website can
    open a finished step's files while later steps run."""
    from pipeline.runner import jobspec
    from gwashub_api.services import default_template
    entry = synth_small["manifest"][0]
    s3.upload_file(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "gh-up", f"uploads/p/3/{entry['file']}")
    params = {k: entry[k] for k in ("column_mapping", "build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")}
    steps = default_template()[:2]  # parse and filter: no reference files needed
    spec = {"kind": "qc", "input": f"s3://gh-up/uploads/p/3/{entry['file']}", "params": params, "steps": steps,
            "resources": {}, "output": "s3://gh-res/qc/30"}
    seen = {}

    def on_step(i, state, res):
        if res:  # the moment a step finishes: its files and its full record are already published
            keys = {o["Key"] for o in s3.list_objects_v2(Bucket="gh-res", Prefix=f"qc/30/{res['dir']}/").get("Contents", [])}
            progress = json.loads(s3.get_object(Bucket="gh-res", Key="qc/30/progress.json")["Body"].read())
            seen[i] = (keys, progress[i], res)

    jobspec.run_spec(spec, str(tmp_path / "work"), on_step=on_step)
    keys, rec, res = seen[0]
    assert f"qc/30/{res['dir']}/step_log.json" in keys
    assert all(f"qc/30/{res['dir']}/{a['path']}" in keys for a in res.get("artifacts") or [])
    assert rec["messages"] == res["messages"] and rec.get("artifacts") == res.get("artifacts")


def test_blocked_run_skips_to_the_report(s3, synth_small):
    """After a block, the hand-over names the report's group, not the next one: the groups between never start a job,
    and their steps show as skipped at once."""
    from pipeline.runner.aws import batch_entry
    from gwashub_api.services import default_template
    entry = synth_small["manifest"][0]
    s3.upload_file(os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), "gh-up", f"uploads/p/4/{entry['file']}")
    params = {k: entry[k] for k in ("build", "ancestry", "effect_type", "n_cases", "n_controls", "cohort", "phenotype", "sex")}
    params["column_mapping"] = {k: v for k, v in entry["column_mapping"].items() if k != "se"}  # parse fails and blocks
    steps = default_template()
    n = len(steps)
    groups = [{"image": "gwashub/base", "steps": [0, 1, 2]}, {"image": "gwashub/base", "steps": list(range(3, n - 1))},
              {"image": "gwashub/base", "steps": [n - 1]}]
    spec = {"kind": "qc", "input": f"s3://gh-up/uploads/p/4/{entry['file']}", "params": params, "steps": steps,
            "resources": {}, "output": "s3://gh-res/qc/40", "groups": groups}
    s3.put_object(Bucket="gh-res", Key="qc/40/spec.json", Body=json.dumps(spec).encode())
    batch_entry.main(["s3://gh-res/qc/40/spec.json", "--group", "0"])
    state = json.loads(s3.get_object(Bucket="gh-res", Key="qc/40/_state/state.json")["Body"].read())
    assert state["status"] == "blocked" and state["next_group"] == 2  # group 1 is skipped, not run
    progress = json.loads(s3.get_object(Bucket="gh-res", Key="qc/40/progress.json")["Body"].read())
    assert progress[0]["state"] == "fail" and all(p["state"] == "skipped" for p in progress[1:n - 1])
    batch_entry.main(["s3://gh-res/qc/40/spec.json", "--group", "2"])
    result = json.loads(s3.get_object(Bucket="gh-res", Key="qc/40/result.json")["Body"].read())
    assert result["status"] == "blocked" and [s["state"] for s in result["steps"]][1:n - 1] == ["skipped"] * (n - 2)
    assert result["steps"][-1]["state"] in ("pass", "warn")  # the report ran

