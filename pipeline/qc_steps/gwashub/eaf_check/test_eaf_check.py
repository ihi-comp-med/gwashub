import gzip

import numpy as np
import pandas as pd

from _lib.testing import needs_image, run_step


def data(tmp_path, n=400, seed=3):
    """A GRCh38 dataset and a matching 1000 Genomes reference; the first 40 variants' EAFs are far off."""
    rng = np.random.default_rng(seed)
    ref_af = rng.uniform(0.1, 0.9, n).round(3)
    eaf = (ref_af + rng.normal(0, 0.01, n)).clip(0.01, 0.99).round(3)
    eaf[:40] = np.where(ref_af[:40] > 0.5, ref_af[:40] - 0.4, ref_af[:40] + 0.4).round(3)
    gwas = pd.DataFrame({"chr": "1", "pos": np.arange(1, n + 1) * 100, "ea": "A", "oa": "G", "beta": 0.01, "se": 0.01,
                         "p": 0.5, "eaf": eaf, "n": 1000})
    gwas.loc[n - 1, "pos"] = 10**8  # not in the reference: always kept
    res = tmp_path / "res"
    res.mkdir()
    ref = pd.DataFrame({"id": [f"v{i}" for i in range(n)], "chr": "chr1", "pos_b38": np.arange(1, n + 1) * 100, "ref": "G",
                        "alt": "A", "eur_afreq": ref_af})
    with gzip.open(res / "all_afreq_b38.tsv.gz", "wt") as f:
        ref.to_csv(f, sep="\t", index=False)
    return gwas, res


@needs_image("gwashub/vcf")
def test_default_removes_nothing(tmp_path):
    gwas, res = data(tmp_path)
    log, out = run_step("eaf_check", gwas, {"ancestry": "EUR"}, tmp_path, resources=res)
    assert log["verdict"] == "warn" and log["metrics"]["large_diff"] == 40 and len(out) == len(gwas)
    assert "rows_removed" not in log["metrics"] and not (tmp_path / "artifacts" / "removed_rows.tsv.gz").exists()


@needs_image("gwashub/vcf")
def test_remove_large_diff(tmp_path):
    gwas, res = data(tmp_path)
    log, out = run_step("eaf_check", gwas, {"ancestry": "EUR", "step": {"remove_large_diff": True}}, tmp_path, resources=res)
    assert log["verdict"] == "warn" and log["metrics"]["rows_removed"] == 40  # still judged on the share before removal
    assert len(out) == len(gwas) - 40 and set(out["pos"]) == set(gwas["pos"][40:]) and 10**8 in out["pos"].tolist()
    assert out["n"].dtype.kind == "i"
    removed = pd.read_csv(tmp_path / "artifacts" / "removed_rows.tsv.gz", sep="\t")
    assert len(removed) == 40 and (removed["eaf_diff"] > 0.2).all() and removed["reason"].str.contains("EUR").all()


@needs_image("gwashub/vcf")
def test_names_maf_given_as_eaf(tmp_path):
    """An EAF column holding the minor allele frequency agrees with the reference only once both are folded."""
    gwas, res = data(tmp_path)
    ref = pd.read_csv(res / "all_afreq_b38.tsv.gz", sep="\t")
    gwas["eaf"] = np.minimum(ref["eur_afreq"], 1 - ref["eur_afreq"]).to_numpy()
    log, _ = run_step("eaf_check", gwas, {"ancestry": "EUR"}, tmp_path, resources=res)
    assert log["verdict"] == "warn" and log["metrics"]["large_diff_fraction_as_maf"] == 0
    assert any("minor allele frequency" in m for m in log["messages"])
    (tmp_path / "b").mkdir()
    gwas["eaf"] = 1 - ref["eur_afreq"].to_numpy()  # swapped allele columns: also agree once folded, but are not MAF
    log, _ = run_step("eaf_check", gwas, {"ancestry": "EUR"}, tmp_path / "b", resources=res)
    assert not any("minor allele frequency" in m for m in log["messages"]) and any("swapped" in m for m in log["messages"])


@needs_image("gwashub/vcf")
def test_names_strand_problems_in_ambiguous_snps(tmp_path):
    """A/T SNPs reported on the other strand look like their EAF is 1 - the reference's; other SNPs are fine."""
    gwas, res = data(tmp_path, n=800)
    ref = pd.read_csv(res / "all_afreq_b38.tsv.gz", sep="\t")
    gwas["eaf"] = ref["eur_afreq"].to_numpy()
    amb = np.arange(len(gwas)) % 2 == 0
    gwas.loc[amb, ["ea", "oa"]] = ["A", "T"]
    ref.loc[amb, ["ref", "alt"]] = ["T", "A"]
    far = amb & (np.abs(2 * ref["eur_afreq"] - 1) > 0.25).to_numpy()  # far enough from 0.5 that 1 - f is a large difference
    gwas.loc[far, "eaf"] = 1 - gwas.loc[far, "eaf"]
    with gzip.open(res / "all_afreq_b38.tsv.gz", "wt") as f:
        ref.to_csv(f, sep="\t", index=False)
    log, _ = run_step("eaf_check", gwas, {"ancestry": "EUR"}, tmp_path, resources=res)
    m = log["metrics"]
    assert log["verdict"] == "warn" and m["ambiguous_large_diff_fraction"] > 0.3 and m["other_large_diff_fraction"] == 0
    assert any("Strand-ambiguous" in msg for msg in log["messages"])


@needs_image("gwashub/vcf")
def test_names_a_better_fitting_ancestry(tmp_path):
    """Frequencies that match EAS, uploaded as EUR: the step names EAS."""
    gwas, res = data(tmp_path)
    ref = pd.read_csv(res / "all_afreq_b38.tsv.gz", sep="\t")
    rng = np.random.default_rng(5)
    ref["eas_afreq"] = (ref["eur_afreq"] + rng.choice([-0.25, 0.25], len(ref))).clip(0.01, 0.99).round(3)
    gwas["eaf"] = ref["eas_afreq"].to_numpy()
    with gzip.open(res / "all_afreq_b38.tsv.gz", "wt") as f:
        ref.to_csv(f, sep="\t", index=False)
    log, _ = run_step("eaf_check", gwas, {"ancestry": "EUR"}, tmp_path, resources=res)
    assert log["verdict"] == "warn" and log["metrics"]["ancestry_best_fit"] == "EAS"
    assert any("fit 1000 Genomes EAS better than the declared EUR" in m for m in log["messages"])
