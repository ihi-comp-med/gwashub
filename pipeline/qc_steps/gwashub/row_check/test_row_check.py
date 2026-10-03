import gzip

import pandas as pd

from _lib.testing import run_step

MAP = {"chr": "CHR", "pos": "BP", "ea": "A1", "oa": "A2", "beta": "BETA", "se": "SE", "p": "P"}
OPTS = {"step": {"min_rows": 1}}


def frame(n=5):
    return pd.DataFrame({"CHR": [1] * n, "BP": range(100, 100 + n), "A1": "A", "A2": "G", "BETA": 0.1, "SE": 0.05, "P": 0.5})


def test_parses_gzipped_csv_and_rewrites_as_tsv(tmp_path):
    src = tmp_path / "in.csv.gz"
    with gzip.open(src, "wt") as f:
        frame().to_csv(f, index=False)
    log, df = run_step("row_check", str(src), {"column_mapping": MAP, **OPTS}, tmp_path)
    assert log["verdict"] == "pass"
    assert log["metrics"]["delimiter"] == "comma" and log["metrics"]["compressed"] is True
    assert list(df.columns) == list(frame().columns) and len(df) == 5


def test_fails_when_required_column_not_mapped(tmp_path):
    log, _ = run_step("row_check", frame(), {"column_mapping": {k: v for k, v in MAP.items() if k != "se"}, **OPTS}, tmp_path)
    assert log["verdict"] == "fail"
    assert "se" in log["messages"][0]


def test_fails_when_mapped_column_missing_from_header(tmp_path):
    log, _ = run_step("row_check", frame(), {"column_mapping": {**MAP, "p": "PVAL"}, **OPTS}, tmp_path)
    assert log["verdict"] == "fail"
    assert "PVAL" in " ".join(log["messages"])


def test_fails_on_non_numeric_column(tmp_path):
    df = frame()
    df["P"] = "n/a"
    log, _ = run_step("row_check", df, {"column_mapping": MAP, **OPTS}, tmp_path)
    assert log["verdict"] == "fail"
    assert log["metrics"]["numeric_fraction_p"] == 0
