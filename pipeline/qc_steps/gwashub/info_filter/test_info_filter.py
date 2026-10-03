import pandas as pd

from _lib.testing import run_step


def test_filters_on_info(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [1, 2, 3], "ea": "A", "oa": "G", "beta": 0.1, "se": 0.1, "p": 0.5,
                       "info": [0.5, 0.9, None]})
    log, out = run_step("info_filter", df, {}, tmp_path)
    assert out["pos"].tolist() == [2, 3]  # missing INFO is kept


def test_warns_without_info_column(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [1], "ea": "A", "oa": "G", "beta": 0.1, "se": 0.1, "p": 0.5})
    log, _ = run_step("info_filter", df, {}, tmp_path)
    assert log["verdict"] == "warn"
