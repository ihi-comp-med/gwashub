"""GWASHub's post-processing after any meta-analysis method: lead loci, forest-plot data, Manhattan and
QQ plots, and summary.json, all from the method's meta_results.tsv.gz and the inputs.

    python publish.py --inputs inputs.json --params common.json --out OUT_DIR --log method_log.json --method KEY
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from _lib.plotting import lambda_gc, make_plots

COMMON = {"gws": 5e-8, "clump_kb": 500, "het_p_threshold": 1e-3}


def clump(res, gws, window):
    hits = res[res["p"] < gws].sort_values("p")
    leads = []
    for row in hits.itertuples(index=False):
        if any(l.chr == row.chr and abs(l.pos - row.pos) < window for l in leads):
            continue
        leads.append(row)
    return pd.DataFrame(leads, columns=res.columns) if leads else res.iloc[0:0]


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
        out.append({"chr": str(r.chr), "pos": int(r.pos), "rsid": None if rsid is None or pd.isna(rsid) else str(rsid), "ea": r.ea, "oa": r.oa,
                    "beta": None if pd.isna(getattr(r, "beta", np.nan)) else float(r.beta),
                    "se": None if pd.isna(getattr(r, "se", np.nan)) else float(r.se), "p": float(r.p),
                    "i2": None if pd.isna(getattr(r, "i2", np.nan)) else float(r.i2),
                    "het_p": None if het is None or pd.isna(het) else float(het), "studies": studies})
    return out


DATASET_COLUMNS = [
    ("position", "Order in the direction column (1 = first symbol)"),
    ("dataset_id", "GWASHub dataset ID"), ("qc_run_id", "QC run whose cleaned file was used"),
    ("cohort", ""), ("phenotype", ""), ("ancestry", ""), ("sex", ""), ("n_cases", ""), ("n_controls", ""),
    ("variants_in", "Variants in the cleaned (QC'd) file"),
    ("removed_missing_beta_se", "Removed: BETA or SE missing, or SE not positive"),
    ("removed_maf", "Removed: below the method's minimum MAF"),
    ("removed_palindromic", "Removed: palindromic SNPs (when chosen)"),
    ("variants_used", "Variants passed to the method"),
    ("in_results", "Variants in meta_results.tsv.gz with this dataset contributing"),
    ("lambda_gc", "λGC of the cleaned file"), ("gc_applied", "λ used for genomic control (SE × √λ), if applied"),
]


def write_datasets(specs, studies, out):
    """datasets.tsv: the datasets in input order (the order of the direction string) with their filter counts."""
    by_id = {st.get("id"): st for st in studies}
    rows = []
    for i, s in enumerate(specs, 1):
        st = by_id.get(s["id"], {})
        rows.append({"position": i, "dataset_id": s["id"], "qc_run_id": s.get("qc_run_id"), "cohort": s.get("cohort") or s["label"],
                     **{k: s.get(k) for k in ("phenotype", "ancestry", "sex", "n_cases", "n_controls")},
                     **{k: st.get(k) for k, _ in DATASET_COLUMNS[9:]}})
    pd.DataFrame(rows, columns=[c for c, _ in DATASET_COLUMNS]).to_csv(os.path.join(out, "datasets.tsv"), sep="\t", index=False)


RESULT_HELP = {
    "chr": "Chromosome (GRCh38)", "pos": "Position (GRCh38)", "rsid": "rsID, where the inputs had one",
    "ea": "Effect allele: the allele BETA, Z and EAF refer to. QC aligned every dataset so that this is the ALT allele of the reference",
    "oa": "Other (non-effect) allele: the REF allele", "beta": "Pooled effect of the effect allele", "se": "Standard error of BETA",
    "z": "Pooled Z-score of the effect allele", "p": "P-value", "n_studies": "Number of datasets with this variant",
    "n": "Total sample size over those datasets", "eaf": "Effect-allele frequency, weighted by sample size",
    "q": "Cochran's Q", "het_p": "Heterogeneity P-value (Cochran's Q)", "i2": "I² (0 to 1)", "tau2": "Between-dataset variance (random effects)",
    "direction": "One symbol per dataset, in the order of datasets.tsv: + effect allele raises the trait or risk, - lowers it, ? variant missing",
}


def write_readme(res, specs, a, opts, out):
    lines = [f"GWASHub meta-analysis: {a.title or a.method} ({a.method})", "",
             f"{len(specs)} datasets, {len(res):,} variants. Genome-wide threshold P < {opts['gws']:g}; lead loci at least "
             f"{opts['clump_kb']} kb apart.", "", "Files", "-----",
             "meta_results.tsv.gz  one row per variant (columns below)",
             "datasets.tsv         the datasets in input order, the order of the direction column, with how many variants each filter removed",
             "lead_loci.tsv        the strongest variant of each genome-wide significant locus", "",
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

    leads = clump(res, opts["gws"], opts["clump_kb"] * 1000)
    forest = forest_data(leads, specs)
    if len(leads):
        leads = leads.assign(rsid=[f["rsid"] for f in forest])
    leads.to_csv(os.path.join(a.out, "lead_loci.tsv"), sep="\t", index=False, float_format="%.6g")
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
    dirs = res["direction"].fillna("").astype(str)
    if dirs.str.len().eq(len(specs)).all() and len(dirs):
        for i, st in enumerate(studies):
            st["in_results"] = int((dirs.str[i] != "?").sum())
    write_datasets(specs, studies, a.out)
    write_readme(res, specs, a, opts, a.out)
    n_het = int((pd.to_numeric(leads["het_p"], errors="coerce") < opts["het_p_threshold"]).sum()) if len(leads) else 0
    summary = {
        "n_datasets": len(specs), "method": a.method, "method_title": a.title, "n_variants": int(len(res)),
        "n_genome_wide_sig": int((res["p"] < opts["gws"]).sum()), "n_loci": int(len(leads)), "n_loci_heterogeneous": n_het,
        "lambda_gc": None if not np.isfinite(lam) else round(lam, 4), "studies": studies,
    }
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=float)
    messages = list(mlog.get("messages") or []) + [f"{len(leads)} genome-wide significant loci (P < {opts['gws']:g})."
                                                  + (f" {n_het} show significant heterogeneity." if n_het else "")]
    verdict = "warn" if n_het or mlog.get("verdict") == "warn" else "pass"
    with open(a.log, "w") as f:
        json.dump({**mlog, "verdict": verdict, "messages": messages,
                   "metrics": {**(mlog.get("metrics") or {}), **{k: summary[k] for k in ("n_datasets", "n_variants", "n_genome_wide_sig",
                                                                                         "n_loci", "n_loci_heterogeneous", "lambda_gc")}}},
                  f, indent=2, default=float)


if __name__ == "__main__":
    main()
