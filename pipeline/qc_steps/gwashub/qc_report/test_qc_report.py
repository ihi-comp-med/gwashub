import json

import pandas as pd

from _lib.testing import run_step


def test_report_rolls_up_step_logs(tmp_path):
    logs = tmp_path / "logs.jsonl"
    logs.write_text("\n".join(json.dumps(l) for l in [
        {"step": "parse", "verdict": "pass", "metrics": {"total_rows": 10}, "messages": ["ok"]},
        {"step": "filter", "verdict": "warn", "metrics": {}, "messages": ["dropped <stuff>"]},
    ]))
    log, _ = run_step("qc_report", pd.DataFrame({"a": [1]}), {"cohort": "Test"}, tmp_path,
                      env={"GWASHUB_STEP_LOGS": str(logs)})
    html = (tmp_path / "artifacts" / "qc_report.html").read_text()
    assert log["metrics"]["steps_reported"] == 2
    assert "filter" in html and "&lt;stuff&gt;" in html and "Overall verdict" in html and ">warn<" in html


def test_report_shows_rows_by_step_and_columns_before_and_after(tmp_path):
    logs = tmp_path / "logs.jsonl"
    logs.write_text("\n".join(json.dumps(l) for l in [
        {"step": "parse", "verdict": "pass", "_rows": [120, 120]},
        {"step": "filter", "verdict": "pass", "_rows": [120, 100]},
    ]))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "profile_input.json").write_text(json.dumps(
        {"rows": 120, "columns": {"beta": {"n": 118, "missing": 2, "mean": 0.5, "median": 0.4, "sd": 1.0, "min": -9.0, "max": 99.0}}}))
    df = pd.DataFrame({"chr": ["1"] * 4, "pos": [1, 2, 3, 4], "ea": ["A"] * 4, "oa": ["G"] * 4,
                       "beta": [0.1, 0.2, 0.3, 0.4], "se": [0.1] * 4, "p": [0.5] * 4})
    run_step("qc_report", df, {}, tmp_path, env={"GWASHUB_STEP_LOGS": str(logs), "GWASHUB_RUN_DIR": str(run_dir)})
    html = (tmp_path / "artifacts" / "qc_report.html").read_text()
    assert "Rows by step" in html and ">20<" in html  # filter removed 20
    assert "Columns before and after QC" in html and ">99<" in html and ">0.25<" in html  # max uploaded, mean after
    assert "not in file" in html  # se was not mapped in the upload
