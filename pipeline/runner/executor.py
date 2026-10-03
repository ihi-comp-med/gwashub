"""Run a resolved pipeline on one input file, locally (one subprocess per step).

Layout of a run directory:

    <out>/working.tsv            the file each step reads and may rewrite
    <out>/params/<NN>_<name>.json per-step params (run params + {"step": step params})
    <out>/resources/<NN>/        only the resources that step declares (symlinks)
    <out>/steps/<NN>_<name>/     step log, stdout/stderr, artifacts (GWASHUB_ARTIFACT_DIR)
    <out>/step_logs.jsonl        every step log so far (GWASHUB_STEP_LOGS, read by qc_report)
    <out>/gwas_qc.tsv            final working file
    <out>/result.json            machine-readable roll-up

The AWS Batch executor will stage the same layout (input/resources from S3) and run each
step in its image; scripts can't tell the difference.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ORDER = {"pass": 0, "warn": 1, "fail": 2}


class RunError(Exception):
    pass


def _interpreter(cmd):
    if cmd[0] in ("python", "python3"):
        return [sys.executable, *cmd[1:]]
    found = shutil.which(cmd[0])
    if not found:
        raise RunError(f"interpreter {cmd[0]!r} not found on this machine")
    return [found, *cmd[1:]]


def _count_rows(path):
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def _valid_log(log):
    return isinstance(log, dict) and log.get("verdict") in ORDER


def start_run(input_path, out_dir):
    """Create the run layout and the state that run_steps carries from step to step (and, on AWS,
    from one Batch job to the next via state.json)."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(os.path.abspath(input_path), os.path.join(out_dir, "working.tsv"))
    open(os.path.join(out_dir, "step_logs.jsonl"), "w").close()
    return {"status": "completed", "stop_reason": None, "results": [], "verdict_logs": [], "elapsed": 0.0}


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
                    "on_fail": entry.on_fail, "source": entry.step.source, "dir": f"steps/{tag}"}

            if status != "completed" and not entry.step.always_run:
                results.append({**base, "state": "skipped"})
                continue

            # Stage only the resources this step declares.
            missing = []
            for key in entry.step.resources:
                src = os.path.join(resource_dir, key)
                if not os.path.exists(src):
                    missing.append(key)
                    continue
                dst = os.path.join(res_dir, key)
                if not os.path.exists(dst):
                    os.symlink(os.path.abspath(src), dst)
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
            try:
                cmd = _interpreter(entry.step.command)
                with open(os.path.join(step_dir, "output.txt"), "w") as out:
                    proc = subprocess.run(
                        [*cmd, "--input", working, "--resources", res_dir, "--params", params_path, "--log", log_path],
                        cwd=entry.step.workdir, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout,
                    )
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
            except RunError as e:
                code = str(e)
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
                reason = (f"exited with {code}" if code != 0 else "did not write a valid step log")
                if status == "completed":
                    status, stop_reason = "failed", f"{entry.step.title} crashed ({reason})"
                res = {**base, "state": "error", "verdict": "fail", "exit_code": code, "duration": duration,
                       "messages": [stop_reason, *([tail] if tail else [])], "metrics": {}, "artifacts": []}
                results.append(res)
                if on_step:
                    on_step(entry.index, "error", res)
                continue

            log["_dir"] = f"steps/{tag}"
            with open(logs_path, "a") as f:
                f.write(json.dumps(log) + "\n")
            rows = _count_rows(working)
            res = {**base, "state": log["verdict"], "verdict": log["verdict"], "metrics": log.get("metrics", {}),
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
    }
    with open(os.path.join(out_dir, "result.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)
    return result


def run_pipeline(resolved, input_path, run_params, resource_dir, out_dir, timeout=3600, on_step=None):
    """Execute `resolved` (list of ResolvedEntry) in one go. Returns the result dict (also written to result.json).

    on_step(index, state, log) is called as steps start/finish (for progress reporting).
    """
    state = start_run(input_path, out_dir)
    run_steps(resolved, state, run_params, resource_dir, out_dir, timeout=timeout, on_step=on_step)
    return finish_run(state, out_dir)


def _tail(path, n=15):
    try:
        with open(path) as f:
            lines = f.read().strip().splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""
