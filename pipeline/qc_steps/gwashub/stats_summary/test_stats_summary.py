import numpy as np
import pandas as pd
from scipy import stats

from _lib.testing import run_step


def sumstats(n=20000, inflation=1.0, hits=()):
    rng = np.random.default_rng(1)
    z = rng.normal(size=n) * np.sqrt(inflation)
    for i, zz in hits:
        z[i] = zz
    se = np.full(n, 0.02)
    return pd.DataFrame({"chr": "1", "pos": np.arange(n) * 1000 + 1, "ea": "A", "oa": "G", "beta": z * se, "se": se,
                         "p": 2 * stats.norm.sf(np.abs(z))})


def test_null_lambda_near_one_and_lead_loci(tmp_path):
    log, _ = run_step("stats_summary", sumstats(hits=[(100, 9), (110, 8), (5000, -7)]), {}, tmp_path)
    assert log["verdict"] == "pass"
    assert 0.95 < log["metrics"]["lambda_gc"] < 1.05
    assert log["metrics"]["n_loci"] == 2  # 100 and 110 are within 500 kb
    assert (tmp_path / "artifacts" / "lead_loci.tsv").exists()


def test_inflation_warns(tmp_path):
    log, _ = run_step("stats_summary", sumstats(inflation=1.3), {}, tmp_path)
    assert log["verdict"] == "warn" and log["metrics"]["lambda_gc"] > 1.2


def test_lambda_1000_maf_lambda_and_suggestive(tmp_path):
    df = sumstats(inflation=1.2, hits=[(100, 5)])
    df["eaf"] = np.where(np.arange(len(df)) % 10 == 0, 0.005, 0.3)  # 10% rare variants
    log, _ = run_step("stats_summary", df, {"n_cases": 10000, "n_controls": 40000}, tmp_path)
    m = log["metrics"]
    lam = m["lambda_gc"]
    expected = 1 + (lam - 1) * (1 / 10000 + 1 / 40000) / (2 / 1000)
    assert abs(m["lambda_1000"] - expected) < 1e-3 and m["lambda_1000"] < lam
    assert abs(m["lambda_maf01"] - lam) < 0.05  # common variants share the same inflation here
    assert m["n_suggestive"] >= m["n_genome_wide_sig"] and m["n_suggestive"] > 0
    (tmp_path / "x").mkdir()
    log2, _ = run_step("stats_summary", sumstats(), {}, tmp_path / "x")  # no counts or EAF: those metrics are left out
    assert "lambda_1000" not in log2["metrics"] and "lambda_maf01" not in log2["metrics"]
