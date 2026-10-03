import pandas as pd

from _lib.testing import run_step


def test_filters_on_minor_allele_frequency(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [1, 2, 3, 4], "ea": "A", "oa": "G", "beta": 0.1, "se": 0.1, "p": 0.5,
                       "eaf": [0.005, 0.995, 0.02, 0.5]})
    log, out = run_step("maf_filter", df, {"step": {"maf_threshold": 0.01}}, tmp_path)
    assert out["pos"].tolist() == [3, 4] and log["metrics"]["dropped_low_maf"] == 2
