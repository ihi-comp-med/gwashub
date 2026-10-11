import os

import pandas as pd

from _lib.testing import needs_image, run_step
from devtools.synth import params_for


@needs_image("gwashub/base-r", "gwashub/vcf")
def test_removed_rows_have_stage_and_reason(synth_small, tmp_path):
    entry = next(e for e in synth_small["manifest"] if e["build"] == "GRCh37")
    params = params_for(entry)
    (tmp_path / "parse").mkdir()
    _, gwas = run_step("parse", os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), params, tmp_path / "parse")
    gwas.loc[5, "pos"] = 10**9  # beyond the end of the chromosome: REF can't match
    gwas = pd.concat([gwas, gwas.iloc[[10]]], ignore_index=True)  # a duplicate, removed after the liftover
    log, out = run_step("harmonise_liftover", gwas, params, tmp_path, resources=synth_small["resources"])
    assert log["verdict"] in ("pass", "warn"), log
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t", dtype=str)
    assert list(removed.columns[:5]) == ["stage", "reason", "line", "chr_input", "pos_input"]
    g2v = removed[removed["stage"] == "gwas2vcf"]
    assert len(g2v) == log["metrics"].get("not_read", 0) + log["metrics"]["not_harmonised"]
    m = log["metrics"]
    assert m["harmonised"] + m["not_harmonised"] + m.get("not_read", 0) == m["gwas2vcf_total"]  # each variant counted once
    far = g2v[g2v["line"] == "6"]  # data line 6 = row 5
    assert len(far) == 1 and far["reason"].iloc[0] == "REF does not match the reference genome" and far["pos_input"].iloc[0] == str(10**9)
    assert (removed["stage"] == "liftover").sum() == log["metrics"]["unmapped"]
    assert removed["reason"].str.startswith("duplicate").sum() == log["metrics"]["duplicates"] >= 1
    assert len(out) + len(removed) == len(gwas)
    art = {a["path"]: a for a in log["artifacts"]}
    assert art["removed_rows.tsv.gz"]["rows"] == len(removed)


@needs_image("gwashub/base-r", "gwashub/vcf")
def test_names_the_build_when_the_declared_one_is_wrong(synth_small, tmp_path):
    entry = next(e for e in synth_small["manifest"] if e["build"] == "GRCh38")
    params = params_for(entry)
    (tmp_path / "parse").mkdir()
    _, gwas = run_step("parse", os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), params, tmp_path / "parse")
    log, _ = run_step("harmonise_liftover", gwas, {**params, "build": "GRCh37"}, tmp_path, resources=synth_small["resources"])
    m = log["metrics"]
    assert log["verdict"] == "warn" and m["build_match_GRCh38"] > 0.9 and m["build_match_GRCh37"] < 0.7, log
    assert any("look like GRCh38" in msg for msg in log["messages"])


def test_removed_row_with_two_alt_alleles():
    """bcftools +liftover makes a record multi-allelic when the GRCh38 base is neither allele (one value per ALT, "."
    for the added one): the removed-rows table takes it without failing on the numbers."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("hl", os.path.join(os.path.dirname(__file__), "harmonise_liftover.py"))
    hl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hl)
    q = pd.DataFrame([["1", "1000", "rs1", "C", "A,T", ".,0.01", ".,0.002", ".,0.7459", ".,0.3", "500", ".", ".", ".,0.18", "1", "900", "."],
                      ["1", "2000", "rs2", "G", "A", "0.02", "0.003", "2", "0.4", "500", ".", ".", "0.01", "1", "1900", "400"]],
                     columns=hl.FIELDS)
    r = hl.vcf_rows(q)
    assert r["p"].isna().iloc[0] and abs(r["p"].iloc[1] - 0.01) < 1e-12 and r["ea"].iloc[0] == "A,T"


@needs_image("gwashub/base-r", "gwashub/vcf")
def test_sample_sizes_come_back_unchanged(synth_small, tmp_path):
    """n, ncase and ncontrol leave the step as they came in, even when n isn't ncase + ncontrol (an effective N, say):
    controls travel in their own FORMAT field, not as n - ncase."""
    entry = next(e for e in synth_small["manifest"] if e["build"] == "GRCh37")
    params = params_for(entry)
    (tmp_path / "parse").mkdir()
    _, gwas = run_step("parse", os.path.join(synth_small["dir"], "fake_gwas", entry["file"]), params, tmp_path / "parse")
    gwas["ncase"], gwas["ncontrol"], gwas["n"] = 1000, 3000, 2900  # n below cases + controls
    log, out = run_step("harmonise_liftover", gwas, params, tmp_path, resources=synth_small["resources"])
    assert log["verdict"] in ("pass", "warn"), log
    assert len(out) and (out["ncase"] == 1000).all() and (out["ncontrol"] == 3000).all() and (out["n"] == 2900).all()
