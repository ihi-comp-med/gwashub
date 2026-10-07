import gzip

import numpy as np
import pandas as pd

from _lib.testing import needs_image, run_step

MAP = {"rsid": "SNP", "chr": "CHR", "pos": "BP", "ea": "A1", "oa": "A2", "eaf": "FRQ", "beta": "BETA", "se": "SE", "p": "P"}


def upload(n=2000, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"SNP": [f"rs{i}" for i in range(n)], "CHR": rng.choice(["1", "chr2", "23", "X"], n),
                         "BP": rng.integers(1, 10**6, n), "A1": rng.choice(["c", "C", "G", "T"], n), "A2": "A",
                         "FRQ": rng.uniform(0.01, 0.99, n).round(3), "BETA": rng.normal(0, 0.1, n).round(4),
                         "SE": 0.05, "P": rng.uniform(0, 1, n).round(4), "EXTRA": "x"})


@needs_image("gwashub/base-r")
def test_parse_writes_gwashub_format(tmp_path):
    df = upload()
    df.loc[0, "P"] = 0  # recoded, not dropped
    df.loc[1, "SE"] = -1  # invalid required value: dropped
    df.loc[2, "FRQ"] = 1.5  # EAF out of bounds: EAF is required, so dropped
    df.loc[3, "CHR"] = "MT"  # not a chromosome: dropped
    df.loc[4, "A1"] = "a"  # EA = OA after upper-casing: dropped
    src = tmp_path / "upload.csv.gz"
    with gzip.open(src, "wt") as f:
        df.to_csv(f, index=False)
    log, out = run_step("parse", str(src), {"column_mapping": MAP, "n_cases": 100, "n_controls": 900}, tmp_path)
    assert log["verdict"] == "pass", log
    assert list(out.columns) == ["chr", "pos", "ea", "oa", "beta", "se", "p", "eaf", "n", "ncase", "ncontrol", "rsid"]
    assert len(out) == len(df) - 4 and log["metrics"]["rows_removed"] == 4 and log["metrics"]["identical_alleles"] == 1
    assert set(out["chr"]) <= {"1", "2", "X"} and out["ea"].str.fullmatch("[ACGT]+").all()
    assert out.loc[out["rsid"] == "rs0", "p"].iloc[0] > 0 and log["metrics"]["p_zero_recoded"] == 1
    assert "rs2" not in out["rsid"].tolist()
    assert (out["n"] == 1000).all() and (out["ncase"] == 100).all() and log["metrics"]["filled_n"] == len(df)


@needs_image("gwashub/base-r")
def test_parse_or_and_log10p(tmp_path):
    df = upload()
    df["OR"] = np.exp(df["BETA"])
    df["LP"] = -np.log10(df["P"].clip(lower=1e-10))
    mapping = {**{k: v for k, v in MAP.items() if k not in ("p", "beta")}, "beta": "OR", "log10p": "LP"}
    log, out = run_step("parse", df, {"column_mapping": mapping, "effect_type": "or",
                                      "step": {"p_from_log10p": "log10p"}}, tmp_path)
    assert log["verdict"] == "pass" and log["metrics"]["or_to_beta"] > 0, log
    assert np.allclose(out["beta"], np.log(df.set_index("SNP").loc[out["rsid"], "OR"]), atol=1e-6)
    assert np.allclose(out["p"], 10 ** -df.set_index("SNP").loc[out["rsid"], "LP"], rtol=1e-6)


@needs_image("gwashub/base-r")
def test_parse_fails_on_neglog10p_and_unmapped(tmp_path):
    df = upload()
    df["P"] = -np.log10(df["P"].clip(lower=1e-6))  # -log10 P in the P column
    log, _ = run_step("parse", df, {"column_mapping": MAP}, tmp_path)
    assert log["verdict"] == "fail" and "-log10" in log["messages"][0]
    log, _ = run_step("parse", upload(), {"column_mapping": {k: v for k, v in MAP.items() if k != "se"}}, tmp_path)
    assert log["verdict"] == "fail" and "se" in log["messages"][0]
    log, _ = run_step("parse", upload(), {"column_mapping": {k: v for k, v in MAP.items() if k != "eaf"}}, tmp_path)
    assert log["verdict"] == "fail" and "eaf" in log["messages"][0]  # EAF is required


@needs_image("gwashub/base-r")
def test_parse_reports_and_wrangles(tmp_path):
    """Parse reports missing and out-of-bounds values per column, chromosomes and indels; fills P from BETA/SE where P
    is missing (not where it is out of bounds) and N from cases + controls; removes EAF of 0 or 1."""
    df = upload()
    df["NCASE"], df["NCONTROL"] = 200, 800
    df.loc[0:9, "P"] = np.nan  # missing: computed from BETA/SE
    df.loc[10:14, "P"] = 1.7  # out of bounds: reported, row removed
    df.loc[15:16, "FRQ"] = [0.0, 1.0]  # monomorphic: removed
    df.loc[17, "A1"], df.loc[17, "A2"] = "AT", "A"  # an indel: counted, kept
    mapping = {**MAP, "ncase": "NCASE", "ncontrol": "NCONTROL"}
    log, out = run_step("parse", df, {"column_mapping": mapping}, tmp_path)
    m, msgs = log["metrics"], " ".join(log["messages"])
    assert log["verdict"] == "pass", log
    assert m["p_computed"] == 10 and m["eaf_monomorphic"] == 2 and m["indels"] == 1
    assert m["n_from_cases_controls"] == len(df) and (out["n"] == 1000).all()
    rows = out.set_index("rsid")
    b, se = df.set_index("SNP").loc["rs3", ["BETA", "SE"]]
    from scipy import stats
    assert abs(rows.loc["rs3", "p"] - 2 * stats.norm.sf(abs(b / se))) < 1e-9
    assert not {"rs10", "rs14", "rs15", "rs16"} & set(out["rsid"]) and "rs17" in out["rsid"].tolist()
    assert "Chromosomes present" in msgs and "P: 10 missing" in msgs and "5 out of bounds" in msgs
    summary = pd.read_csv(tmp_path / "artifacts" / "parse_summary.tsv", sep="\t").set_index("column")
    assert summary.loc["p", "missing"] == 10 and summary.loc["p", "out_of_bounds"] == 5 and summary.loc["p", "missing_pct"] == 0.5
    chroms = pd.read_csv(tmp_path / "artifacts" / "chromosomes.tsv", sep="\t")
    assert {"as_received", "chromosome", "rows"} <= set(chroms.columns) and chroms["rows"].sum() == len(df)
