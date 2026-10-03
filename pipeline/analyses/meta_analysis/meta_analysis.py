"""Inverse-variance meta-analysis of QC'd GWAS summary statistics.

    python meta_analysis.py --inputs inputs.json --params params.json --out OUT_DIR --log log.json

inputs.json: [{"id": 1, "label": "UK Biobank · EUR", "path": ".../gwas_qc.tsv", "n_cases": .., "n_controls": .., "ancestry": "EUR"}]
params.json: settings (see DEFAULTS).

Outputs in OUT_DIR:
    meta_results.tsv.gz  chr pos rsid ea oa beta se p n_studies n eaf q het_p i2 direction
    lead_loci.tsv        clumped lead variants
    forest.json          per-study effects at each lead locus (for forest plots)
    manhattan.png qq.png plot_data.json
    summary.json         counts, lambda, per-study info
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
from scipy import stats

from _lib.common import complement
from _lib.plotting import lambda_gc, make_plots

DEFAULTS = {
    "method": "fixed",            # fixed | random (DerSimonian-Laird)
    "min_studies": 2,             # variants must be present in at least this many studies
    "maf_threshold": 0.01,        # per-study MAF filter (needs EAF)
    "drop_palindromic": False,    # QC already resolved palindromic SNPs; set True to be strict
    "genomic_control": False,     # inflate each study's SE by sqrt(lambda) when lambda > 1
    "gws": 5e-8,
    "clump_kb": 500,
    "het_p_threshold": 1e-3,      # flag loci with significant heterogeneity
}


def load_study(spec, opts):
    df = pd.read_csv(spec["path"], sep="\t", dtype={"chr": str})
    df = df.dropna(subset=["beta", "se"])
    df = df[df["se"] > 0]
    lam = lambda_gc(df["p"]) if "p" in df else float("nan")
    info = {"id": spec["id"], "label": spec["label"], "variants_in": len(df), "lambda_gc": round(lam, 4) if np.isfinite(lam) else None}
    if opts["genomic_control"] and np.isfinite(lam) and lam > 1:
        df["se"] = df["se"] * np.sqrt(lam)
        info["gc_applied"] = round(lam, 4)
    if opts["maf_threshold"] and "eaf" in df:
        maf = np.minimum(df["eaf"], 1 - df["eaf"])
        df = df[maf.isna() | (maf >= opts["maf_threshold"])]
    if opts["drop_palindromic"]:
        snp = (df["ea"].str.len() == 1) & (df["oa"].str.len() == 1)
        df = df[~(snp & (df["ea"].map(complement) == df["oa"]))]
    if "n" not in df or df["n"].isna().all():
        df["n"] = (spec.get("n_cases") or 0) + (spec.get("n_controls") or 0)
    info["variants_used"] = len(df)
    cols = ["chr", "pos", "ea", "oa", "beta", "se", "n"] + [c for c in ("eaf", "rsid") if c in df]
    out = df[cols].copy()
    out["study"] = spec["id"]
    return out, info


def meta(long, study_order, opts):
    # Canonical allele pair per variant; orient every study to the effect allele of the first study seen.
    long["a1"] = np.where(long["ea"] < long["oa"], long["ea"], long["oa"])
    long["a2"] = np.where(long["ea"] < long["oa"], long["oa"], long["ea"])
    key = ["chr", "pos", "a1", "a2"]
    long = long.sort_values(key + ["study_rank"])
    first_ea = long.groupby(key, sort=False)["ea"].transform("first")
    sign = np.where(long["ea"] == first_ea, 1.0, -1.0)
    long["b"] = long["beta"] * sign
    if "eaf" in long:
        long["f"] = np.where(sign > 0, long["eaf"], 1 - long["eaf"])
    long["ea"] = first_ea
    long["oa"] = np.where(long["ea"] == long["a1"], long["a2"], long["a1"])
    long["w"] = 1.0 / long["se"] ** 2
    long["wb"] = long["w"] * long["b"]

    g = long.groupby(key, sort=False)
    agg = g.agg(ea=("ea", "first"), oa=("oa", "first"), k=("b", "size"), sw=("w", "sum"), swb=("wb", "sum"),
                sw2=("w", lambda w: float((w ** 2).sum())), n=("n", "sum"))
    if "rsid" in long:
        agg["rsid"] = g["rsid"].first()
    agg = agg[agg["k"] >= opts["min_studies"]]
    beta_fe = agg["swb"] / agg["sw"]
    long = long.join(beta_fe.rename("beta_fe"), on=key, how="inner")
    long["qterm"] = long["w"] * (long["b"] - long["beta_fe"]) ** 2
    agg["q"] = long.groupby(key, sort=False)["qterm"].sum()
    df_ = agg["k"] - 1
    agg["het_p"] = np.where(df_ > 0, stats.chi2.sf(agg["q"], df_.clip(lower=1)), np.nan)
    agg["i2"] = np.where(agg["q"] > 0, np.clip((agg["q"] - df_) / agg["q"], 0, 1), 0.0)

    if opts["method"] == "random":
        tau2 = np.clip((agg["q"] - df_) / (agg["sw"] - agg["sw2"] / agg["sw"]), 0, None).fillna(0)
        long = long.join(tau2.rename("tau2"), on=key)
        long["wr"] = 1.0 / (long["se"] ** 2 + long["tau2"])
        long["wrb"] = long["wr"] * long["b"]
        gr = long.groupby(key, sort=False)
        agg["beta"] = gr["wrb"].sum() / gr["wr"].sum()
        agg["se"] = np.sqrt(1.0 / gr["wr"].sum())
        agg["tau2"] = tau2
    else:
        agg["beta"] = beta_fe
        agg["se"] = np.sqrt(1.0 / agg["sw"])

    z = agg["beta"] / agg["se"]
    agg["p"] = 2 * stats.norm.sf(np.abs(z))
    if "f" in long:
        long["fn"] = long["f"] * long["n"]
        agg["eaf"] = long.groupby(key, sort=False)["fn"].sum() / long.groupby(key, sort=False)["n"].sum()

    # METAL-style direction string in study order: + / - / ?
    rank = {s: i for i, s in enumerate(study_order)}
    long["sym"] = np.where(long["b"] > 0, "+", "-")
    long["r"] = long["study"].map(rank)
    syms = long.pivot_table(index=key, columns="r", values="sym", aggfunc="first")
    syms = syms.reindex(columns=range(len(study_order)))
    agg["direction"] = syms.fillna("?").agg("".join, axis=1)

    res = agg.reset_index()
    cols = ["chr", "pos", "rsid", "ea", "oa", "beta", "se", "p", "k", "n", "eaf", "q", "het_p", "i2", "direction"] + (["tau2"] if "tau2" in res else [])
    res = res[[c for c in cols if c in res]].rename(columns={"k": "n_studies"})
    res["cn"] = res["chr"].map(lambda c: 23 if c == "X" else int(c))
    return res.sort_values(["cn", "pos"]).drop(columns="cn"), long


def clump(res, gws, window):
    hits = res[res["p"] < gws].sort_values("p")
    leads = []
    for row in hits.itertuples(index=False):
        if any(l.chr == row.chr and abs(l.pos - row.pos) < window for l in leads):
            continue
        leads.append(row)
    return pd.DataFrame(leads, columns=res.columns) if leads else res.iloc[0:0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args()
    with open(a.inputs) as f:
        specs = json.load(f)
    with open(a.params) as f:
        opts = {**DEFAULTS, **json.load(f)}
    os.makedirs(a.out, exist_ok=True)

    if len(specs) < 2:
        raise SystemExit("meta-analysis needs at least 2 datasets")
    frames, infos = [], []
    for i, s in enumerate(specs):
        df, info = load_study(s, opts)
        df["study_rank"] = i
        frames.append(df)
        infos.append(info)
    long = pd.concat(frames, ignore_index=True)
    order = [s["id"] for s in specs]
    res, long = meta(long, order, opts)

    res.to_csv(os.path.join(a.out, "meta_results.tsv.gz"), sep="\t", index=False, compression="gzip", float_format="%.6g")
    leads = clump(res, opts["gws"], opts["clump_kb"] * 1000)
    leads.to_csv(os.path.join(a.out, "lead_loci.tsv"), sep="\t", index=False, float_format="%.6g")

    # Forest data: per-study oriented effects at each lead variant.
    labels = {s["id"]: s["label"] for s in specs}
    forest = []
    if len(leads):
        lk = leads[["chr", "pos"]].astype({"pos": int})
        sub = long.merge(lk, on=["chr", "pos"])
        for row in leads.itertuples(index=False):
            st = sub[(sub["chr"] == row.chr) & (sub["pos"] == row.pos)]
            forest.append({
                "chr": row.chr, "pos": int(row.pos), "rsid": getattr(row, "rsid", None), "ea": row.ea, "oa": row.oa,
                "beta": row.beta, "se": row.se, "p": row.p, "i2": row.i2, "het_p": None if pd.isna(row.het_p) else row.het_p,
                "studies": [{"id": int(r.study), "label": labels[r.study], "beta": r.b, "se": r.se} for r in st.itertuples()],
            })
    with open(os.path.join(a.out, "forest.json"), "w") as f:
        json.dump(forest, f)

    lam, _ = make_plots(res, a.out, gws=opts["gws"], title=f"Meta-analysis ({len(specs)} datasets, {opts['method']} effects)")
    n_het = int(((leads["het_p"] < opts["het_p_threshold"]).sum()) if len(leads) else 0)
    summary = {
        "n_datasets": len(specs), "method": opts["method"], "n_variants": len(res),
        "n_genome_wide_sig": int((res["p"] < opts["gws"]).sum()), "n_loci": len(leads), "n_loci_heterogeneous": n_het,
        "lambda_gc": None if not np.isfinite(lam) else round(lam, 4), "studies": infos, "settings": opts,
    }
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=float)
    with open(a.log, "w") as f:
        json.dump({"step": "meta_analysis", "verdict": "warn" if n_het else "pass", "metrics": {
            k: summary[k] for k in ("n_datasets", "n_variants", "n_genome_wide_sig", "n_loci", "n_loci_heterogeneous", "lambda_gc")},
            "messages": [f"Meta-analysed {len(res):,} variants across {len(specs)} datasets; {len(leads)} genome-wide significant loci."
                         + (f" {n_het} show significant heterogeneity." if n_het else "")]}, f, indent=2)


if __name__ == "__main__":
    main()
