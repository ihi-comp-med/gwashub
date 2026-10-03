import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
SCRIPT = os.path.join(ROOT, "pipeline", "analyses", "meta_analysis", "meta_analysis.py")


def run_meta(tmp_path, studies, params=None):
    specs = []
    for i, df in enumerate(studies):
        p = tmp_path / f"s{i}.tsv"
        df.to_csv(p, sep="\t", index=False)
        specs.append({"id": i + 1, "label": f"S{i + 1}", "path": str(p), "n_cases": 100, "n_controls": 100})
    (tmp_path / "in.json").write_text(json.dumps(specs))
    (tmp_path / "p.json").write_text(json.dumps({"maf_threshold": 0, **(params or {})}))
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, SCRIPT, "--inputs", str(tmp_path / "in.json"), "--params", str(tmp_path / "p.json"),
                           "--out", str(out), "--log", str(tmp_path / "log.json")],
                          env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "pipeline", "qc_steps"), "MPLCONFIGDIR": "/tmp/gwashub-mpl"},
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
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
    res, _, _ = run_meta(tmp_path, [s3, s1, s2], {"min_studies": 2})
    assert res.set_index("pos").loc[200, "direction"] == "?++"


def test_random_effects_widens_se_under_heterogeneity(tmp_path):
    a, b = study([0.5, 0.1], [0.05, 0.05]), study([-0.3, 0.1], [0.05, 0.05])
    fe, _, _ = run_meta(tmp_path, [a, b], {"method": "fixed"})
    re, _, _ = run_meta(tmp_path, [a, b], {"method": "random"})
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
