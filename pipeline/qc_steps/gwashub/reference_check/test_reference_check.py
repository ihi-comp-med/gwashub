import gzip

import numpy as np
import pandas as pd

from _lib.testing import run_step


def panel(tmp_path, rows):
    res = tmp_path / "res"
    res.mkdir(exist_ok=True)
    df = pd.DataFrame(rows, columns=["chr", "pos", "rsid", "ref", "alt", "af_EUR"])
    with gzip.open(res / "ref_panel_grch38.tsv.gz", "wt") as f:
        f.write("# test panel\n")
        df.to_csv(f, sep="\t", index=False)
    return res


def test_alignment_cases(tmp_path):
    res = panel(tmp_path, [
        ("1", 100, "rs1", "G", "A", 0.2),   # exact match
        ("1", 200, "rs2", "G", "A", 0.2),   # allele flip
        ("1", 300, "rs3", "G", "A", 0.2),   # strand: C/T reported for G/A
        ("1", 400, "rs4", "T", "A", 0.1),   # palindromic, resolvable by EAF
        ("1", 500, "rs5", "T", "A", 0.45),  # palindromic, ambiguous -> dropped
        ("1", 600, "rs6", "G", "A", 0.2),   # C/A reported for G/A: mismatched -> dropped
    ])
    df = pd.DataFrame({
        "chr": "1", "pos": [100, 200, 300, 400, 500, 600, 700],
        "ea": ["A", "G", "T", "T", "A", "C", "A"], "oa": ["G", "A", "C", "A", "T", "A", "G"],
        "beta": [0.5] * 7, "se": 0.1, "p": 0.01, "eaf": [0.2, 0.8, 0.2, 0.9, 0.45, 0.2, 0.3],
    })
    log, out = run_step("reference_check", df, {"ancestry": "EUR"}, tmp_path, resources=res)
    out = out.set_index("pos")
    assert set(out.index) == {100, 200, 300, 400, 700}  # 500 ambiguous, 600 mismatched, 700 not in panel (kept)
    assert out.loc[100, "beta"] == 0.5 and out.loc[100, "ea"] == "A"
    assert out.loc[200, "beta"] == -0.5 and np.isclose(out.loc[200, "eaf"], 0.2) and out.loc[200, "ea"] == "A"
    assert out.loc[300, "beta"] == 0.5 and (out.loc[300, "ea"], out.loc[300, "oa"]) == ("A", "G")
    # rs4: reported EA=T with EAF 0.9; ALT=A has panel AF 0.1 -> T really is the REF -> negate
    assert out.loc[400, "beta"] == -0.5 and np.isclose(out.loc[400, "eaf"], 0.1)
    m = log["metrics"]
    assert (m["palindromic_dropped"], m["mismatched"], m["not_in_reference"]) == (1, 1, 1)


def _many(n, flip_columns=False, shift=0):
    rng = np.random.default_rng(0)
    af = rng.uniform(0.05, 0.5, n)
    rows = [("2", 1000 + i, f"rs{i}", "C", "T", af[i]) for i in range(n)]
    ea, oa = ("C", "T") if flip_columns else ("T", "C")
    df = pd.DataFrame({"chr": "2", "pos": [1000 + i + shift for i in range(n)], "ea": ea, "oa": oa,
                       "beta": 0.1, "se": 0.1, "p": 0.5, "eaf": af})
    return rows, df


def test_detects_swapped_allele_columns(tmp_path):
    rows, df = _many(300, flip_columns=True)
    log, _ = run_step("reference_check", df, {"ancestry": "EUR"}, tmp_path, resources=panel(tmp_path, rows))
    assert log["verdict"] == "fail" and "swapped" in log["messages"][0]


def test_detects_wrong_build(tmp_path):
    rows, df = _many(300, shift=100_000)
    log, _ = run_step("reference_check", df, {"ancestry": "EUR"}, tmp_path, resources=panel(tmp_path, rows))
    assert log["verdict"] == "fail" and "genome build" in log["messages"][0]


def test_clean_data_passes(tmp_path):
    rows, df = _many(300)
    log, out = run_step("reference_check", df, {"ancestry": "EUR"}, tmp_path, resources=panel(tmp_path, rows))
    assert log["verdict"] == "pass" and len(out) == 300 and log["metrics"]["eaf_correlation"] > 0.99
