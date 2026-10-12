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
def test_parse_writes_removed_rows_as_received(tmp_path):
    df = upload()
    df = df.astype({"SE": object, "BETA": object, "FRQ": object})
    df.loc[1, "SE"] = -1
    df.loc[2, "FRQ"] = 1.5
    df.loc[3, "CHR"] = "MT"
    df.loc[4, "A1"] = "a"
    df.loc[5, "FRQ"] = 0
    df.loc[6, ["BETA", "SE"]] = ["oops", ""]  # two problems in one row
    log, out = run_step("parse", df, {"column_mapping": MAP}, tmp_path)
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t", dtype=str).set_index("line")
    assert list(removed.index) == ["2", "3", "4", "5", "6", "7"] and len(out) + len(removed) == len(df)
    assert "EXTRA" not in removed and removed.loc["4", "CHR"] == "MT" and removed.loc["5", "A1"] == "a"  # as received
    assert removed["reason"].to_dict() == {
        "2": "SE not a positive number", "3": "EAF outside 0-1", "4": "CHR not 1-22, X or Y", "5": "EA and OA identical",
        "6": "EAF 0 or 1 (monomorphic)", "7": "BETA not a number; SE missing"}
    art = {a["path"]: a for a in log["artifacts"]}
    assert art["removed_rows.tsv.gz"]["rows"] == 6 == log["metrics"]["rows_removed"]


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


@needs_image("gwashub/base-r")
def test_parse_stops_on_the_wrong_effect_type(tmp_path):
    """Odds ratios uploaded as BETA (all positive, around 1), or log-odds uploaded as odds ratios (negative values)."""
    df = upload()
    df["BETA"] = np.exp(df["BETA"])
    log, _ = run_step("parse", df, {"column_mapping": MAP}, tmp_path)
    assert log["verdict"] == "fail" and "odds ratios" in log["messages"][0]
    log, _ = run_step("parse", upload(), {"column_mapping": MAP, "effect_type": "or"}, tmp_path)
    assert log["verdict"] == "fail" and "log-odds" in log["messages"][0]


@needs_image("gwashub/base-r")
def test_parse_stops_on_a_truncated_gzip(tmp_path):
    src = tmp_path / "upload.tsv.gz"
    with gzip.open(src, "wt") as f:
        upload(n=20000).to_csv(f, sep="\t", index=False)
    data = src.read_bytes()
    src.write_bytes(data[: len(data) // 2])
    log, _ = run_step("parse", str(src), {"column_mapping": MAP}, tmp_path, raw=True)
    assert log["verdict"] == "fail" and "cut short" in log["messages"][0]


@needs_image("gwashub/base-r")
def test_parse_header_lines_xy_and_allele_codes(tmp_path):
    """Header lines inside the data (files joined together) are removed and named; chromosome 25 / XY is X; a leading
    zero (01, chr09) is dropped; alleles coded 1/2 are named."""
    df = upload().astype(str)
    df.iloc[100] = df.columns  # the header again
    df.loc[101:102, "CHR"] = ["XY", "25"]
    df.loc[103:105, "CHR"] = ["01", "chr09", "023"]
    df.loc[200:239, ["A1", "A2"]] = ["1", "2"]
    log, out = run_step("parse", df, {"column_mapping": MAP}, tmp_path)
    msgs = " ".join(log["messages"])
    assert log["metrics"]["header_lines"] == 1 and "repeated header lines" in msgs
    assert out.set_index("rsid").loc[["rs101", "rs102"], "chr"].tolist() == ["X", "X"] and "read as X" in msgs
    assert out.set_index("rsid").loc[["rs103", "rs104", "rs105"], "chr"].tolist() == ["1", "9", "X"] and "leading zero" in msgs
    assert "coded 1/2" in msgs
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t", dtype=str).set_index("line")
    assert removed.loc["101", "reason"] == "repeated header line"


def genome_wide(n=120_000, seed=2):
    """A clean genome-wide-sized upload: every autosome, unrounded values, N of the study's total."""
    rng = np.random.default_rng(seed)
    beta, se = rng.normal(0, 0.02, n), rng.uniform(0.01, 0.05, n)
    from scipy import stats
    return pd.DataFrame({"SNP": [f"rs{i}" for i in range(n)], "CHR": np.repeat([str(c) for c in range(1, 23)], -(-n // 22))[:n],
                         "BP": np.arange(n) + 1, "A1": "C", "A2": "A", "FRQ": rng.uniform(0.01, 0.99, n),
                         "BETA": beta, "SE": se, "P": 2 * stats.norm.sf(np.abs(beta / se)), "N": 1000})


@needs_image("gwashub/base-r")
def test_parse_warns_on_rounding_n_and_missing_autosomes(tmp_path):
    params = {"column_mapping": {**MAP, "n": "N"}, "n_cases": 400, "n_controls": 600}
    log, _ = run_step("parse", genome_wide(), params, tmp_path)
    assert log["verdict"] == "pass", log
    df = genome_wide()
    df["SE"] = df["SE"].round(2)
    df.loc[df["CHR"] == "22", "CHR"] = "21"
    df.loc[:9, "N"] = 5000
    (tmp_path / "b").mkdir()
    log, _ = run_step("parse", df, params, tmp_path / "b")
    m, msgs = log["metrics"], " ".join(log["messages"])
    assert log["verdict"] == "warn"
    assert m["rounded_columns"] == "SE" and "SE has only" in msgs
    assert m["n_above_total"] == 10 and m["autosomes_absent"] == 1 and "chromosome 22" in msgs


@needs_image("gwashub/base-r")
def test_one_allele_missing(tmp_path):
    """A row with an effect allele but no other allele (or the reverse) is removed with its reason; it used to crash
    the removal reasons (EA == OA compared against a missing value)."""
    df = upload()
    df.loc[0, "A2"] = None
    df.loc[1, "A1"] = None
    src = tmp_path / "upload.csv"
    df.to_csv(src, index=False)
    log, out = run_step("parse", str(src), {"column_mapping": MAP}, tmp_path)
    assert log["verdict"] == "pass", log
    assert len(out) == len(df) - 2 and log["metrics"]["identical_alleles"] == 0
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t")
    assert sorted(removed["reason"]) == ["EA missing", "OA missing"]


@needs_image("gwashub/base-r")
def test_censored_counts_are_missing_not_their_bound(tmp_path):
    """A count given as a bound ("<=40", as All of Us hides small counts) is unknown: read as missing and filled from
    the study details, not as 40; "1,020" is still read as 1020. Parse says how many there were."""
    df = upload()
    df["N"] = "1,020"
    df.loc[:9, "N"] = "<=40"
    df.loc[10:14, "N"] = "< 20"
    src = tmp_path / "upload.csv"
    df.to_csv(src, index=False)
    log, out = run_step("parse", str(src), {"column_mapping": {**MAP, "n": "N"}, "n_cases": 100, "n_controls": 900}, tmp_path)
    assert log["verdict"] == "pass", log
    by_rsid = out.set_index("rsid")["n"]
    assert by_rsid["rs0"] == 1000 and by_rsid["rs12"] == 1000 and by_rsid["rs20"] == 1020  # filled from the study (100 + 900)
    assert log["metrics"]["censored_n"] == 15
    assert any("15 values given as a bound (<=40, < 20)" in m for m in log["messages"])
