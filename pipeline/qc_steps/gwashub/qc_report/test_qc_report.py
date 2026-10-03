import json

import pandas as pd

from _lib.testing import run_step


def test_report_rolls_up_step_logs(tmp_path):
    logs = tmp_path / "logs.jsonl"
    logs.write_text("\n".join(json.dumps(l) for l in [
        {"step": "row_check", "verdict": "pass", "metrics": {"total_rows": 10}, "messages": ["ok"]},
        {"step": "value_ranges", "verdict": "warn", "metrics": {}, "messages": ["dropped <stuff>"]},
    ]))
    log, _ = run_step("qc_report", pd.DataFrame({"a": [1]}), {"cohort": "Test"}, tmp_path,
                      env={"GWASHUB_STEP_LOGS": str(logs)})
    html = (tmp_path / "artifacts" / "qc_report.html").read_text()
    assert log["metrics"]["steps_reported"] == 2
    assert "value_ranges" in html and "&lt;stuff&gt;" in html and "Overall verdict" in html and ">warn<" in html
