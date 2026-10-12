"""Run a job from a JSON spec: a QC run or a meta-analysis.

The same code runs in a thread of the local API and inside an AWS Batch job
(`python -m pipeline.runner.aws.batch_entry s3://.../spec.json`). It never touches the database:
inputs, resources and outputs are *locations* (local paths or s3:// URIs), and the outcome is written
to `<output>/result.json`, which the API (locally) or the worker (AWS) records.

QC spec:
    {"kind": "qc", "input": LOC, "params": {...}, "steps": [{"step": ...}, ...],
     "resources": {key: LOC}, "output": LOC, "groups": [{"image": "gwashub/base", "steps": [0, 1, ...]}, ...]}
Meta-analysis spec:
    {"kind": "meta", "inputs": [{id, label, loc, n_cases, n_controls, n_total, ancestry}], "params": {...}, "output": LOC}

On AWS each QC *group* (consecutive steps sharing an image) is its own Batch job, run in that image
and chained with Batch job dependencies. A group picks up where the previous one stopped: the working
file and the run state are passed through <output>/_state/, and earlier steps' outputs are fetched
so that later steps (e.g. the report) can read them. Locally all groups run in one go.
"""
import datetime
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import uuid

from pipeline.runner import executor
from pipeline.runner.catalog import QC_STEPS_DIR, load_steps
from pipeline.runner.pipeline import entries_from_dicts, plan_groups, resolve

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Not published: the working copy (same as gwas_qc.tsv), per-step links to reference files, and the
# hand-over state between groups (published separately, under _state/).
NOT_PUBLISHED = ("working.tsv", "state.json", "resources/", "_resources/", "_state/")
# A meta-analysis method's scratch folders in its OUT_DIR (gwama_work/, metal_work/, publish.py's _loci_work/): copies
# of every input dataset and the program's raw output. Never published, and removed once the method has finished.
SCRATCH_SUFFIX = "_work"


def _scratch(rel: str) -> bool:
    return rel.split("/", 1)[0].endswith(SCRATCH_SUFFIX) and "/" in rel


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
            if rel in NOT_PUBLISHED or rel.startswith(NOT_PUBLISHED) or _scratch(rel) or os.path.islink(full):
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


# ---------- the running step's last output lines ----------

TAIL_LINES, TAIL_WIDTH = 5, 300
TAIL_SECONDS = float(os.environ.get("GWASHUB_TAIL_SECONDS", "15"))  # how often the running step's tail is published
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def tail_lines(path, n=TAIL_LINES, width=TAIL_WIDTH) -> list[str]:
    """The last n lines a step printed, made safe to show: colour codes removed, a line rewritten with \\r reduced to
    what it last said (progress bars), each line cut to width characters. [] when there's nothing yet."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = [_ANSI.sub("", ln.split("\r")[-1]).rstrip() for ln in text.split("\n")]
    lines = [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines if ln.strip()]
    return lines[-n:]


def latest_log(step_dir):
    """The step's console (output.txt) or one of its own *.log files, whichever changed last: harmonise_liftover's
    progress, for one, goes to gwas2vcf.log and bcftools.log. Never a table or other output."""
    try:
        names = [n for n in os.listdir(step_dir) if n == "output.txt" or n.endswith(".log")]
    except OSError:
        return None
    paths = [os.path.join(step_dir, n) for n in names if os.path.getsize(os.path.join(step_dir, n)) > 0]
    return max(paths, key=os.path.getmtime) if paths else None


class TailPublisher:
    """While a job runs, every TAIL_SECONDS puts the running step's last output lines into progress.json (as "tail":
    {lines, at}), so the website can show what the step is doing. Never touches the step itself: any error here is
    ignored. Shares a lock with the code that records finished steps."""

    def __init__(self, progress, local_out, resolved, save, lock):
        self.progress, self.local_out, self.resolved, self.save, self.lock = progress, local_out, resolved, save, lock
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop.wait(TAIL_SECONDS):
            try:
                self.publish_once()
            except Exception:
                pass  # the tail is a convenience: never let it affect the run

    def publish_once(self):
        with self.lock:
            for i, p in enumerate(self.progress):
                if p.get("state") != "running":
                    continue
                e = self.resolved[i]
                source = latest_log(os.path.join(self.local_out, "steps", f"{e.index + 1:02d}_{e.name}"))
                lines = tail_lines(source) if source else []
                if lines and lines != (p.get("tail") or {}).get("lines"):
                    p["tail"] = {"lines": lines, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
                    self.save()


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
    needed = {k for e in resolved if e.index in indexes for k in e.step.all_resources}
    res_dir = os.path.join(workdir, "_resources")
    os.makedirs(res_dir, exist_ok=True)
    for key, loc in (spec.get("resources") or {}).items():
        if key in needed:
            fetch(loc, os.path.join(res_dir, key))

    if todo[0] == 0:
        name = os.path.basename(spec["input"].rstrip("/")) or "input"
        state = executor.start_run(fetch(spec["input"], os.path.join(workdir, "_input", name)), local_out, spec["params"])
        progress = [{"key": e.step.key, "title": e.step.title, "image": e.image, "state": "queued"} for e in resolved]
    else:
        _fetch_outputs(output, local_out)
        with open(os.path.join(local_out, "state.json")) as f:
            state = json.load(f)
        with open(os.path.join(local_out, "progress.json")) as f:
            progress = json.load(f)
        # This job's steps start afresh: a previous attempt at this job (its machine taken back by AWS) left them as it
        # stopped, e.g. a later step of the group still "running" while this attempt starts again at the first.
        for e in resolved:
            if e.index in indexes:
                progress[e.index] = {"key": e.step.key, "title": e.step.title, "image": e.image, "state": "queued"}
    lock = threading.Lock()  # progress is also written by the TailPublisher thread

    def save_progress():
        # The website reads the published copy while the job runs; the local copy is published with the job's outputs
        # at the end, so it must be current too (a stale one would set this group's finished steps back to queued).
        put_json(progress, _join(output, "progress.json"))
        if output.startswith("s3://"):
            put_json(progress, os.path.join(local_out, "progress.json"))

    save_progress()

    def step_done(i, st, res):
        with lock:
            _step_done(i, st, res)

    def _step_done(i, st, res):
        progress[i]["state"] = st
        if st != "running":
            progress[i].pop("tail", None)  # the whole console is in the step's output.txt once it has finished
        if res:
            # The whole step record (verdict, every message, metrics, artifacts, rows), so the website shows a
            # finished step in full while later steps run; its folder is published now, so its files can be opened.
            progress[i].update({k: v for k, v in res.items() if k != "state"})
            if res.get("dir"):
                publish(os.path.join(local_out, res["dir"]), _join(output, res["dir"]))
        save_progress()
        if on_step:
            on_step(i, st, res)

    with TailPublisher(progress, local_out, resolved, save_progress, lock):
        for g in todo:
            executor.run_steps(resolved, state, spec["params"], res_dir, local_out, indexes=set(groups[g]["steps"]), on_step=step_done)

    # Which group runs next (Batch submits one job at a time; the worker reads this from the hand-over). After a block,
    # groups with nothing that must still run (always_run, e.g. the report) are recorded as skipped here instead of
    # each starting a job of its own; with nothing left to run, this job finishes the run.
    nxt = todo[-1] + 1 if todo[-1] + 1 < len(groups) else None
    if state["status"] != "completed" and nxt is not None:
        must_run = [g for g in range(nxt, len(groups)) if any(resolved[i].step.always_run for i in groups[g]["steps"])]
        stop = must_run[0] if must_run else len(groups)
        for g in range(nxt, stop):
            executor.run_steps(resolved, state, spec["params"], res_dir, local_out, indexes=set(groups[g]["steps"]), on_step=step_done)
        nxt = must_run[0] if must_run else None

    if nxt is None:
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
    state["next_group"] = nxt
    with open(os.path.join(local_out, "state.json"), "w") as f:
        json.dump(state, f, default=str)
    publish(local_out, output)
    for fn in ("working.tsv", "state.json", "step_logs.jsonl"):
        _put_file(os.path.join(local_out, fn), _join(output, "_state", fn))
    return state


# ---------- meta-analysis ----------

METHOD_OUTPUT = "method_output.txt"  # the method's console, stdout and stderr


class ConsoleTail:
    """While a meta-analysis runs, every TAIL_SECONDS puts the last lines of its console (method_output.txt) into
    progress.json at loc, as {stage, tail: {lines, at}}, so the website can show what it is doing (the meta-analysis
    counterpart of TailPublisher). stage says which part runs: "method" or "post-processing". Any error is ignored."""

    def __init__(self, console, loc):
        self.console, self.loc, self.stage, self.last = console, loc, "method", None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop.wait(TAIL_SECONDS):
            try:
                self.publish_once()
            except Exception:
                pass  # the tail is a convenience: never let it affect the run

    def publish_once(self):
        lines = tail_lines(self.console)
        if lines and (self.stage, lines) != self.last:
            self.last = (self.stage, lines)
            put_json({"stage": self.stage, "tail": {"lines": lines, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}},
                     self.loc)


def _run_logged(argv, cwd, env, console):
    """Run a command with its stdout and stderr, interleaved as printed, appended to the console file as they come
    (so its tail can be shown while it runs). Returns (exit code, what it printed)."""
    with open(console, "a") as f:
        f.write(f"$ {' '.join(argv)}\n\n")
        f.flush()
        start = f.tell()
        code = subprocess.run(argv, cwd=cwd, env={**env, "PYTHONUNBUFFERED": "1"}, stdout=f, stderr=subprocess.STDOUT).returncode
    with open(console, errors="replace") as f:
        f.seek(start)
        printed = f.read()
    with open(console, "a") as f:
        f.write(f"{'' if printed.endswith(chr(10)) or not printed else chr(10)}exit code: {code}\n\n")
    return code, printed


def run_method(method, inputs, params, common, local_out, resources=None, progress=None):
    """Run one meta-analysis method and then GWASHub's post-processing in local_out (_run_method), publishing the tail
    of its console to progress (a progress.json location; default: in local_out) while it runs; then remove the
    scratch folders (*_work/) however they ended, a failure or an out-of-memory kill included."""
    os.makedirs(local_out, exist_ok=True)
    console = os.path.join(local_out, METHOD_OUTPUT)
    open(console, "w").close()
    try:
        with ConsoleTail(console, progress or os.path.join(local_out, "progress.json")) as tail:
            return _run_method(method, inputs, params, common, local_out, resources, tail)
    finally:
        for name in os.listdir(local_out) if os.path.isdir(local_out) else []:
            if name.endswith(SCRATCH_SUFFIX) and os.path.isdir(os.path.join(local_out, name)):
                shutil.rmtree(os.path.join(local_out, name), ignore_errors=True)


def _run_method(method, inputs, params, common, local_out, resources=None, tail=None):
    """Run one meta-analysis method (analysis.yaml) and then GWASHub's post-processing in local_out.
    inputs: [{id, label, path (local), n_cases, n_controls, n_total, ancestry, sex}]. resources: a folder with the 1000 Genomes
    LD panels (1kg_b38_<POP>.*) for clumping the lead loci, if any. Returns the result dict."""
    from pipeline.runner.catalog import META_ANALYSIS_DIR
    from pipeline.runner.executor import _interpreter, describe_exit, docker_command, step_runner
    os.makedirs(local_out, exist_ok=True)
    inputs = [{**i, "path": os.path.realpath(i["path"])} for i in inputs]
    for name, obj in (("inputs.json", inputs), ("params.json", params), ("common.json", common)):
        with open(os.path.join(local_out, name), "w") as f:
            json.dump(obj, f, indent=2)
    log = os.path.join(local_out, "log.json")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([META_ANALYSIS_DIR, QC_STEPS_DIR]),
           "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR", "/tmp/gwashub-mpl")}
    args = ["--inputs", os.path.join(local_out, "inputs.json"), "--params", os.path.join(local_out, "params.json"),
            "--out", local_out, "--log", log]
    if step_runner() == "docker":  # locally, in the method's image (GWAMA and METAL live there)
        argv = docker_command(method.image, method.command, method.workdir, args, env, f"gwashub-meta-{uuid.uuid4().hex[:12]}",
                              writable=[local_out], readable=[i["path"] for i in inputs],
                              pythonpath=("analyses/meta_analysis", "qc_steps"))
    else:
        cmd = [*_interpreter(method.command[:-1]), os.path.join(method.workdir, method.command[-1])] if len(method.command) > 1 \
            else [os.path.join(method.workdir, method.command[0])]
        argv = [*cmd, *args]
    # The method's whole console goes to method_output.txt as it prints, kept for download (the results page offers
    # it, failed or not); the post-processing's follows it.
    console = os.path.join(local_out, METHOD_OUTPUT)
    code, printed = _run_logged(argv, method.workdir, env, console)
    if code != 0 or not os.path.exists(os.path.join(local_out, "meta_results.tsv.gz")):
        why = (f"The method {describe_exit(code, 'method')}" if code != 0
               else "The method finished without writing meta_results.tsv.gz")
        last = printed.strip()[-2000:]
        return {"status": "failed", "method": method.key, "error": why + "." + (f"\n\nLast output:\n{last}" if last else
                " It printed nothing.") + f"\n\nThe full console output is in {METHOD_OUTPUT}."}
    if not os.path.exists(log):
        with open(log, "w") as f:
            json.dump({"method": method.key, "verdict": "pass", "metrics": {}, "messages": []}, f)
    publish_dir = os.path.join(META_ANALYSIS_DIR, "_metalib")
    pub_args = ["--inputs", os.path.join(local_out, "inputs.json"), "--params", os.path.join(local_out, "common.json"),
                "--out", local_out, "--log", log, "--method", method.key, "--title", method.title,
                *(["--resources", resources] if resources else [])]
    if step_runner() == "docker":  # clumping needs PLINK 2, which every image built FROM gwashub/base has
        staged = [os.path.join(resources, f) for f in sorted(os.listdir(resources))] if resources else []
        pub_argv = docker_command(method.image, ["python", "publish.py"], publish_dir, pub_args, env,
                                  f"gwashub-publish-{uuid.uuid4().hex[:12]}", writable=[local_out],
                                  readable=[i["path"] for i in inputs] + ([resources] if resources else []) + staged,
                                  pythonpath=("analyses/meta_analysis", "qc_steps"))
    else:
        pub_argv = [sys.executable, os.path.join(publish_dir, "publish.py"), *pub_args]
    if tail:
        tail.stage = "post-processing"
    code, printed = _run_logged(pub_argv, publish_dir, env, console)
    if code != 0:
        last = printed.strip()[-2000:]
        return {"status": "failed", "method": method.key,
                "error": f"Post-processing (lead loci, plots) {describe_exit(code, 'method')}." + (f"\n\nLast output:\n{last}" if last else "")
                + f"\n\nThe full console output is in {METHOD_OUTPUT}."}
    with open(os.path.join(local_out, "summary.json")) as f:
        summary = json.load(f)
    with open(log) as f:
        mlog = json.load(f)
    return {"status": "completed", "method": method.key, "summary": summary, "log": mlog}


def run_meta(spec, workdir):
    """Meta-analysis spec: {"kind": "meta", "method": "<owner>/<method>", "inputs": [{id, label, loc, ...}],
    "params": {...}, "common": {gws, ld_panel, ...}, "resources": {key: LOC} (the LD panels), "output": LOC}."""
    from pipeline.runner.catalog import load_analyses
    output = spec["output"]
    local_out = output if not output.startswith("s3://") else os.path.join(workdir, "out")
    os.makedirs(local_out, exist_ok=True)
    methods = load_analyses()
    key = spec.get("method", "gwashub/gwama")
    if key not in methods:
        result = {"status": "failed", "method": key, "error": f"Unknown meta-analysis method {key}"}
    else:
        inputs = []
        for i, item in enumerate(spec["inputs"]):
            path = fetch(item["loc"], os.path.join(workdir, "_inputs", f"{i:03d}_{item['id']}.tsv"))
            inputs.append({k: v for k, v in item.items() if k != "loc"} | {"path": path})
        res_dir = None
        if spec.get("resources"):
            res_dir = os.path.join(workdir, "_resources")
            for rkey, loc in spec["resources"].items():
                fetch(loc, os.path.join(res_dir, rkey))
        result = run_method(methods[key], inputs, spec.get("params") or {}, spec.get("common") or {}, local_out, res_dir,
                            progress=_join(output, "progress.json"))
    if spec.get("images"):
        result["images"] = spec["images"]
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
