"""Run a resolved pipeline on one input file, locally (one subprocess per step).

Layout of a run directory:

    <out>/working.tsv            the file each step reads and may rewrite
    <out>/params/<NN>_<name>.json per-step params (run params + {"step": step params})
    <out>/resources/<NN>/        only the resources that step declares (symlinks)
    <out>/steps/<NN>_<name>/     step log, stdout/stderr, artifacts (GWASHUB_ARTIFACT_DIR)
    <out>/step_logs.jsonl        every step log so far (GWASHUB_STEP_LOGS, read by qc_report)
    <out>/gwas_qc.tsv            final working file
    <out>/profile_input.json     column profile of the upload as received (_lib/profile.py)
    <out>/result.json            machine-readable roll-up

On AWS Batch the job already runs in the step's image, so steps run as plain subprocesses. Locally they
do too, with this machine's Python/R, unless GWASHUB_STEP_RUNNER=docker: then each step runs in its own
image (`docker run`, built locally with devtools/build_images.sh) with the repository's current
pipeline/ code mounted over the image's copy, the run directory and the step's resources mounted at the
same paths, and no network. Scripts can't tell the difference.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ORDER = {"pass": 0, "warn": 1, "fail": 2}
CONTAINER_PIPELINE = "/app/pipeline"  # where images keep pipeline/ (pipeline/images/*/Dockerfile)


def _profiler():
    """The shared profiling helpers (pipeline/qc_steps/_lib/profile.py), also used by qc_report."""
    from pipeline.runner.catalog import QC_STEPS_DIR
    if QC_STEPS_DIR not in sys.path:
        sys.path.insert(0, QC_STEPS_DIR)
    from _lib import profile
    return profile


class RunError(Exception):
    pass


class Interrupted(Exception):
    """The job was told to stop (SIGTERM; on AWS Batch, the Spot machine is being reclaimed). Nothing is recorded or
    handed over, so the retried job runs its group of steps again from the start, not a "crashed" step."""


# A step stopped by SIGTERM: -15 when it runs as a process here, 143 (128 + 15) from `docker run`. (SIGKILL, e.g. the
# kernel's out-of-memory killer, stays a crash: running it again would fail the same way.)
INTERRUPTED = (-signal.SIGTERM, 128 + signal.SIGTERM)


def _interpreter(cmd):
    if cmd[0] in ("python", "python3"):
        return [sys.executable, *cmd[1:]]
    found = shutil.which(cmd[0])
    if not found:
        raise RunError(f"interpreter {cmd[0]!r} not found on this machine")
    return [found, *cmd[1:]]


def step_runner():
    """"host" (default; also inside Batch jobs) or "docker" (local runs in each step's image)."""
    mode = os.environ.get("GWASHUB_STEP_RUNNER", "host")
    if mode not in ("host", "docker"):
        raise RunError(f"GWASHUB_STEP_RUNNER must be host or docker, not {mode!r}")
    return mode


def _image_platforms():
    if not hasattr(_image_platforms, "cache"):
        from pipeline.runner.catalog import load_images
        _image_platforms.cache = {ref: i["platform"] for ref, i in load_images().items()}
    return _image_platforms.cache


def missing_local_images(refs):
    """The image refs that aren't built locally (devtools/build_images.sh builds them)."""
    out = []
    for ref in sorted(set(refs)):
        if subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode != 0:
            out.append(ref)
    return out


def docker_command(image, command, workdir, args, env, name, writable=(), readable=(), pythonpath=("qc_steps",)):
    """`docker run` for one step or meta-analysis method in its image: the image's own interpreter, the repository's
    current pipeline/ code mounted over the image's copy, no network, and only the given host paths mounted (at the
    same paths, so arguments pass through unchanged)."""
    from pipeline.runner.catalog import PIPELINE_DIR
    mounts = [f"{PIPELINE_DIR}:{CONTAINER_PIPELINE}:ro"]
    if os.path.commonpath([PIPELINE_DIR, workdir]) == PIPELINE_DIR:
        container_workdir = os.path.join(CONTAINER_PIPELINE, os.path.relpath(workdir, PIPELINE_DIR))
    else:  # a folder outside the repository (tests)
        container_workdir = workdir
        mounts.append(f"{workdir}:{workdir}:ro")
    # Each path is mounted where symlinks resolve to and where the arguments name it (e.g. /var -> /private/var on macOS).
    for paths, mode in ((writable, ""), (readable, ":ro")):
        for p in paths:
            real = os.path.realpath(p)
            mounts += [f"{real}:{target}{mode}" for target in dict.fromkeys([real, os.path.abspath(p)])]
    passed = {k: v for k, v in env.items() if k.startswith("GWASHUB_")}
    passed.update(PYTHONPATH=":".join(["/app", *(f"{CONTAINER_PIPELINE}/{p}" for p in pythonpath)]), MPLCONFIGDIR="/tmp/mpl", HOME="/tmp")
    cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "-w", container_workdir]
    platform = _image_platforms().get(image)
    if platform:
        cmd += ["--platform", platform]
    for m in dict.fromkeys(mounts):
        cmd += ["-v", m]
    for k, v in sorted(passed.items()):
        cmd += ["-e", f"{k}={v}"]
    return [*cmd, image, *command, *args]


def step_docker_command(entry, step_args, env, out_dir, res_dir, name):
    """docker_command for a QC step: the run directory writable, its resources readable."""
    readable = [] if os.path.commonpath([out_dir, res_dir]) == out_dir else [res_dir]
    readable += [os.path.join(res_dir, f) for f in sorted(os.listdir(res_dir))]
    return docker_command(entry.image, entry.step.command, entry.step.workdir, step_args, env, name,
                          writable=[out_dir], readable=readable)


def _count_rows(path):
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def describe_exit(code, what="step") -> str:
    """Why a step or method process ended, in words: an out-of-memory kill (signal 9; 137 from `docker run`) says
    what to change, since it leaves no error message of its own."""
    if code == "timeout":
        return "ran past the time limit"
    if not isinstance(code, int):
        return str(code)
    if code in (-signal.SIGKILL, 128 + signal.SIGKILL):
        yaml_file = "step.yaml" if what == "step" else "analysis.yaml"
        return (f"was killed (signal 9), most likely because it ran out of memory: give the {what} more with "
                f"compute: memory_gb in its {yaml_file}")
    if code < 0:
        return f"was stopped by signal {-code}"
    return f"exited with {code}"


def used_params(entry) -> list:
    """The parameters a step runs with: every field of its step.yaml, the pipeline's value where it set one, else the
    default; recorded with the step so a run keeps what it used even if a default changes later.
    [{key, label, help, type, value, default, changed}]"""
    given = entry.params or {}
    out = []
    for f in entry.step.param_schema:
        default = f.get("default")
        value = given.get(f["key"], default)
        if f.get("type") == "range" and isinstance(value, dict) and isinstance(default, dict):
            value = {**default, **value}
        out.append({"key": f["key"], "label": f.get("label", f["key"]), "help": f.get("help", ""), "type": f.get("type"), "value": value,
                    "default": default, "changed": f["key"] in given and value != default})
    return out


def _valid_log(log):
    return isinstance(log, dict) and log.get("verdict") in ORDER


def start_run(input_path, out_dir, run_params=None):
    """Create the run layout and the state that run_steps carries from step to step (and, on AWS,
    from one Batch job to the next via state.json). Profiles the upload first, whatever the pipeline,
    so every run can show its numbers before and after QC."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(os.path.abspath(input_path), os.path.join(out_dir, "working.tsv"))
    open(os.path.join(out_dir, "step_logs.jsonl"), "w").close()
    try:
        prof = _profiler().profile_upload(input_path, (run_params or {}).get("column_mapping"))
    except Exception:  # an unreadable upload is gwashub/parse's to report
        prof = None
    with open(os.path.join(out_dir, "profile_input.json"), "w") as f:
        json.dump(prof, f)
    return {"status": "completed", "stop_reason": None, "results": [], "verdict_logs": [], "elapsed": 0.0,
            "rows": prof["rows"] if prof else None, "profile_input": prof}


def run_steps(resolved, state, run_params, resource_dir, out_dir, indexes=None, timeout=3600, on_step=None):
    """Run the steps with these indexes (default: all) on <out_dir>/working.tsv, updating state."""
    out_dir, resource_dir = os.path.abspath(out_dir), os.path.abspath(resource_dir)
    working = os.path.join(out_dir, "working.tsv")
    logs_path = os.path.join(out_dir, "step_logs.jsonl")
    todo = [e for e in resolved if indexes is None or e.index in indexes]
    results, verdict_logs = state["results"], state["verdict_logs"]
    status, stop_reason = state["status"], state["stop_reason"]
    started = time.time()
    try:
        for entry in todo:
            tag = f"{entry.index + 1:02d}_{entry.name}"
            step_dir = os.path.join(out_dir, "steps", tag)
            res_dir = os.path.join(out_dir, "resources", f"{entry.index + 1:02d}")
            os.makedirs(step_dir, exist_ok=True)
            os.makedirs(res_dir, exist_ok=True)
            base = {"index": entry.index, "key": entry.step.key, "title": entry.step.title, "image": entry.image,
                    "params": used_params(entry),
                    "on_fail": entry.on_fail, "source": entry.step.source, "dir": f"steps/{tag}"}

            if status != "completed" and not entry.step.always_run:
                results.append({**base, "state": "skipped"})
                if on_step:  # shown as skipped at once, not left "queued" until the run ends
                    on_step(entry.index, "skipped", None)
                continue

            # Stage only the resources this step declares (optional ones only if they exist).
            missing = []
            for key in entry.step.all_resources:
                src = os.path.join(resource_dir, key)
                if not os.path.exists(src):
                    if key in entry.step.resources:
                        missing.append(key)
                    continue
                dst = os.path.join(res_dir, key)
                if not os.path.exists(dst):
                    os.symlink(os.path.realpath(src), dst)
            if missing:
                status, stop_reason = "failed", f"{entry.step.title}: missing resource(s) {', '.join(missing)}"
                results.append({**base, "state": "error", "verdict": "fail", "messages": [stop_reason]})
                continue

            params_path = os.path.join(out_dir, "params", f"{tag}.json")
            os.makedirs(os.path.dirname(params_path), exist_ok=True)
            with open(params_path, "w") as f:
                json.dump({**run_params, "step": entry.params}, f, indent=2)
            log_path = os.path.join(step_dir, "step_log.json")

            env = dict(os.environ)
            env.update({
                "GWASHUB_ARTIFACT_DIR": step_dir,
                "GWASHUB_STEP_LOGS": logs_path,
                "GWASHUB_RUN_DIR": out_dir,
                "PYTHONPATH": os.pathsep.join([*entry.step.pythonpath, env.get("PYTHONPATH", "")]),
                "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR") or os.path.join(tempfile.gettempdir(), "gwashub-mpl"),
            })
            if on_step:
                on_step(entry.index, "running", None)
            t0 = time.time()
            step_args = ["--input", working, "--resources", res_dir, "--params", params_path, "--log", log_path]
            container = f"gwashub-step-{uuid.uuid4().hex[:12]}"
            try:
                if step_runner() == "docker":
                    argv = step_docker_command(entry, step_args, env, out_dir, res_dir, container)
                else:
                    argv = [*_interpreter(entry.step.command), *step_args]
                with open(os.path.join(step_dir, "output.txt"), "w") as out:
                    proc = subprocess.run(argv, cwd=entry.step.workdir, env=env, stdout=out, stderr=subprocess.STDOUT,
                                          timeout=timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
                if step_runner() == "docker":
                    subprocess.run(["docker", "kill", container], capture_output=True)
            except RunError as e:
                code = str(e)
            if isinstance(code, int) and code in INTERRUPTED:
                raise Interrupted(f"{entry.step.title} was stopped (SIGTERM)")
            duration = round(time.time() - t0, 2)

            log = None
            if os.path.exists(log_path):
                try:
                    with open(log_path) as f:
                        log = json.load(f)
                except json.JSONDecodeError:
                    log = None

            console = os.path.join(step_dir, "output.txt")
            tail = _tail(console)
            if os.path.exists(console) and os.path.getsize(console) == 0:
                os.remove(console)  # the step printed nothing
            if code != 0 or not _valid_log(log):
                reason = describe_exit(code) if code != 0 else "did not write a valid step log"
                if status == "completed":
                    status, stop_reason = "failed", f"{entry.step.title} crashed ({reason})"
                res = {**base, "state": "error", "verdict": "fail", "exit_code": code, "duration": duration,
                       "messages": [stop_reason, *([tail] if tail else [])], "metrics": {}, "artifacts": []}
                results.append(res)
                if on_step:
                    on_step(entry.index, "error", res)
                continue

            rows = _count_rows(working)
            rows_before, state["rows"] = state.get("rows"), rows
            log["_dir"], log["_rows"] = f"steps/{tag}", [rows_before, rows]  # read by qc_report
            with open(logs_path, "a") as f:
                f.write(json.dumps(log) + "\n")
            res = {**base, "rows_before": rows_before, "state": log["verdict"], "verdict": log["verdict"], "metrics": log.get("metrics", {}),
                   "messages": log.get("messages", []), "artifacts": log.get("artifacts", []), "duration": duration,
                   "rows_after": rows, "exit_code": 0}
            results.append(res)
            if entry.on_fail != "continue":
                verdict_logs.append(log)
            if on_step:
                on_step(entry.index, log["verdict"], res)
            if log["verdict"] == "fail" and entry.on_fail == "block" and status == "completed":
                status, stop_reason = "blocked", f"{entry.step.title} failed and is set to block"

    finally:
        state.update(status=status, stop_reason=stop_reason, elapsed=state["elapsed"] + time.time() - started)
    return state


def finish_run(state, out_dir):
    """Final file and result.json once every step has run."""
    out_dir = os.path.abspath(out_dir)
    final = os.path.join(out_dir, "gwas_qc.tsv")
    shutil.copyfile(os.path.join(out_dir, "working.tsv"), final)
    results, status = state["results"], state["status"]
    overall = "fail" if status != "completed" else max((l["verdict"] for l in state["verdict_logs"]), key=ORDER.get, default="pass")
    report = next((os.path.join(r["dir"], a["path"]) for r in results for a in r.get("artifacts") or []
                   if a.get("type") == "report"), None)
    result = {
        "status": status,  # completed | blocked | failed
        "overall_verdict": overall,
        "stop_reason": state["stop_reason"],
        "duration": round(state["elapsed"], 2),
        "steps": results,
        "outputs": {"gwas_qc": "gwas_qc.tsv", "qc_report": report, "rows": _count_rows(final)},
        "profile": {"input": state.get("profile_input"), "output": None},
    }
    if status == "completed":
        try:
            result["profile"]["output"] = _profiler().profile_working(final)
        except Exception:
            pass
    with open(os.path.join(out_dir, "result.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)
    return result


def run_pipeline(resolved, input_path, run_params, resource_dir, out_dir, timeout=3600, on_step=None):
    """Execute `resolved` (list of ResolvedEntry) in one go. Returns the result dict (also written to result.json).

    on_step(index, state, log) is called as steps start/finish (for progress reporting).
    """
    state = start_run(input_path, out_dir, run_params)
    run_steps(resolved, state, run_params, resource_dir, out_dir, timeout=timeout, on_step=on_step)
    return finish_run(state, out_dir)


def _tail(path, n=15):
    try:
        with open(path) as f:
            lines = f.read().strip().splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""
