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


def test_lambda_1000_for_a_quantitative_trait(tmp_path):
    """Quantitative traits have no cases or controls: λ is rescaled to 1,000 people from the total sample size."""
    log, _ = run_step("stats_summary", sumstats(inflation=1.2), {"trait_type": "quantitative", "n_total": 50000}, tmp_path)
    m = log["metrics"]
    assert abs(m["lambda_1000"] - (1 + (m["lambda_gc"] - 1) * 1000 / 50000)) < 1e-3


def case_control(n=20000, cases=5000, controls=15000, scale=1.0, seed=2):
    """Null log-odds summary statistics whose SEs follow from the counts and EAF (times scale)."""
    rng = np.random.default_rng(seed)
    eaf = rng.uniform(0.05, 0.95, n)
    neff = 4 / (1 / cases + 1 / controls)
    se = np.sqrt(2 / (eaf * (1 - eaf) * neff)) * scale
    z = rng.normal(size=n)
    return pd.DataFrame({"chr": "1", "pos": np.arange(n) * 1000 + 1, "ea": "A", "oa": "G", "beta": z * se, "se": se,
                         "p": 2 * stats.norm.sf(np.abs(z)), "eaf": eaf, "ncase": cases, "ncontrol": controls})


def test_se_agrees_with_sample_size(tmp_path):
    log, _ = run_step("stats_summary", case_control(), {}, tmp_path)
    assert log["verdict"] == "pass" and abs(log["metrics"]["se_ratio_expected"] - 1) < 0.05


def test_linear_scale_effects_and_wrong_n_warn(tmp_path):
    phi = 5000 / 20000
    log, _ = run_step("stats_summary", case_control(scale=phi * (1 - phi)), {}, tmp_path)  # BOLT-LMM style 0/1 scale
    assert log["verdict"] == "warn" and any("linear 0/1 scale" in m for m in log["messages"])
    (tmp_path / "b").mkdir()
    log, _ = run_step("stats_summary", case_control(scale=4), {}, tmp_path / "b")  # N overstated 16-fold
    assert log["verdict"] == "warn" and any("overstated" in m for m in log["messages"])


def test_quantitative_reports_the_implied_trait_sd(tmp_path):
    df = case_control(scale=1).drop(columns=["ncase", "ncontrol"])
    df["se"] = 2.0 / np.sqrt(2 * df["eaf"] * (1 - df["eaf"]) * 30000)
    df["p"] = 2 * stats.norm.sf(np.abs(df["beta"] / df["se"]))
    log, _ = run_step("stats_summary", df, {"trait_type": "quantitative", "n_total": 30000}, tmp_path)
    assert abs(log["metrics"]["implied_trait_sd"] - 2) < 0.01 and "se_ratio_expected" not in log["metrics"]


def test_se_on_another_scale_is_named(tmp_path):
    df = sumstats()
    df["se"] = df["se"] * 3  # e.g. SE of the odds ratio: P no longer matches BETA/SE
    log, _ = run_step("stats_summary", df, {}, tmp_path)
    assert abs(log["metrics"]["z_ratio_median"] - 1 / 3) < 0.01 and any("another scale" in m for m in log["messages"])
