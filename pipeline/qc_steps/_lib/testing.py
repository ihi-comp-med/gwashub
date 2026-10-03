"""Test helper: run one step on a DataFrame and return (log, output DataFrame).
name: "row_check" (a default step, owner gwashub) or "<owner>/<step>"."""
import json
import os
import subprocess
import sys

import pandas as pd

STEPS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_step(name, data, params, tmp_path, resources=None, raw=False, env=None):
    """data: DataFrame (written as TSV) or a path to copy. Returns (log dict, df or None)."""
    work = tmp_path / "working.tsv"
    if isinstance(data, pd.DataFrame):
        data.to_csv(work, sep="\t", index=False)
    else:
        work.write_bytes(open(data, "rb").read())
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params))
    log_path = tmp_path / "log.json"
    res_dir = resources or (tmp_path / "res")
    os.makedirs(res_dir, exist_ok=True)
    art = tmp_path / "artifacts"
    full_env = {**os.environ, "PYTHONPATH": STEPS, "GWASHUB_ARTIFACT_DIR": str(art), **(env or {})}
    folder = name if "/" in name else f"gwashub/{name}"
    proc = subprocess.run([sys.executable, os.path.join(STEPS, folder, f"{folder.split('/')[-1]}.py"), "--input", str(work),
                           "--resources", str(res_dir), "--params", str(params_path), "--log", str(log_path)],
                          env=full_env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    log = json.loads(log_path.read_text())
    if raw:
        return log, work
    try:
        df = pd.read_csv(work, sep="\t", dtype={"chr": str})
    except pd.errors.EmptyDataError:
        df = None
    return log, df
