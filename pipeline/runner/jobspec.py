"""Run a job from a JSON spec: a QC run or a meta-analysis.

The same code runs in a thread of the local API and inside an AWS Batch job
(`python -m pipeline.runner.aws.batch_entry s3://.../spec.json`). It never touches the database:
inputs, resources and outputs are *locations* (local paths or s3:// URIs), and the outcome is written
to `<output>/result.json`, which the API (locally) or the worker (AWS) records.

QC spec:
    {"kind": "qc", "input": LOC, "params": {...}, "steps": [{"step": ...}, ...],
     "resources": {key: LOC}, "output": LOC, "groups": [{"image": "gwashub/base", "steps": [0, 1, ...]}, ...]}
Meta-analysis spec:
    {"kind": "meta", "inputs": [{id, label, loc, n_cases, n_controls, ancestry}], "params": {...}, "output": LOC}

On AWS each QC *group* (consecutive steps sharing an image) is its own Batch job, run in that image
and chained with Batch job dependencies. A group picks up where the previous one stopped: the working
file and the run state are passed through <output>/_state/, and earlier steps' outputs are fetched
so that later steps (e.g. the report) can read them. Locally all groups run in one go.
"""
import json
import os
import posixpath
import shutil
import subprocess
import sys

from pipeline.runner import executor
from pipeline.runner.catalog import QC_STEPS_DIR, load_steps
from pipeline.runner.pipeline import entries_from_dicts, plan_groups, resolve

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Not published: the working copy (same as gwas_qc.tsv), per-step links to reference files, and the
# hand-over state between groups (published separately, under _state/).
NOT_PUBLISHED = ("working.tsv", "state.json", "resources/", "_resources/", "_state/")


# ---------- locations ----------

def _s3():
    import boto3
    if not hasattr(_s3, "client"):
        _s3.client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION")))
    return _s3.client


def _split(loc):
    bucket, _, key = loc[len("s3://"):].partition("/")
    return bucket, key


def _join(loc, *parts):
    return posixpath.join(loc, *parts) if loc.startswith("s3://") else os.path.join(loc, *parts)


def fetch(loc, dest):
    """Local copy of a location: S3 download, or a symlink to a local file."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if loc.startswith("s3://"):
        _s3().download_file(*_split(loc), dest)
    elif not os.path.lexists(dest):
        os.symlink(os.path.abspath(loc), dest)
    return dest


def put_json(obj, loc):
    data = json.dumps(obj, indent=2, default=str)
    if loc.startswith("s3://"):
        bucket, key = _split(loc)
        _s3().put_object(Bucket=bucket, Key=key, Body=data.encode(), ContentType="application/json")
    else:
        os.makedirs(os.path.dirname(loc), exist_ok=True)
        with open(loc, "w") as f:
            f.write(data)


def publish(local_dir, loc):
    """Upload a run's outputs (S3), skipping working files. Local outputs are already in place."""
    if not loc.startswith("s3://"):
        return
    bucket, prefix = _split(loc)
    for dirpath, _, files in os.walk(local_dir):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, local_dir).replace(os.sep, "/")
            if rel in NOT_PUBLISHED or rel.startswith(NOT_PUBLISHED) or os.path.islink(full):
                continue
            _s3().upload_file(full, bucket, posixpath.join(prefix, rel))


# ---------- QC ----------

def resolve_spec(spec):
    return resolve(entries_from_dicts(spec["steps"]), load_steps(QC_STEPS_DIR))


def groups_of(spec, resolved):
    return spec.get("groups") or plan_groups(resolved)


def _put_file(path, loc):
    if loc.startswith("s3://"):
        _s3().upload_file(path, *_split(loc))
    elif os.path.abspath(path) != os.path.abspath(loc):
        os.makedirs(os.path.dirname(loc), exist_ok=True)
        shutil.copyfile(path, loc)


def _fetch_outputs(output, local_out):
    """Earlier groups' published outputs (step logs, plots, ...) and the hand-over state."""
    bucket, prefix = _split(output)
    prefix = prefix.rstrip("/") + "/"
    for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            rel = o["Key"][len(prefix):]
            if rel in ("spec.json", "result.json"):
                continue
            dest = os.path.join(local_out, rel[len("_state/"):] if rel.startswith("_state/") else rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _s3().download_file(bucket, o["Key"], dest)


def run_qc(spec, workdir, on_step=None, group=None):
    """Run a QC spec: every group (group=None, local) or just one group (a Batch job).
    Returns the result dict once the last group has run, else the hand-over state."""
    output = spec["output"]
    local_out = output if not output.startswith("s3://") else os.path.join(workdir, "out")
    os.makedirs(local_out, exist_ok=True)
    resolved = resolve_spec(spec)
    groups = groups_of(spec, resolved)
    todo = list(range(len(groups))) if group is None else [group]
    if any(not 0 <= g < len(groups) for g in todo):
        raise ValueError(f"group {group} not in this pipeline ({len(groups)} groups)")

    # Fetch only the resources the steps of this job declare.
    indexes = {i for g in todo for i in groups[g]["steps"]}
    needed = {k for e in resolved if e.index in indexes for k in e.step.resources}
    res_dir = os.path.join(workdir, "_resources")
    os.makedirs(res_dir, exist_ok=True)
    for key, loc in (spec.get("resources") or {}).items():
        if key in needed:
            fetch(loc, os.path.join(res_dir, key))

    if todo[0] == 0:
        name = os.path.basename(spec["input"].rstrip("/")) or "input"
        state = executor.start_run(fetch(spec["input"], os.path.join(workdir, "_input", name)), local_out)
        progress = [{"key": e.step.key, "title": e.step.title, "image": e.image, "state": "queued"} for e in resolved]
    else:
        _fetch_outputs(output, local_out)
        with open(os.path.join(local_out, "state.json")) as f:
            state = json.load(f)
        with open(os.path.join(local_out, "progress.json")) as f:
            progress = json.load(f)
    put_json(progress, _join(output, "progress.json"))

    def step_done(i, st, res):
        progress[i]["state"] = st
        if res:
            progress[i]["messages"] = res.get("messages", [])[:2]
        put_json(progress, _join(output, "progress.json"))
        if on_step:
            on_step(i, st, res)

    for g in todo:
        executor.run_steps(resolved, state, spec["params"], res_dir, local_out, indexes=set(groups[g]["steps"]), on_step=step_done)

    if todo[-1] == len(groups) - 1:
        result = executor.finish_run(state, local_out)
        if spec.get("images"):  # exactly which image builds the run used (digests on AWS)
            result["images"] = spec["images"]
            with open(os.path.join(local_out, "result.json"), "w") as f:
                json.dump(result, f, indent=2, default=str)
        publish(local_out, output)  # includes result.json
        if output.startswith("s3://") and todo[0] > 0:  # the hand-over copy is no longer needed
            bucket, prefix = _split(_join(output, "_state"))
            keys = [{"Key": o["Key"]} for o in _s3().list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])]
            if keys:
                _s3().delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
        return result
    # Hand over to the next group's job.
    with open(os.path.join(local_out, "state.json"), "w") as f:
        json.dump(state, f, default=str)
    publish(local_out, output)
    for fn in ("working.tsv", "state.json", "step_logs.jsonl"):
        _put_file(os.path.join(local_out, fn), _join(output, "_state", fn))
    return state


# ---------- meta-analysis ----------

def run_meta(spec, workdir):
    output = spec["output"]
    local_out = output if not output.startswith("s3://") else os.path.join(workdir, "out")
    os.makedirs(local_out, exist_ok=True)
    inputs = []
    for i, item in enumerate(spec["inputs"]):
        path = fetch(item["loc"], os.path.join(workdir, "_inputs", f"{i:03d}_{item['id']}.tsv"))
        inputs.append({k: v for k, v in item.items() if k != "loc"} | {"path": path})
    with open(os.path.join(local_out, "inputs.json"), "w") as f:
        json.dump(inputs, f, indent=2)
    with open(os.path.join(local_out, "params.json"), "w") as f:
        json.dump(spec["params"], f, indent=2)
    env = {**os.environ, "PYTHONPATH": QC_STEPS_DIR, "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR", "/tmp/gwashub-mpl")}
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "analyses", "meta_analysis", "meta_analysis.py"),
                           "--inputs", os.path.join(local_out, "inputs.json"), "--params", os.path.join(local_out, "params.json"),
                           "--out", local_out, "--log", os.path.join(local_out, "log.json")],
                          env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        result = {"status": "failed", "error": (proc.stderr[-2000:] or proc.stdout[-2000:])}
    else:
        with open(os.path.join(local_out, "summary.json")) as f:
            summary = json.load(f)
        with open(os.path.join(local_out, "log.json")) as f:
            log = json.load(f)
        result = {"status": "completed", "summary": summary, "log": log}
    with open(os.path.join(local_out, "result.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)
    publish(local_out, output)
    return result


def run_spec(spec, workdir, on_step=None, group=None):
    os.makedirs(workdir, exist_ok=True)
    try:
        if spec["kind"] == "qc":
            return run_qc(spec, workdir, on_step, group)
        if spec["kind"] == "meta":
            return run_meta(spec, workdir)
        raise ValueError(f"unknown job kind {spec['kind']!r}")
    finally:
        if spec["output"].startswith("s3://"):
            shutil.rmtree(workdir, ignore_errors=True)
