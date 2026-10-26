"""Meta-analysis methods run as a job does (run_method), in their images when GWASHUB_STEP_RUNNER=docker."""
import json
import os

import numpy as np
import pandas as pd
from scipy import stats

from _lib.testing import needs_image

DEFAULT_IMAGES = ("gwashub/base", "gwashub/base-r", "gwashub/vcf", "gwashub/ldsc")


def run_meta(tmp_path, studies, params=None, method="gwashub/gwama", common=None, resources=None):
    """Run a meta-analysis method plus GWASHub's post-processing, as a job does."""
    from pipeline.runner.catalog import load_analyses
    from pipeline.runner.jobspec import run_method
    tmp_path.mkdir(parents=True, exist_ok=True)
    specs = []
    for i, df in enumerate(studies):
        p = tmp_path / f"s{i}.tsv"
        df.to_csv(p, sep="\t", index=False)
        specs.append({"id": i + 1, "label": f"S{i + 1}", "path": str(p), "n_cases": 100, "n_controls": 100, "ancestry": "EUR", "sex": "Combined"})
    out = tmp_path / f"out_{method.replace('/', '_')}"
    result = run_method(load_analyses()[method], specs, params or {}, common or {}, str(out), resources)
    assert result["status"] == "completed", result
    res = pd.read_csv(out / "meta_results.tsv.gz", sep="\t", dtype={"chr": str})
    return res, json.loads((out / "summary.json").read_text()), out, result


def study(betas, ses, ea="A", oa="G", eaf=0.3, pos=(100, 200)):
    return pd.DataFrame({"chr": "1", "pos": list(pos), "ea": ea, "oa": oa, "beta": betas, "se": ses,
                         "p": 2 * stats.norm.sf(np.abs(np.array(betas) / np.array(ses))), "eaf": eaf, "n": 200})


def oriented(res, pos, ea="A"):
    """The results row at pos with BETA for effect allele `ea` (methods may report either allele)."""
    r = res.set_index("pos").loc[pos].copy()
    if r["ea"] != ea:
        r["beta"], r["ea"], r["oa"] = -r["beta"], r["oa"], r["ea"]
        if "eaf" in r and pd.notna(r["eaf"]):
            r["eaf"] = 1 - r["eaf"]
    return r


def fixed_effects(betas, ses):
    w = 1 / np.array(ses) ** 2
    b = (w * betas).sum() / w.sum()
    q = (w * (np.array(betas) - b) ** 2).sum()
    return b, np.sqrt(1 / w.sum()), q


@needs_image("gwashub/gwama")
def test_gwama_fixed_effects_match_hand_calculation(tmp_path):
    res, summ, out, result = run_meta(tmp_path, [study([0.2, 0.0], [0.1, 0.1]), study([0.1, 0.0], [0.05, 0.1])])
    r = oriented(res, 100)
    b, se, q = fixed_effects([0.2, 0.1], [0.1, 0.05])
    assert np.isclose(r["beta"], b, rtol=1e-3) and np.isclose(r["se"], se, rtol=1e-3) and np.isclose(r["q"], q, rtol=1e-3)
    assert np.isclose(r["i2"], max(0, (q - 1) / q), atol=1e-3)
    assert r["direction"] == "++" and r["n_studies"] == 2 and summ["n_datasets"] == 2
    for f in ("manhattan.png", "qq.png", "plot_data.json", "lead_loci.tsv", "forest.json"):
        assert (out / f).exists()
    # GWAMA's own files are kept and listed for download.
    files = {f["path"] for f in result["log"]["files"]}
    assert {"gwama_console.txt", "gwama_log.txt"} <= files and all((out / f).exists() for f in files)
    assert not (out / "gwama_work").exists()
    # Its progress, and GWAMA's own output, reach the console as they print (the running meta-analysis's tail).
    console = (out / "method_output.txt").read_text()
    for line in ("Reading dataset 1 of 2: S1", "Running GWAMA on 2 datasets", "Writing ", "Lead loci: LD clumping", "Manhattan and QQ plots"):
        assert line in console, line
    assert (out / "gwama_console.txt").read_text().split("\n\n", 1)[1].strip() in console


def genome(seed, n=600, chroms=("1", "2", "3", "X"), inflate=1.0):
    """A small multi-chromosome dataset: shared positions (so variants match across datasets), alleles written either
    way round, a few real effects; inflate scales the test statistics (for genomic control)."""
    rng = np.random.default_rng(seed)
    c = np.array(chroms)[np.arange(n) % len(chroms)]
    se = rng.uniform(0.02, 0.1, n)
    beta = rng.normal(0, se * np.sqrt(inflate)) + np.where(np.arange(n) % 97 == 0, 0.3, 0)
    flip = rng.random(n) < 0.3
    return pd.DataFrame({"chr": c, "pos": 1000 + np.arange(n) * 10, "ea": np.where(flip, "G", "A"), "oa": np.where(flip, "A", "G"),
                         "beta": np.where(flip, -beta, beta), "se": se, "p": 2 * stats.norm.sf(np.abs(beta / se)),
                         "eaf": np.where(flip, 0.7, 0.3), "n": 500, "rsid": [f"rs{seed}{i}" for i in range(n)]})


@needs_image("gwashub/gwama")
def test_gwama_per_chromosome_matches_one_whole_genome_run(tmp_path, monkeypatch):
    """GWAMA runs one chromosome at a time (memory); the results are those of one whole-genome run: identical files
    for the fixed and random models with a direction threshold, datasets missing a whole chromosome included (the
    direction string keeps every dataset's place), and the same per-dataset counts. Genomic control, applied by the
    wrapper before GWAMA, matches GWAMA's own --genomic_control (to GWAMA's printed precision)."""
    studies = [genome(1), genome(2, chroms=("1", "2", "3")), genome(3, inflate=1.4), genome(4, n=300, inflate=0.8)]
    for params in ({}, {"model": "random", "direction_threshold": 0.5, "min_studies": 1}, {"genomic_control": True}):
        tag = "_".join(f"{k}{v}" for k, v in params.items()) or "default"
        monkeypatch.delenv("GWASHUB_GWAMA_WHOLE_GENOME", raising=False)
        split, _, out_split, r_split = run_meta(tmp_path / f"split_{tag}", studies, params)
        monkeypatch.setenv("GWASHUB_GWAMA_WHOLE_GENOME", "1")
        whole, _, out_whole, r_whole = run_meta(tmp_path / f"whole_{tag}", studies, params)
        assert r_split["log"]["metrics"]["runs"] == 4 and r_whole["log"]["metrics"]["runs"] == 1
        assert len(split) == len(whole) > 0 and "X" in set(split["chr"])
        if params.get("genomic_control"):
            assert "--genomic_control" in (out_whole / "gwama_console.txt").read_text()
            assert "--genomic_control" not in (out_split / "gwama_console.txt").read_text()
            gc = pd.read_csv(out_split / "genomic_control.tsv", sep="\t")
            gwama = pd.read_csv(out_whole / "gwama_gc.txt", sep="\t", index_col=False)  # GWAMA's own lambdas
            assert np.allclose(gc["lambda"], gwama["directly_genotyped_markers_lambda"], atol=1e-4)
            assert gc["se_corrected"].tolist() == [True, False, True, False]  # only lambdas above 1 correct the SEs
            for col in ("beta", "se", "z"):
                assert np.allclose(split[col], whole[col], atol=2e-6), col
            assert np.allclose(split["p"], whole["p"], rtol=1e-4) and (split["direction"] == whole["direction"]).all()
        else:
            assert (out_split / "meta_results.tsv.gz").read_bytes() == (out_whole / "meta_results.tsv.gz").read_bytes()
        counts = lambda o: pd.read_csv(o / "datasets.tsv", sep="\t")["in_results"].tolist()  # noqa: E731
        assert counts(out_split) == counts(out_whole)


@needs_image("gwashub/gwama")
def test_gwama_options_reach_gwama(tmp_path):
    a, b = study([0.5, 0.1], [0.05, 0.05]), study([-0.3, 0.1], [0.05, 0.05])
    fe, _, _, _ = run_meta(tmp_path, [a, b])
    re, _, out, _ = run_meta(tmp_path / "re", [a, b], {"model": "random", "genomic_control_output": True, "direction_threshold": 0.05})
    console = (out / "gwama_console.txt").read_text()
    assert "--random" in console and "--genomic_control_output" in console and "--threshold 0.05" in console
    assert oriented(re, 100)["se"] > 3 * oriented(fe, 100)["se"]  # heterogeneity widens the random-effects SE


@needs_image("gwashub/gwama")
def test_flipped_alleles_are_reoriented(tmp_path):
    s1 = study([0.2, 0.1], [0.1, 0.1], ea="A", oa="G", eaf=0.3)
    s2 = study([-0.2, -0.1], [0.1, 0.1], ea="G", oa="A", eaf=0.7)  # same effect, other allele
    res, _, _, _ = run_meta(tmp_path, [s1, s2])
    r = oriented(res, 100)
    assert np.isclose(r["beta"], 0.2, atol=1e-4) and np.isclose(r["eaf"], 0.3, atol=1e-3) and r["q"] < 1e-6


@needs_image("gwashub/gwama")
def test_min_studies_and_direction_placeholders(tmp_path):
    s1, s2, s3 = study([0.1, 0.1], [0.1, 0.1]), study([0.1, 0.1], [0.1, 0.1]), study([0.1], [0.1], pos=(100,))
    res, _, _, _ = run_meta(tmp_path, [s1, s2, s3], {"min_studies": 3})
    assert res["pos"].tolist() == [100]
    res, _, _, _ = run_meta(tmp_path / "b", [s3, s1, s2], {"min_studies": 2})
    assert res.set_index("pos").loc[200, "direction"] in ("?++", "?--")


@needs_image("gwashub/gwama")
def test_direction_threshold_keeps_datasets_present(tmp_path):
    """Above --threshold GWAMA writes "?" for a dataset, as for a missing one; the dataset still counts as
    contributing in datasets.tsv. An effect of exactly 0 stays "0" (present), not "?"."""
    strong, weak = study([0.5, 0.5], [0.05, 0.05]), study([0.01, 0.01], [0.1, 0.1])
    res, _, out, _ = run_meta(tmp_path, [strong, weak], {"direction_threshold": 0.05})
    assert set(res["direction"]) <= {"+?", "-?"}
    ds = pd.read_csv(out / "datasets.tsv", sep="\t")
    assert ds["in_results"].tolist() == [2, 2]
    zero = study([0.0, 0.2], [0.1, 0.1])
    res, _, _, _ = run_meta(tmp_path / "zero", [zero, strong])
    assert res.set_index("pos").loc[100, "direction"] in ("0+", "0-")


@needs_image("gwashub/metal")
def test_metal_matches_hand_calculation_and_keeps_its_files(tmp_path):
    a, b = study([0.2, 0.05], [0.1, 0.1]), study([-0.1, 0.03], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    res, _, out, result = run_meta(tmp_path, [a, b], {"minmax_freq": True}, method="gwashub/metal")
    beta, se, _ = fixed_effects([0.2, 0.1], [0.1, 0.05])
    r = oriented(res, 100)
    assert np.isclose(r["beta"], beta, rtol=1e-6) and np.isclose(r["se"], se, rtol=1e-6)  # 8 significant digits
    assert {"eaf_min", "eaf_max"} <= set(res.columns)
    files = {f["path"] for f in result["log"]["files"]}
    assert {"metal_script.txt", "metal_console.txt", "metal_info.txt"} <= files and all((out / f).exists() for f in files)
    assert "MINMAXFREQ ON" in (out / "metal_script.txt").read_text()
    console = (out / "method_output.txt").read_text()
    assert "Reading dataset 2 of 2: S2" in console and "Running METAL on 2 datasets" in console
    assert (out / "metal_console.txt").read_text().strip() in console


@needs_image("gwashub/gwama", "gwashub/metal")
def test_gwama_and_metal_agree(tmp_path):
    a, b = study([0.2, 0.05], [0.1, 0.1]), study([-0.1, 0.03], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    gw, _, _, _ = run_meta(tmp_path / "g", [a, b])
    mt, _, _, _ = run_meta(tmp_path / "m", [a, b], method="gwashub/metal")
    for pos in (100, 200):
        g, m = oriented(gw, pos), oriented(mt, pos)
        assert np.isclose(g["beta"], m["beta"], atol=1e-4) and np.isclose(g["se"], m["se"], atol=1e-4)


@needs_image("gwashub/gwama", *DEFAULT_IMAGES)
def test_recovers_true_loci_on_synthetic_data(synth_small, tmp_path):
    from pipeline.runner.catalog import load_steps
    from pipeline.runner.executor import run_pipeline
    from pipeline.runner.pipeline import load_template, resolve
    from devtools.synth import params_for
    resolved = resolve(load_template("pipeline/qc_steps/templates/default.yaml"), load_steps())
    paths = []
    for e in synth_small["manifest"]:
        d = tmp_path / e["file"].split(".")[0]
        r = run_pipeline(resolved, os.path.join(synth_small["dir"], "fake_gwas", e["file"]), params_for(e), synth_small["resources"], str(d))
        assert r["status"] == "completed"
        paths.append(pd.read_csv(d / "gwas_qc.tsv", sep="\t", dtype={"chr": str}))
    truth = pd.read_csv(os.path.join(synth_small["dir"], "truth_causal.tsv"), sep="\t", dtype={"chr": str})
    truth = truth[truth["phenotype"] == "All-cause HF"]
    # Lead loci by LD clumping against the synthetic 1000 Genomes panels, and by distance without them.
    for resources, method in ((synth_small["resources"], "clumping"), (None, "distance")):
        res, summ, out, _ = run_meta(tmp_path / method, paths, resources=resources)
        assert summ["loci_method"] == method
        loci = pd.read_csv(out / "lead_loci.tsv", sep="\t", dtype={"chr": str})
        found = sum(any(l.chr == t.chr and l.locus_start - 100_000 <= t.pos38 <= l.locus_end + 100_000 for l in loci.itertuples())
                    for t in truth.itertuples())
        assert found >= len(truth) - 1, (method, found, len(truth))
        assert len(loci) <= len(truth) + 2, (method, len(loci))  # one locus per true signal, give or take
        assert {"locus_id", "locus_start", "locus_end", "n_leads", "n_signals", "leads"} <= set(loci.columns)
        # Per dataset: lead loci covered, direction agreement and outliers (all three datasets carry the same signals).
        for st in summ["studies"]:
            assert st["lead_loci_total"] == len(loci) and 0 < st["lead_loci_covered"] <= len(loci)
            assert st["direction_agreement"] >= 0.75 and st["outlier_loci"] >= 0
        # Every independent signal is saved with its locus and lead; one locus lead per locus, n_leads leads per locus.
        sig = pd.read_csv(out / "signals.tsv", sep="\t", dtype={"chr": str})
        per = sig.groupby("locus_id")
        assert sorted(per.groups) == list(loci["locus_id"]) and (per["is_locus_lead"].sum() == 1).all()
        assert (per["is_lead"].sum() == loci.set_index("locus_id")["n_leads"]).all()
        assert (per.size() == loci.set_index("locus_id")["n_signals"]).all()
        top = sig[sig["is_locus_lead"]].set_index("locus_id")
        assert (top["pos"] == loci.set_index("locus_id")["pos"]).all()
        # A locus plot per locus: r² with the lead from the panel (none without it) and the synthetic genes in view.
        region = json.loads((out / "region_1.json").read_text())
        assert region["locus_id"] == 1 and region["start"] <= region["locus_start"] <= region["lead"]["pos"] <= region["locus_end"] <= region["end"]
        r2 = [v for v in region["points"]["r2"] if v is not None]
        if method == "clumping":
            assert r2 and max(r2) == 1.0 and any(0 < v < 1 for v in r2)  # the lead itself, and LD with its neighbours
            assert summ["n_region_files"] == len(loci) and summ["gene_track"] and region["genes"]
            assert (loci["nearest_gene"].fillna("") != "").all()
        else:  # no resources at all: no r², no genes
            assert not r2 and not region["genes"] and not summ["gene_track"]


def test_methods_are_listed_with_typed_params():
    from pipeline.runner.catalog import load_analyses
    m = load_analyses()
    assert set(m) == {"gwashub/gwama", "gwashub/metal", "custom_example/custom_meta_analysis"}
    model = next(f for f in m["gwashub/gwama"].param_schema if f["key"] == "model")
    assert model["type"] == "select" and model["options"] == ["fixed", "random"] and model["default"] == "fixed"
    gwama = {f["key"] for f in m["gwashub/gwama"].param_schema}
    assert {"maf_min", "genomic_control", "genomic_control_output", "direction_threshold", "sex_differentiated"} <= gwama
    assert m["gwashub/gwama"].image == "gwashub/gwama" and m["gwashub/metal"].image == "gwashub/metal"


@needs_image("gwashub/base")
def test_weighted_z_example_method(tmp_path):
    """The example consortium method: Z = Σ√N·z / √ΣN, oriented to one effect allele; no BETA/SE."""
    s1 = study([0.2, 0.0], [0.1, 0.1])  # n = 200
    s2 = study([-0.1, 0.0], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    s2["n"] = 800
    res, summ, out, _ = run_meta(tmp_path, [s1, s2], method="custom_example/custom_meta_analysis")
    r = res.set_index("pos").loc[100]
    z = (np.sqrt(200) * 2.0 + np.sqrt(800) * 2.0) / np.sqrt(200 + 800)
    assert r["ea"] == "A" and np.isclose(r["z"], z, rtol=1e-4) and r["direction"] == "++" and "beta" not in res.columns
    assert summ["method"] == "custom_example/custom_meta_analysis" and (out / "manhattan.png").exists()
    # The method outputs no rsids; the lead loci get them from the inputs.
    s1["rsid"], s2["rsid"] = ["rs100", "rs200"], ["rs100", "rs200"]
    _, _, out, _ = run_meta(tmp_path / "rs", [s1, s2], method="custom_example/custom_meta_analysis", common={"gws": 0.05})
    lead = pd.read_csv(out / "lead_loci.tsv", sep="\t")
    assert list(lead["rsid"]) == ["rs100"]


def test_lead_locus_agreement_flags_swapped_alleles():
    from _metalib.publish import lead_locus_agreement
    forest = [{"beta": 0.2, "studies": [{"id": 1, "beta": 0.21, "se": 0.02}, {"id": 2, "beta": -0.2, "se": 0.02}]},
              {"beta": 0.1, "studies": [{"id": 1, "beta": 0.1, "se": 0.02}]}]
    studies = [{"id": 1}, {"id": 2}]
    lead_locus_agreement(forest, studies)
    assert studies[0] == {"id": 1, "lead_loci_covered": 2, "lead_loci_total": 2, "direction_agreement": 1.0, "outlier_loci": 0}
    assert studies[1]["lead_loci_covered"] == 1 and studies[1]["direction_agreement"] == 0 and studies[1]["outlier_loci"] == 1


@needs_image("gwashub/gwama")
def test_datasets_table_and_readme(tmp_path):
    """datasets.tsv lists the datasets in direction-string order with what each filter removed; README.txt explains the columns."""
    a = study([0.2, 0.05, 0.1], [0.1, 0.1, 0.1], pos=(100, 200, 300))
    a.loc[2, "se"] = None  # missing SE: removed before the method runs
    b = study([0.1, 0.02], [0.05, 0.1], eaf=0.004)  # both below MAF 1%
    c = study([0.15, 0.03], [0.08, 0.1])
    res, summ, out, _ = run_meta(tmp_path, [a, b, c], params={"maf_min": 0.01})
    ds = pd.read_csv(out / "datasets.tsv", sep="\t")
    assert list(ds["position"]) == [1, 2, 3] and list(ds["dataset_id"]) == [1, 2, 3]
    assert list(ds["variants_in"]) == [3, 2, 2] and list(ds["removed_missing_beta_se"]) == [1, 0, 0]
    assert list(ds["removed_maf"]) == [0, 2, 0] and list(ds["variants_used"]) == [2, 0, 2]
    assert list(ds["in_results"]) == [2, 0, 2]
    readme = (out / "README.txt").read_text()
    assert "Effect allele" in readme and "order of datasets.tsv" in readme and "  2: S2" in readme
    assert "\nbeta " in readme and "\nz " in readme and "\neaf_min " not in readme  # only the columns the method wrote


def test_merge_intervals_and_point_loci():
    from _metalib.loci import _tables, merge_intervals
    # 0-100 and 300-400 are 200 apart (merged at 250 kb), 900 is not.
    assert merge_intervals(np.array([300, 0, 900]), np.array([400, 100, 900]), 250).tolist() == [0, 0, 1]
    res = pd.DataFrame({"chr": ["1"], "pos": [1_000_000], "p": [1e-9]})
    t, sig = _tables(res, [{"lead_index": 0, "locus_start": 1_000_000, "locus_end": 1_000_000, "n_leads": 1, "n_signals": 1,
                            "leads": "1:1000000", "ld_panel": ""}], {"min_locus_kb": 250})
    assert list(sig["locus_id"]) == [1] and bool(sig.loc[0, "is_lead"]) and bool(sig.loc[0, "is_locus_lead"])
    assert t.loc[0, "expanded"] and (t.loc[0, "locus_start"], t.loc[0, "locus_end"]) == (750_000, 1_250_000)


def test_results_keep_the_methods_precision(tmp_path):
    """The written results lose none of the digits GWAMA (6 decimals) or METAL print, whatever the magnitude, and
    whole numbers stay whole (no 1e+06)."""
    from _metalib.common import write_results
    res = pd.DataFrame({"chr": ["1"], "pos": [100], "ea": ["A"], "oa": ["G"], "beta": [-0.201477], "se": [0.007274],
                        "z": [-27.696703], "p": [7.651513e-169], "n_studies": [3], "n": [1234567.0], "q": [27.123456],
                        "het_p": [0.000001], "i2": [0.926264]})
    write_results(res, str(tmp_path))
    back = pd.read_csv(tmp_path / "meta_results.tsv.gz", sep="\t", dtype=str).iloc[0]
    assert (back["z"], back["q"], back["p"], back["n"], back["i2"]) == ("-27.696703", "27.123456", "7.651513e-169", "1234567", "0.926264")


def test_results_are_byte_identical_on_rerun(tmp_path):
    """Writing the same results twice gives the same file (the gzip header carries no timestamp)."""
    import time
    from _metalib.common import write_results
    res = pd.DataFrame({"chr": ["1"], "pos": [100], "ea": ["A"], "oa": ["G"], "beta": [0.1], "se": [0.01], "p": [1e-20],
                        "n_studies": [2]})
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        write_results(res, str(tmp_path / d))
        time.sleep(1.1)
    assert (tmp_path / "a" / "meta_results.tsv.gz").read_bytes() == (tmp_path / "b" / "meta_results.tsv.gz").read_bytes()


def test_negative_zero_beta_is_zero(tmp_path):
    """-0.0 (a zero BETA negated by an allele switch) is loaded as 0, so no method reads it as a negative effect."""
    from _metalib.common import load_study
    p = tmp_path / "s.tsv"
    study([0.1, 0.2], [0.05, 0.05]).assign(beta=[-0.0, 0.2]).to_csv(p, sep="\t", index=False)
    df, _ = load_study({"id": 1, "label": "S", "path": str(p)})
    assert not np.signbit(df["beta"]).any()


def test_dataset_overlap_is_named():
    """Z-scores that correlate across null variants: the same data twice, or shared samples; independent ones pass."""
    from _metalib.publish import dataset_overlap
    import tempfile
    rng = np.random.default_rng(3)
    n = 5000
    shared, own = rng.normal(size=n), rng.normal(size=(3, n))
    z = {"A": shared, "B": shared.copy(), "C": 0.6 * shared + 0.8 * own[0], "D": own[1]}  # B = A; C overlaps A
    specs = []
    with tempfile.TemporaryDirectory() as d:
        for name, zz in z.items():
            p = os.path.join(d, f"{name}.tsv")
            pd.DataFrame({"chr": "1", "pos": np.arange(n) + 1, "ea": "A", "oa": "G", "beta": zz * 0.01, "se": 0.01}).to_csv(p, sep="\t", index=False)
            specs.append({"label": name, "path": p})
        pairs, messages = dataset_overlap(specs)
    r = {(p["a"], p["b"]): p["z_correlation"] for p in pairs}
    assert r[("A", "B")] > 0.98 and 0.3 < r[("A", "C")] < 0.7 and abs(r[("A", "D")]) < 0.1
    text = " ".join(messages)
    assert "A and B look like the same data" in text and "A and C may share samples" in text and "A and D" not in text


def test_a_failing_method_keeps_its_output_and_says_why(tmp_path, monkeypatch):
    """A method that fails leaves its whole console in method_output.txt and an error that says how it ended."""
    from pipeline.runner.catalog import AnalysisDef
    from pipeline.runner.jobspec import METHOD_OUTPUT, run_method
    monkeypatch.setenv("GWASHUB_STEP_RUNNER", "host")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "boom.py").write_text(
        "import os, sys\nout = sys.argv[sys.argv.index('--out') + 1]\nos.makedirs(os.path.join(out, 'boom_work'))\n"
        "open(os.path.join(out, 'boom_work', 'study_1.txt'), 'w').write('a copy of an input')\n"
        "print('reading inputs')\nsys.stderr.write('MemoryError: out of room\\n')\nsys.exit(3)\n")
    method = AnalysisDef(key="testco/boom", title="Boom", description="", workdir=str(tmp_path / "m"),
                         command=["python", "boom.py"], image="gwashub/base")
    out = tmp_path / "out"
    res = run_method(method, [], {}, {}, str(out))
    assert res["status"] == "failed" and res["error"].startswith("The method exited with 3.")
    assert "MemoryError: out of room" in res["error"] and METHOD_OUTPUT in res["error"]
    console = (out / METHOD_OUTPUT).read_text()
    assert "exit code: 3" in console and "reading inputs" in console and "MemoryError" in console
    assert not (out / "boom_work").exists()  # its scratch folder (a copy of the inputs) is removed, failed or not
    from pipeline.runner.jobspec import _scratch
    assert _scratch("gwama_work/study_1.txt") and _scratch("_loci_work/x") and not _scratch("lead_loci.tsv")


def test_a_running_method_shows_its_output_tail(tmp_path, monkeypatch):
    """A meta-analysis's console reaches method_output.txt as it prints, stdout and stderr in order; its last lines
    are published to progress.json with the part that is running, and only when they change."""
    from pipeline.runner.catalog import AnalysisDef
    from pipeline.runner.jobspec import METHOD_OUTPUT, ConsoleTail, run_method
    monkeypatch.setenv("GWASHUB_STEP_RUNNER", "host")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "slow.py").write_text(
        "import sys\nprint('study 1 of 2')\nsys.stderr.write('warning: few variants\\n')\nprint('study 2 of 2')\nsys.exit(1)\n")
    method = AnalysisDef(key="testco/slow", title="Slow", description="", workdir=str(tmp_path / "m"),
                         command=["python", "slow.py"], image="gwashub/base")
    out = tmp_path / "out"
    res = run_method(method, [], {}, {}, str(out))
    console = (out / METHOD_OUTPUT).read_text()
    assert console.index("study 1 of 2") < console.index("warning: few variants") < console.index("study 2 of 2")
    assert "exit code: 1" in console and "study 2 of 2" in res["error"]
    progress = tmp_path / "progress.json"
    tail = ConsoleTail(str(out / METHOD_OUTPUT), str(progress))
    tail.publish_once()
    live = json.loads(progress.read_text())
    assert live["stage"] == "method" and live["tail"]["lines"][-1] == "exit code: 1" and "study 1 of 2" in live["tail"]["lines"]
    progress.unlink()
    tail.publish_once()  # nothing new: not written again
    assert not progress.exists()
    tail.stage = "post-processing"
    tail.publish_once()
    assert json.loads(progress.read_text())["stage"] == "post-processing"


def test_a_failed_program_says_how_it_ended(capsys):
    """When GWAMA or METAL fails, the wrapper says how that program ended, not the wrapper: a kill is named as running
    out of memory (and the wrapper exits 137, which the runner explains as well); GWAMA's line counters are left out."""
    import pytest
    from _metalib.common import tool_failed
    with pytest.raises(SystemExit) as e:
        tool_failed("GWAMA", -9, "Reading file: study_29.txt\nLine: 25300000\nLine: 25310000\nLine: ", " on chromosome 2",
                    "Its error file ends:\nW000001 Warning")
    msg = capsys.readouterr().err
    assert e.value.code == 137 and "GWAMA was killed (signal 9), most likely because it ran out of memory on chromosome 2" in msg
    assert "Reading file: study_29.txt" in msg and "Line:" not in msg and "W000001" in msg
    with pytest.raises(SystemExit) as e:
        tool_failed("METAL", 2, "bad header")
    assert e.value.code == 2 and "METAL exited with 2." in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        tool_failed("GWAMA", 0, "")
    assert e.value.code == 1 and "finished without writing its results" in capsys.readouterr().err
