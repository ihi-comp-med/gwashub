import pandas as pd

from _lib.testing import run_step


def test_counts_and_drops(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [1, 2, 3, 4, 5], "ea": ["A", "A", "AT", "N", "C"], "oa": ["G", "T", "A", "G", "C"],
                       "beta": 0.1, "se": 0.1, "p": 0.5})
    log, out = run_step("alleles", df, {}, tmp_path)
    m = log["metrics"]
    assert m["invalid"] == 2  # N, and ea == oa
    assert m["palindromic"] == 1 and m["indels"] == 1
    assert len(out) == 3


def test_drop_indels_option(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [1, 2], "ea": ["A", "AT"], "oa": ["G", "A"], "beta": 0.1, "se": 0.1, "p": 0.5})
    _, out = run_step("alleles", df, {"step": {"drop_indels": True}}, tmp_path)
    assert out["ea"].tolist() == ["A"]
