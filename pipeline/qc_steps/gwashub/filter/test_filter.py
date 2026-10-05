import pandas as pd

from _lib.testing import needs_image, run_step


def frame():
    return pd.DataFrame({
        "chr": ["1", "1", "1", "2", "2", "X", "Y", "3", "3", "3"],
        "pos": [10, 20, 30, 40, 50, 60, 70, 80, 80, 90],
        "ea": ["A", "C", "G", "T", "AT", "A", "C", "G", "C", "A"],
        "oa": ["G", "A", "A", "C", "A", "G", "A", "C", "G", "G"],
        "beta": [0.1, 25.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, -0.1, 0.1],
        "se": [0.01] * 10, "p": [0.5] * 10,
        "eaf": [0.3, 0.3, None, 1.0, 0.3, 0.3, 0.3, 0.3, 0.7, 0.002],
        "n": [1000] * 9 + [None], "info": [0.9, 0.9, 0.9, 0.9, 0.9, None, 0.5, 0.9, 0.9, 0.9],
    })


@needs_image("gwashub/base-r")
def test_hermes_defaults(tmp_path):
    log, out = run_step("filter", frame(), {}, tmp_path)
    m = log["metrics"]
    # |BETA| 25, missing EAF, EAF 1, missing INFO, missing N, and the duplicate at 3:80 (alleles swapped) go.
    assert m["removed_beta_too_large"] == 1 and m["removed_eaf_missing"] == 1 and m["removed_eaf_0_or_1"] == 1
    assert m["removed_info_missing"] == 1 and m["removed_n_missing"] == 1 and m["removed_duplicate_variant"] == 1
    assert sorted(out["pos"]) == [10, 50, 70, 80] and log["verdict"] == "warn"  # 6 of 10 removed


@needs_image("gwashub/base-r")
def test_thresholds(tmp_path):
    step = {"chromosomes": "1-22", "min_info": 0.8, "drop_indels": True, "require_eaf": False, "require_n": False,
            "drop_missing_info": False, "min_maf": 0.01}
    log, out = run_step("filter", frame(), {"step": step}, tmp_path)
    assert set(out["chr"]) <= {"1", "2", "3"} and not (out["ea"].str.len() > 1).any()
    assert log["metrics"]["removed_maf_below_minimum"] == 1 and 25.0 not in out["beta"].tolist()


@needs_image("gwashub/base-r")
def test_missing_column_fails_clearly(tmp_path):
    log, _ = run_step("filter", frame().drop(columns=["eaf"]), {}, tmp_path)
    assert log["verdict"] == "fail" and "EAF" in log["messages"][0]
