"""GWASHub's post-processing after any meta-analysis method: lead loci (LD clumping, _metalib/loci.py), forest-plot
data, Manhattan and QQ plots, and summary.json, all from the method's meta_results.tsv.gz and the inputs.

    python publish.py --inputs inputs.json --params common.json --out OUT_DIR --log method_log.json --method KEY \
                      [--resources DIR]   # the 1000 Genomes LD panels (1kg_b38_<POP>.*), when uploaded
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from _lib.plotting import lambda_gc, make_plots
from _metalib import loci, regions
from _metalib.common import FLOAT_FORMAT

COMMON = {**loci.DEFAULTS, "het_p_threshold": 1e-3}


def forest_data(leads, specs):
    """Each dataset's effect at each lead variant, oriented to the meta-analysis effect allele."""
    if not len(leads):
        return []
    want = {(str(r.chr), int(r.pos)) for r in leads.itertuples()}
    per = {}
    for s in specs:
        df = pd.read_csv(s["path"], sep="\t", dtype={"chr": str}, usecols=lambda c: c in ("chr", "pos", "ea", "oa", "beta", "se", "rsid"))
        df = df[[(c, p) in want for c, p in zip(df["chr"].astype(str), df["pos"].astype(int))]]
        per[s["id"]] = df
    out = []
    for r in leads.itertuples(index=False):
        studies, rsid = [], getattr(r, "rsid", None)
        for s in specs:
            d = per[s["id"]]
            d = d[(d["chr"].astype(str) == str(r.chr)) & (d["pos"].astype(int) == int(r.pos))]
            d = d[[{a, b} == {r.ea, r.oa} for a, b in zip(d["ea"], d["oa"])]]
            if len(d):
                row = d.iloc[0]
                sign = 1.0 if row["ea"] == r.ea else -1.0
                studies.append({"id": int(s["id"]), "label": s["label"], "beta": float(row["beta"]) * sign, "se": float(row["se"])})
                if (rsid is None or pd.isna(rsid) or rsid == "") and "rsid" in row and pd.notna(row["rsid"]):
                    rsid = row["rsid"]  # methods needn't output rsids; take one from the inputs
        het = getattr(r, "het_p", None)
        out.append({"locus_id": int(getattr(r, "locus_id", len(out) + 1)), "chr": str(r.chr), "pos": int(r.pos), "rsid": None if rsid is None or pd.isna(rsid) else str(rsid), "ea": r.ea, "oa": r.oa,
                    "beta": None if pd.isna(getattr(r, "beta", np.nan)) else float(r.beta),
                    "se": None if pd.isna(getattr(r, "se", np.nan)) else float(r.se), "p": float(r.p),
                    "i2": None if pd.isna(getattr(r, "i2", np.nan)) else float(r.i2),
                    "het_p": None if het is None or pd.isna(het) else float(het), "studies": studies})
    return out


DATASET_COLUMNS = [
    ("position", "Order in the direction column (1 = first symbol)"),
    ("dataset_id", "GWASHub dataset ID"), ("qc_run_id", "QC run whose cleaned file was used"),
    ("cohort", ""), ("phenotype", ""), ("ancestry", ""), ("sex", ""), ("n_cases", ""), ("n_controls", ""),
    ("n_total", "Total sample size (cases + controls, or the participants of a quantitative trait)"),
    ("variants_in", "Variants in the cleaned (QC'd) file"),
    ("removed_missing_beta_se", "Removed: BETA or SE missing, or SE not positive"),
    ("removed_maf", "Removed: below the method's minimum MAF"),
    ("variants_used", "Variants passed to the method"),
    ("in_results", "Variants in meta_results.tsv.gz with this dataset contributing"),
    ("lambda_gc", "λGC of the cleaned file"),
    ("lead_loci_covered", "Lead loci (their lead SNP) present in this dataset"),
    ("direction_agreement", "Share of those lead loci where this dataset's effect has the same sign as the meta-analysis"),
    ("outlier_loci", "Lead loci where this dataset's effect differs from the pooled effect by more than 3 of its standard errors"),
]


def dataset_overlap(specs, z_max=2.0, warn_r=0.1, same_r=0.98, min_variants=1000):
    """Pairs of datasets whose Z-scores correlate across mostly-null variants (|Z| < z_max in both): shared samples
    (shared controls, one dataset inside another) or the same data uploaded twice. Fixed-effect meta-analysis assumes
    independent datasets, so overlap makes its results overconfident (EasyQC's meta-level check, Winkler et al. 2014).
    Returns ([{a, b, n_variants, z_correlation}], messages). Shared polygenic signal adds a little correlation too, so
    this warns rather than judges."""
    z = []
    for s in specs:
        df = pd.read_csv(s["path"], sep="\t", usecols=["chr", "pos", "ea", "oa", "beta", "se"], dtype={"chr": str})
        df = df[df["se"] > 0]
        a1 = np.minimum(df["ea"], df["oa"])
        zz = np.where(df["ea"] == a1, 1, -1) * df["beta"] / df["se"]  # Z for the alphabetically first allele
        key = df["chr"] + ":" + df["pos"].astype(str) + ":" + a1 + ":" + np.maximum(df["ea"], df["oa"])
        z.append(pd.Series(zz.to_numpy(), index=key.to_numpy()).groupby(level=0).first())
    pairs, messages = [], []
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            both = pd.concat([z[i], z[j]], axis=1, join="inner").dropna()
            both = both[(both.abs() < z_max).all(axis=1)]
            if len(both) < min_variants:
                continue
            r = float(np.corrcoef(both.iloc[:, 0], both.iloc[:, 1])[0, 1])
            a, b = specs[i]["label"], specs[j]["label"]
            pairs.append({"a": a, "b": b, "n_variants": int(len(both)), "z_correlation": round(r, 4)})
            if r >= same_r:
                messages.append(f"{a} and {b} look like the same data (Z-score correlation {r:.3f}): check that one isn't "
                                "uploaded twice.")
            elif r > warn_r:
                messages.append(f"{a} and {b} may share samples (Z-scores of null variants correlate, r = {r:.2f}), e.g. shared "
                                "controls or one including the other: fixed-effect results will be overconfident. METAL's "
                                "overlap correction (sample-size scheme) allows for it.")
    return pairs, messages


def lead_locus_agreement(forest, studies):
    """Per dataset, at the lead loci (forest-plot data): how many it covers, the share with the same effect direction as
    the pooled estimate, and how many it is an outlier at (|beta - pooled beta| > 3 SE of the dataset's beta)."""
    by_id = {st.get("id"): st for st in studies}
    for st in studies:
        st.update(lead_loci_covered=0, lead_loci_total=len(forest), direction_agreement=None, outlier_loci=None)
    agree, compared, outliers = {}, {}, {}
    for f in forest:
        for x in f["studies"]:
            st = by_id.get(x["id"])
            if st is None:
                continue
            st["lead_loci_covered"] += 1
            if f.get("beta") is not None and x.get("se"):
                compared[x["id"]] = compared.get(x["id"], 0) + 1
                agree[x["id"]] = agree.get(x["id"], 0) + (np.sign(x["beta"]) == np.sign(f["beta"]))
                outliers[x["id"]] = outliers.get(x["id"], 0) + (abs(x["beta"] - f["beta"]) / x["se"] > 3)
    for i, n in compared.items():
        by_id[i]["direction_agreement"] = round(agree[i] / n, 3)
        by_id[i]["outlier_loci"] = int(outliers[i])


def write_datasets(specs, studies, out):
    """datasets.tsv: the datasets in input order (the order of the direction string) with their filter counts."""
    by_id = {st.get("id"): st for st in studies}
    names = [c for c, _ in DATASET_COLUMNS]
    rows = []
    for i, s in enumerate(specs, 1):
        st = by_id.get(s["id"], {})
        rows.append({"position": i, "dataset_id": s["id"], "qc_run_id": s.get("qc_run_id"), "cohort": s.get("cohort") or s["label"],
                     **{k: s.get(k) for k in ("phenotype", "ancestry", "sex", "n_cases", "n_controls", "n_total")},
                     **{k: st.get(k) for k in names[names.index("n_total") + 1:]}})  # the method's per-dataset counts
    pd.DataFrame(rows, columns=[c for c, _ in DATASET_COLUMNS]).to_csv(os.path.join(out, "datasets.tsv"), sep="\t", index=False)


RESULT_HELP = {
    "chr": "Chromosome (GRCh38)", "pos": "Position (GRCh38)", "rsid": "rsID, where the inputs had one",
    "ea": "Effect allele: the allele BETA, Z and EAF refer to. QC aligned every dataset so that this is the ALT allele of the reference",
    "oa": "Other (non-effect) allele: the REF allele", "beta": "Pooled effect of the effect allele", "se": "Standard error of BETA",
    "z": "Pooled Z-score of the effect allele", "p": "P-value", "n_studies": "Number of datasets with this variant",
    "n": "Total sample size over those datasets", "eaf": "Effect-allele frequency, weighted by sample size",
    "q": "Cochran's Q", "het_p": "Heterogeneity P-value (Cochran's Q)", "i2": "I² (0 to 1)",
    "eaf_min": "Smallest effect-allele frequency across datasets", "eaf_max": "Largest effect-allele frequency across datasets",
    "gender_differentiated_p": "Sex-differentiated P-value (GWAMA --sex)", "gender_heterogeneity_p": "Sex heterogeneity P-value (GWAMA --sex)",
    "direction": "One symbol per dataset, in the order of datasets.tsv: + effect allele raises the trait or risk, - lowers it, ? variant missing",
}


def write_readme(res, specs, a, opts, out, how):
    lines = [f"GWASHub meta-analysis: {a.title or a.method} ({a.method})", "",
             f"{len(specs)} datasets, {len(res):,} variants. Genome-wide threshold P < {opts['gws']:g}; lead loci by {how}.",
             "", "Files", "-----",
             "meta_results.tsv.gz  one row per variant (columns below)",
             "datasets.tsv         the datasets in input order, the order of the direction column, with how many variants each filter removed",
             "lead_loci.tsv        one row per locus (locus_id 1 = strongest): its strongest lead variant, the locus bounds, its lead SNPs",
             "signals.tsv          every independent significant SNP in a locus, with its locus_id, the lead SNP it clumped under",
             "                     (lead_snp), and whether it is a lead SNP (is_lead) or the locus's strongest (is_locus_lead)",
             "region_<id>.json     data for each locus's plot: variants with -log10 P and r² with the lead, signals, genes", "",
             "Columns of meta_results.tsv.gz", "------------------------------"]
    header = pd.read_csv(os.path.join(out, "meta_results.tsv.gz"), sep="\t", nrows=0).columns  # as the method wrote it
    lines += [f"{c:<12} {RESULT_HELP.get(c, '')}" for c in header]
    lines += ["", "Columns of datasets.tsv", "-----------------------"]
    lines += [f"{c:<24} {h}" for c, h in DATASET_COLUMNS if h]
    lines += ["", "Datasets (position: dataset)", "----------------------------"]
    lines += [f"{i:>3}: {s['label']}" for i, s in enumerate(specs, 1)]
    with open(os.path.join(out, "README.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    for a in ("--inputs", "--params", "--out", "--log", "--method"):
        ap.add_argument(a, required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--resources", default="")
    a = ap.parse_args()
    with open(a.inputs) as f:
        specs = json.load(f)
    with open(a.params) as f:
        opts = {**COMMON, **json.load(f)}
    with open(a.log) as f:
        mlog = json.load(f)
    res = pd.read_csv(os.path.join(a.out, "meta_results.tsv.gz"), sep="\t", dtype={"chr": str})
    missing = [c for c in ("chr", "pos", "ea", "oa", "p", "n_studies") if c not in res.columns]
    if missing or not ({"beta", "se"} <= set(res.columns) or "z" in res.columns):
        raise SystemExit(f"meta_results.tsv.gz is missing required columns: {', '.join(missing) or 'beta+se or z'}")
    res = res.dropna(subset=["p"])
    for col in ("beta", "se", "i2", "het_p"):
        if col not in res.columns:
            res[col] = np.nan
    if "direction" not in res.columns:
        res["direction"] = ""

    work = os.path.join(a.out, "_loci_work")
    leads, signals, loci_info = loci.define_loci(res, opts, a.resources, work)
    leads = regions.annotate_genes(leads, regions.load_genes(a.resources))
    region_files = regions.write_regions(res, leads, signals, a.resources, opts, a.out, work)
    __import__("shutil").rmtree(work, ignore_errors=True)
    forest = forest_data(leads, specs)
    if len(leads):
        leads = leads.assign(rsid=[f["rsid"] for f in forest])
    leads.to_csv(os.path.join(a.out, "lead_loci.tsv"), sep="\t", index=False, float_format=FLOAT_FORMAT)
    signals.to_csv(os.path.join(a.out, "signals.tsv"), sep="\t", index=False, float_format=FLOAT_FORMAT)
    with open(os.path.join(a.out, "forest.json"), "w") as f:
        json.dump(forest, f)
    lam, _ = make_plots(res, a.out, gws=opts["gws"], title=f"Meta-analysis ({len(specs)} datasets, {a.title or a.method})")

    # Per-dataset table: the method's own rows if it gave them, else counts from the inputs.
    studies = mlog.get("studies")
    if not studies:
        studies = []
        for s in specs:
            df = pd.read_csv(s["path"], sep="\t", usecols=lambda c: c == "p")
            lam_s = lambda_gc(df["p"]) if len(df) else float("nan")
            studies.append({"id": s["id"], "label": s["label"], "variants_in": len(df), "lambda_gc": round(lam_s, 4) if np.isfinite(lam_s) else None})
    # Variants each dataset contributed to the results: its symbol in the direction string isn't "?".
    for st, s in zip(studies, specs):
        st.setdefault("qc_run_id", s.get("qc_run_id"))
    lead_locus_agreement(forest, studies)
    dirs = res["direction"].fillna("").astype(str)
    if dirs.str.len().eq(len(specs)).all() and len(dirs):
        for i, st in enumerate(studies):
            st["in_results"] = int((dirs.str[i] != "?").sum())
    write_datasets(specs, studies, a.out)
    how = (f"LD clumping against the 1000 Genomes {opts['ld_panel']} panel (r² < {opts['iss_r2']}, then < {opts['lead_r2']}; "
           f"loci bounded by each lead's LD cloud and merged within {opts['merge_kb']} kb)"
           if loci_info["loci_method"] == "clumping" else f"a {opts['boundary_kb']} kb distance window (no LD panel uploaded)")
    write_readme(res, specs, a, opts, a.out, how)
    n_het = int((pd.to_numeric(leads["het_p"], errors="coerce") < opts["het_p_threshold"]).sum()) if len(leads) else 0
    pairs, overlap_messages = dataset_overlap(specs)
    summary = {
        "n_datasets": len(specs), "method": a.method, "method_title": a.title, "n_variants": int(len(res)),
        "n_genome_wide_sig": int((res["p"] < opts["gws"]).sum()), "n_loci": int(len(leads)), "n_loci_heterogeneous": n_het,
        "lambda_gc": None if not np.isfinite(lam) else round(lam, 4), "studies": studies, **loci_info,
        "n_region_files": len(region_files), "gene_track": regions.load_genes(a.resources) is not None,
        "dataset_pairs": pairs,
    }
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=float)
    messages = list(mlog.get("messages") or []) + [f"{len(leads)} genome-wide significant loci (P < {opts['gws']:g}; {how})."
                                                  + (f" {n_het} show significant heterogeneity." if n_het else "")]
    if loci_info.get("n_gws_not_in_panel"):
        messages.append(f"{loci_info['n_gws_not_in_panel']:,} genome-wide significant variants aren't in the LD panel and "
                        "couldn't be clumped.")
    messages += overlap_messages
    verdict = "warn" if n_het or overlap_messages or mlog.get("verdict") == "warn" else "pass"
    with open(a.log, "w") as f:
        json.dump({**mlog, "verdict": verdict, "messages": messages,
                   "metrics": {**(mlog.get("metrics") or {}), **{k: summary[k] for k in ("n_datasets", "n_variants", "n_genome_wide_sig",
                                                                                         "n_loci", "n_loci_heterogeneous", "lambda_gc")}}},
                  f, indent=2, default=float)


if __name__ == "__main__":
    main()
