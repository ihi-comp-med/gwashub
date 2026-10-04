import gzip

import numpy as np
import pandas as pd

from _lib.testing import run_step


def _data(n_snps=20000, intercept=1.0, h2=0.3, n=1000, seed=3):
    """Simulated χ² with E[χ²] = intercept + N·h²·ℓ/M, and the matching LD-score reference."""
    rng = np.random.default_rng(seed)
    l2 = rng.gamma(2.0, 25.0, n_snps)
    m = n_snps
    mean = intercept + n * h2 * l2 / m
    z = rng.normal(size=n_snps) * np.sqrt(mean)
    pos = np.arange(n_snps) * 1000 + 1
    df = pd.DataFrame({"chr": "1", "pos": pos, "ea": "A", "oa": "G", "beta": z * 0.01, "se": 0.01, "p": 0.5})
    ld = pd.DataFrame({"chr": "1", "pos": pos, "l2": l2})
    return df, ld


def _write_ref(dirpath, pop, ld):
    dirpath.mkdir(exist_ok=True)
    with gzip.open(dirpath / f"ldscores_{pop}.tsv.gz", "wt") as f:
        ld.to_csv(f, sep="\t", index=False)


def test_recovers_intercept(tmp_path):
    df, ld = _data(intercept=1.0)
    _write_ref(tmp_path / "res", "EUR", ld)
    log, out = run_step("ldsc", df, {"ancestry": "EUR", "n_cases": 500, "n_controls": 500}, tmp_path, resources=tmp_path / "res")
    assert log["verdict"] == "pass" and abs(log["metrics"]["intercept"] - 1.0) < 0.05 and log["metrics"]["intercept_se"] > 0
    assert len(out) == len(df)  # data unchanged


def test_confounding_warns(tmp_path):
    df, ld = _data(intercept=1.25)
    _write_ref(tmp_path / "res", "CSA", ld)  # SAS datasets use the CSA reference
    log, _ = run_step("ldsc", df, {"ancestry": "SAS", "n_cases": 500, "n_controls": 500}, tmp_path, resources=tmp_path / "res")
    assert log["verdict"] == "warn" and log["metrics"]["intercept"] > 1.15 and log["metrics"]["population"] == "CSA"


def test_missing_reference_passes_and_says_why(tmp_path):
    """Until the references are uploaded the step must not hold datasets up: it passes, saying it didn't run."""
    df, ld = _data(n_snps=100)
    log, _ = run_step("ldsc", df, {"ancestry": "EUR", "n_cases": 100, "n_controls": 100}, tmp_path)
    assert log["verdict"] == "pass" and "isn't uploaded yet" in log["messages"][0] and log["metrics"]["skipped"] == 1
    log, _ = run_step("ldsc", df, {"ancestry": "Multi"}, tmp_path)
    assert log["verdict"] == "pass" and "multi-ancestry" in log["messages"][0]
    _write_ref(tmp_path / "res", "EUR", ld)
    log, _ = run_step("ldsc", df, {"ancestry": "EUR", "n_cases": 100, "n_controls": 100}, tmp_path, resources=tmp_path / "res")
    assert log["verdict"] == "pass" and "variants matched" in log["messages"][0]
