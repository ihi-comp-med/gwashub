import pandas as pd

from _lib.testing import needs_image, run_step


def frame():
    return pd.DataFrame({
        "chr": ["1", "1", "1", "2", "2", "X", "Y", "3", "3", "3"],
        "pos": [10, 20, 30, 40, 50, 60, 70, 80, 80, 90],
        "ea": ["A", "C", "G", "T", "AT", "A", "C", "G", "C", "A"],
        "oa": ["G", "A", "A", "C", "A", "G", "A", "C", "G", "G"],
        "beta": [0.1, 25.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, -0.1, 0.1],
        "se": [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.5],
        "p": [0.5] * 10,
        "eaf": [0.3, 0.3, 0.4, 0.6, 0.3, 0.3, 0.3, 0.3, 0.7, 0.002],
        "n": [1000] * 9 + [None], "info": [0.9, 0.9, 0.9, 0.9, 0.9, None, 0.5, 0.9, 0.9, 0.9],
    })


def rng(on=True, min=None, max=None):
    """A range value: each limit given is switched on."""
    return {"on": on, "min_on": min is not None, "min": min, "max_on": max is not None, "max": max}


@needs_image("gwashub/base-r")
def test_defaults(tmp_path):
    log, out = run_step("filter", frame(), {}, tmp_path)
    m = log["metrics"]
    # Only the duplicate at 3:80 (alleles swapped) goes: the |BETA| filter is off by default, so |BETA| 25 stays;
    # missing INFO is filled with 1; a missing N stays.
    assert m["removed_beta_outside_the_limits"] == 0 and m["removed_duplicate_variant"] == 1 and "removed_n_missing" not in m
    assert m["removed_info_missing"] == 0 and m["filled_info"] == 1
    assert sorted(out["pos"]) == [10, 20, 30, 40, 50, 60, 70, 80, 90]
    assert out.loc[out["pos"] == 60, "info"].iloc[0] == 1 and log["verdict"] == "warn"  # 1 of 10 removed, above 5%


@needs_image("gwashub/base-r")
def test_ranges_switch_each_limit_on_or_off(tmp_path):
    """A range keeps min <= value <= max over the limits switched on; a limit switched off doesn't apply."""
    step = {"chromosomes": "1-22", "drop_indels": True, "missing_info": "remove the variant",
            "info": rng(min=0.8), "maf": rng(min=0.01), "se": rng(max=0.1), "beta": rng(on=False, max=20)}
    log, out = run_step("filter", frame(), {"step": step}, tmp_path)
    m = log["metrics"]
    assert set(out["chr"]) <= {"1", "2", "3"} and not (out["ea"].str.len() > 1).any()
    assert 25.0 in out["beta"].tolist()  # |BETA| filter switched off
    assert m["removed_maf_outside_the_limits"] == 0  # the MAF 0.002 row went earlier, on SE 0.5 > max 0.1
    assert m["removed_se_outside_the_limits"] == 1 and 0.002 not in out["eaf"].tolist()
    # INFO: the missing one (chr X) went with the chromosome; INFO 0.5 (chr Y) too; nothing else is below 0.8.
    assert m["removed_info_outside_the_limits"] == 0 and (out["info"] >= 0.8).all()
    (tmp_path / "b").mkdir()
    maxonly = {"maf": rng(max=0.35)}  # a maximum only: keep MAF <= 0.35
    log2, out2 = run_step("filter", frame(), {"step": maxonly}, tmp_path / "b")
    assert (out2["eaf"].clip(upper=1 - out2["eaf"]) <= 0.35).all() and log2["metrics"]["removed_maf_outside_the_limits"] == 2


@needs_image("gwashub/base-r")
def test_filter_on_a_missing_column_fails_clearly(tmp_path):
    log, _ = run_step("filter", frame().drop(columns=["info"]), {"step": {"info": rng(min=0.8)}}, tmp_path)
    assert log["verdict"] == "fail" and "INFO" in log["messages"][0]


@needs_image("gwashub/base-r")
def test_removed_rows_have_one_reason_each(tmp_path):
    log, out = run_step("filter", frame(), {}, tmp_path)
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t", dtype={"chr": str})
    assert sorted(removed["pos"]) == [20, 80] and len(removed) + len(out) == len(frame())
    assert removed.set_index("pos").loc[20, "reason"].startswith("|BETA| outside the limits")
    assert removed.set_index("pos").loc[80, "reason"].startswith("duplicate variant")
    art = {a["path"]: a for a in log["artifacts"]}
    assert art["removed_rows.tsv.gz"]["rows"] == 2
    summary = pd.read_csv(tmp_path / "artifacts" / "filter_summary.tsv", sep="\t").set_index("reason")["removed"]
    assert removed["reason"].value_counts().to_dict() == summary[summary > 0].to_dict()


@needs_image("gwashub/base-r")
def test_no_removed_rows_file_when_nothing_is_removed(tmp_path):
    log, _ = run_step("filter", frame().iloc[[0, 2, 3]], {}, tmp_path)
    assert not (tmp_path / "artifacts" / "removed_rows.tsv.gz").exists()
    assert [a["path"] for a in log["artifacts"]] == ["filter_summary.tsv"]


@needs_image("gwashub/base-r")
def test_strand_ambiguous_snps_by_maf(tmp_path):
    """A/T and C/G SNPs above the MAF maximum go when the rule is on; other SNPs and indels stay whatever their MAF."""
    df = pd.DataFrame({"chr": "1", "pos": [10, 20, 30, 40, 50, 60], "ea": ["A", "C", "T", "A", "A", "AT"],
                       "oa": ["T", "G", "A", "G", "T", "A"], "beta": 0.1, "se": 0.01, "p": 0.5,
                       "eaf": [0.45, 0.58, 0.2, 0.5, 0.1, 0.5]})
    log, out = run_step("filter", df, {}, tmp_path)
    assert len(out) == len(df) and log["metrics"]["removed_strand_ambiguous_snp_with_maf_outside_the_limits"] == 0  # off by default
    (tmp_path / "b").mkdir()
    log, out = run_step("filter", df, {"step": {"palindromic": rng(max=0.4)}}, tmp_path / "b")
    assert sorted(out["pos"]) == [30, 40, 50, 60] and log["metrics"]["removed_strand_ambiguous_snp_with_maf_outside_the_limits"] == 2


@needs_image("gwashub/base-r")
def test_minor_allele_count(tmp_path):
    """MAC = 2 x MAF x N: a rare variant in a small sample goes, the same frequency in a large one stays."""
    df = pd.DataFrame({"chr": "1", "pos": [10, 20, 30], "ea": ["A", "C", "G"], "oa": ["G", "T", "A"], "beta": 0.1, "se": 0.01,
                       "p": 0.5, "eaf": [0.005, 0.005, 0.3], "n": [400, 100000, 400]})  # MAC 4, 1000, 240
    log, out = run_step("filter", df, {"step": {"mac": rng(min=6)}}, tmp_path)
    assert sorted(out["pos"]) == [20, 30] and log["metrics"]["removed_mac_outside_the_limits"] == 1
