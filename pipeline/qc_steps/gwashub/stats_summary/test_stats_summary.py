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
