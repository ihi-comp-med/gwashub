import pandas as pd

from _lib.testing import run_step


def base(n=1000):
    return pd.DataFrame({"chr": "1", "pos": range(1, n + 1), "ea": "A", "oa": "G", "beta": 0.1, "se": 0.05, "p": 0.5, "eaf": 0.3})


def test_clean_file_passes(tmp_path):
    log, out = run_step("value_ranges", base(), {}, tmp_path)
    assert log["verdict"] == "pass" and len(out) == 1000


def test_drops_missing_out_of_range_and_duplicates(tmp_path):
    df = base()
    df.loc[0, "se"] = None
    df.loc[1, "p"] = 0
    df.loc[2, "eaf"] = 1.5
    df.loc[3, "se"] = -1
    df = pd.concat([df, df.iloc[[10, 11]]])
    log, out = run_step("value_ranges", df, {}, tmp_path)
    m = log["metrics"]
    assert (m["dropped_na"], m["dropped_out_of_range"], m["duplicates"]) == (1, 3, 2)
    assert len(out) == 996
    assert log["verdict"] == "warn"  # 6/1002 > 0.5%


def test_detects_neglog10_p(tmp_path):
    df = base()
    df.loc[:200, "p"] = 3.2
    log, _ = run_step("value_ranges", df, {}, tmp_path)
    assert log["verdict"] == "fail"
    assert "log10" in log["messages"][0]
