"""Test helpers: run one step on a DataFrame the way the runner does, and skip tests whose images aren't built.

run_step(name, ...): name is "<owner>/<step>", or just "<step>" for a gwashub default step. With
GWASHUB_STEP_RUNNER=docker (the root conftest.py sets it when Docker and the images are there) the step runs in its
image; otherwise with this machine's interpreters."""
import functools
import json
import os
import shutil
import subprocess

import pandas as pd
import pytest

STEPS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@functools.lru_cache(maxsize=1)
def _catalog():
    from pipeline.runner.catalog import load_steps
    return load_steps(STEPS)


@functools.lru_cache(maxsize=None)
def images_built(*refs):
    """True when Docker is available and every image ref is built locally."""
    if not shutil.which("docker"):
        return False
    from pipeline.runner.executor import missing_local_images
    try:
        return not missing_local_images(refs)
    except OSError:
        return False


def needs_image(*refs):
    """pytest mark: run only when these images are built and steps run in Docker (devtools/build_images.sh)."""
    ok = images_built(*refs) and os.environ.get("GWASHUB_STEP_RUNNER") == "docker"
    return pytest.mark.skipif(not ok, reason=f"needs Docker images {', '.join(refs)} (./devtools/build_images.sh)")


def run_step(name, data, params, tmp_path, resources=None, raw=False, env=None):
    """data: DataFrame (written as TSV) or a path to copy. Returns (log dict, df or None); raw=True returns the path."""
    from pipeline.runner.executor import _interpreter, step_docker_command, step_runner
    from pipeline.runner.pipeline import ResolvedEntry
    sd = _catalog()[name if "/" in name else f"gwashub/{name}"]
    work = tmp_path / "working.tsv"
    if isinstance(data, pd.DataFrame):
        data.to_csv(work, sep="\t", index=False, na_rep="NA")
    else:
        work.write_bytes(open(data, "rb").read())
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params))
    log_path = tmp_path / "log.json"
    res_dir = str(resources or (tmp_path / "res"))
    os.makedirs(res_dir, exist_ok=True)
    art = tmp_path / "artifacts"
    art.mkdir(exist_ok=True)
    full_env = {**os.environ, "PYTHONPATH": STEPS, "GWASHUB_ARTIFACT_DIR": str(art), **(env or {})}
    args = ["--input", str(work), "--resources", res_dir, "--params", str(params_path), "--log", str(log_path)]
    if step_runner() == "docker":
        entry = ResolvedEntry(0, sd, sd.default_on_fail, sd.image, sd.params)
        cmd = step_docker_command(entry, args, full_env, str(tmp_path), res_dir, f"gwashub-test-{os.getpid()}-{tmp_path.name}"[:60])
    else:
        cmd = [*_interpreter(sd.command), *args]
    proc = subprocess.run(cmd, cwd=sd.workdir, env=full_env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = json.loads(log_path.read_text())
    if raw:
        return log, work
    try:
        df = pd.read_csv(work, sep="\t", dtype={"chr": str, "ea": str, "oa": str})
    except pd.errors.EmptyDataError:
        df = None
    return log, df
