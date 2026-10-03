import numpy as np
import pandas as pd

from _lib.testing import run_step

MAP = {"chr": "CHROM", "pos": "BP", "ea": "EA", "oa": "NEA", "beta": "OR", "se": "SE", "p": "P"}


def test_renames_converts_or_and_normalises(tmp_path):
    df = pd.DataFrame({"CHROM": ["chr1", "chr23", "chrUn_gl000220", "2"], "BP": [10, 20, 30, 40],
                       "EA": ["a", "C", "G", "T"], "NEA": ["g", "T", "A", "C"], "OR": [np.e, 1.0, 2.0, 0.0],
                       "SE": 0.1, "P": 0.5, "EXTRA": 1})
    log, out = run_step("harmonise_columns", df, {"column_mapping": MAP, "effect_type": "or", "n_cases": 10, "n_controls": 90}, tmp_path)
    assert log["verdict"] == "pass"
    assert list(out.columns) == ["chr", "pos", "ea", "oa", "beta", "se", "p", "n"]
    assert out["chr"].tolist() == ["1", "X", "2"]  # contig dropped, 23 -> X
    assert out["ea"].tolist()[0] == "A" and out["oa"].tolist()[0] == "G"
    assert out["beta"].iloc[0] == 1.0  # log(e)
    assert np.isnan(out["beta"].iloc[2])  # OR of 0 is invalid -> NA, removed later by value_ranges
    assert (out["n"] == 100).all()
    assert log["metrics"]["dropped_other_chromosomes"] == 1


def test_fails_without_required_mapping(tmp_path):
    df = pd.DataFrame({"CHROM": [1], "BP": [1]})
    log, _ = run_step("harmonise_columns", df, {"column_mapping": {"chr": "CHROM", "pos": "BP"}}, tmp_path)
    assert log["verdict"] == "fail"
