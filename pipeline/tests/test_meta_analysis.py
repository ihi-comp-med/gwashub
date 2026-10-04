import json
import os

import numpy as np
import pytest
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root


def run_meta(tmp_path, studies, params=None, method="gwashub/ivw", common=None):
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
    result = run_method(load_analyses()[method], specs, {"maf_min": 0, **(params or {})} if method != "custom_example/custom_meta_analysis" else (params or {}),
                        common or {}, str(out))
    assert result["status"] == "completed", result
    res = pd.read_csv(out / "meta_results.tsv.gz", sep="\t", dtype={"chr": str})
    return res, json.loads((out / "summary.json").read_text()), out


def study(betas, ses, ea="A", oa="G", eaf=0.3, pos=(100, 200)):
    return pd.DataFrame({"chr": "1", "pos": list(pos), "ea": ea, "oa": oa, "beta": betas, "se": ses,
                         "p": 2 * stats.norm.sf(np.abs(np.array(betas) / np.array(ses))), "eaf": eaf, "n": 200})


def test_fixed_effects_matches_hand_calculation(tmp_path):
    res, summ, out = run_meta(tmp_path, [study([0.2, 0.0], [0.1, 0.1]), study([0.1, 0.0], [0.05, 0.1])])
    r = res.set_index("pos").loc[100]
    w = np.array([1 / 0.1**2, 1 / 0.05**2])
    b = (w * [0.2, 0.1]).sum() / w.sum()
    assert np.isclose(r["beta"], b, rtol=1e-4) and np.isclose(r["se"], np.sqrt(1 / w.sum()), rtol=1e-4)
    q = (w * (np.array([0.2, 0.1]) - b) ** 2).sum()
    assert np.isclose(r["q"], q, rtol=1e-4) and np.isclose(r["i2"], max(0, (q - 1) / q), atol=1e-4)
    assert r["direction"] == "++" and r["n_studies"] == 2
    assert summ["n_datasets"] == 2
    for f in ("manhattan.png", "qq.png", "plot_data.json", "lead_loci.tsv", "forest.json"):
        assert (out / f).exists()


def test_flipped_alleles_are_reoriented(tmp_path):
    s1 = study([0.2, 0.1], [0.1, 0.1], ea="A", oa="G", eaf=0.3)
    s2 = study([-0.2, -0.1], [0.1, 0.1], ea="G", oa="A", eaf=0.7)  # same effect, other allele
    res, _, _ = run_meta(tmp_path, [s1, s2])
    r = res.set_index("pos").loc[100]
    assert r["ea"] == "A" and np.isclose(r["beta"], 0.2) and np.isclose(r["eaf"], 0.3) and r["q"] < 1e-9


def test_min_studies_and_direction_placeholders(tmp_path):
    s1, s2, s3 = study([0.1, 0.1], [0.1, 0.1]), study([0.1, 0.1], [0.1, 0.1]), study([0.1], [0.1], pos=(100,))
    res, _, _ = run_meta(tmp_path, [s1, s2, s3], {"min_studies": 3})
    assert res["pos"].tolist() == [100]
    res, _, _ = run_meta(tmp_path / "b", [s3, s1, s2], {"min_studies": 2})
    assert res.set_index("pos").loc[200, "direction"] == "?++"


def test_random_effects_widens_se_under_heterogeneity(tmp_path):
    a, b = study([0.5, 0.1], [0.05, 0.05]), study([-0.3, 0.1], [0.05, 0.05])
    fe, _, _ = run_meta(tmp_path, [a, b], {"model": "fixed"})
    re, _, _ = run_meta(tmp_path / "re", [a, b], {"model": "random"})
    assert re.set_index("pos").loc[100, "se"] > 3 * fe.set_index("pos").loc[100, "se"]
    assert np.isclose(re.set_index("pos").loc[200, "se"], fe.set_index("pos").loc[200, "se"])  # no heterogeneity


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
    res, summ, out = run_meta(tmp_path, paths)
    truth = pd.read_csv(os.path.join(synth_small["dir"], "truth_causal.tsv"), sep="\t", dtype={"chr": str})
    truth = truth[truth["phenotype"] == "All-cause HF"]
    leads = pd.read_csv(out / "lead_loci.tsv", sep="\t", dtype={"chr": str})
    found = sum(any((l.chr == t.chr) and abs(l.pos - t.pos38) < 500_000 for l in leads.itertuples()) for t in truth.itertuples())
    assert found >= len(truth) - 1, (found, len(truth))


def test_methods_are_listed_with_typed_params():
    from pipeline.runner.catalog import load_analyses
    m = load_analyses()
    assert {"gwashub/ivw", "gwashub/gwama", "gwashub/metal", "custom_example/custom_meta_analysis"} <= set(m)
    model = next(f for f in m["gwashub/ivw"].param_schema if f["key"] == "model")
    assert model["type"] == "select" and model["options"] == ["fixed", "random"] and model["default"] == "fixed"
    assert m["gwashub/gwama"].image == "gwashub/gwama" and m["gwashub/metal"].image == "gwashub/metal"


def test_weighted_z_example_method(tmp_path):
    """The example consortium method: Z = Σ√N·z / √ΣN, oriented to one effect allele; no BETA/SE."""
    s1 = study([0.2, 0.0], [0.1, 0.1])  # n = 200
    s2 = study([-0.1, 0.0], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    s2["n"] = 800
    res, summ, out = run_meta(tmp_path, [s1, s2], method="custom_example/custom_meta_analysis")
    r = res.set_index("pos").loc[100]
    z = (np.sqrt(200) * 2.0 + np.sqrt(800) * 2.0) / np.sqrt(200 + 800)
    assert r["ea"] == "A" and np.isclose(r["z"], z, rtol=1e-4) and r["direction"] == "++" and "beta" not in res.columns
    assert summ["method"] == "custom_example/custom_meta_analysis" and (out / "manhattan.png").exists()
    # The method outputs no rsids; the lead loci get them from the inputs.
    s1["rsid"], s2["rsid"] = ["rs100", "rs200"], ["rs100", "rs200"]
    _, _, out = run_meta(tmp_path / "rs", [s1, s2], method="custom_example/custom_meta_analysis", common={"gws": 0.05})
    lead = pd.read_csv(out / "lead_loci.tsv", sep="\t")
    assert list(lead["rsid"]) == ["rs100"]


@pytest.mark.skipif(not __import__("shutil").which("GWAMA"), reason="GWAMA is installed in the gwashub/gwama image")
def test_gwama_matches_ivw(tmp_path):
    a, b = study([0.2, 0.05], [0.1, 0.1]), study([-0.1, 0.03], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    ivw, _, _ = run_meta(tmp_path / "i", [a, b])
    gw, _, _ = run_meta(tmp_path / "g", [a, b], method="gwashub/gwama")
    m = ivw.merge(gw, on=["chr", "pos"], suffixes=("", "_g"))
    sign = np.where(m["ea"] == m["ea_g"], 1, -1)
    assert np.allclose(m["beta"], sign * m["beta_g"], atol=1e-5) and np.allclose(m["se"], m["se_g"], atol=1e-5)


@pytest.mark.skipif(not __import__("shutil").which("metal"), reason="METAL is installed in the gwashub/metal image")
def test_metal_matches_ivw(tmp_path):
    a, b = study([0.2, 0.05], [0.1, 0.1]), study([-0.1, 0.03], [0.05, 0.1], ea="G", oa="A", eaf=0.7)
    ivw, _, _ = run_meta(tmp_path / "i", [a, b])
    mt, _, _ = run_meta(tmp_path / "m", [a, b], method="gwashub/metal")
    m = ivw.merge(mt, on=["chr", "pos"], suffixes=("", "_m"))
    sign = np.where(m["ea"] == m["ea_m"], 1, -1)
    assert np.allclose(m["beta"], sign * m["beta_m"], atol=1e-3) and np.allclose(m["se"], m["se_m"], atol=1e-3)


def test_datasets_table_and_readme(tmp_path):
    """datasets.tsv lists the datasets in direction-string order with what each filter removed; README.txt explains the columns."""
    a = study([0.2, 0.05, 0.1], [0.1, 0.1, 0.1], pos=(100, 200, 300))
    a.loc[2, "se"] = None  # missing SE: removed before the method runs
    b = study([0.1, 0.02], [0.05, 0.1], eaf=0.004)  # both below MAF 1%
    c = study([0.15, 0.03], [0.08, 0.1])
    res, summ, out = run_meta(tmp_path, [a, b, c], params={"maf_min": 0.01})
    ds = pd.read_csv(out / "datasets.tsv", sep="\t")
    assert list(ds["position"]) == [1, 2, 3] and list(ds["dataset_id"]) == [1, 2, 3]
    assert list(ds["variants_in"]) == [3, 2, 2] and list(ds["removed_missing_beta_se"]) == [1, 0, 0]
    assert list(ds["removed_maf"]) == [0, 2, 0] and list(ds["variants_used"]) == [2, 0, 2]
    assert list(ds["in_results"]) == [2, 0, 2]
    readme = (out / "README.txt").read_text()
    assert "Effect allele" in readme and "order of datasets.tsv" in readme and "  2: S2" in readme
    assert "\nbeta " in readme and "\nz " not in readme  # only the columns the method wrote
