"""sgc/qc_munge: SGC's qc_munge.R on a small GRCh37 file of real SNPs (conftest.py)."""
import numpy as np
import pandas as pd

from _lib.common import STANDARD
from _lib.testing import needs_image, run_step

IMAGE = "sgc/mungesumstats"


@needs_image(IMAGE)
def test_qc_munge_runs_sgc_script_and_returns_gwashub_format(tmp_path, grch37_file):
    params = {"build": "GRCh37", "cohort": "Test cohort", "phenotype": "Atopic dermatitis", "ancestry": "EUR", "sex": "All",
              "n_cases": 2000, "n_controls": 18000}
    log, out = run_step("sgc/qc_munge", grch37_file, params, tmp_path)
    assert log["verdict"] in ("pass", "warn"), log
    assert set(out.columns) <= set(STANDARD) and {"chr", "pos", "ea", "oa", "beta", "se", "p"} <= set(out.columns)
    # Lifted to GRCh38: the same rsIDs at different positions.
    both = grch37_file.merge(out, on="rsid", suffixes=("_37", "_38"))
    assert len(both) > 0.8 * len(grch37_file) and (both["pos_37"] != both["pos_38"]).mean() > 0.9
    # SGC's filters: INFO < 0.3 and frequency < 0.005 gone; effects kept, with signs following the effect allele.
    assert not set(grch37_file.loc[:79, "rsid"]) & set(out["rsid"])
    same = both["ea_37"] == both["ea_38"]
    assert np.allclose(both.loc[same, "beta_37"], both.loc[same, "beta_38"], atol=1e-9)
    assert np.allclose(both.loc[~same, "beta_37"], -both.loc[~same, "beta_38"], atol=1e-9)
    assert (out["n"] == 20000).all()
    art = tmp_path / "artifacts"
    assert (art / "Test_cohort_Atopic_dermatitis_EUR_All.tsv.gz").exists()  # qc_munge.R's own output, SGC's columns
    removed = pd.read_csv(art / "removed_rows.tsv.gz", sep="\t", dtype=str)
    assert len(removed) >= 80 and log["metrics"]["build_passed"] == "GRCh37"
