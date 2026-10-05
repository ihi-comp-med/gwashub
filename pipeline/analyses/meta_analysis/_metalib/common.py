"""Shared helpers for meta-analysis methods (pipeline/analyses/meta_analysis/<owner>/<method>/).

The method contract (see CONTRIBUTING.md):

    <command> --inputs inputs.json --params params.json --out OUT_DIR --log log.json

inputs.json: [{"id", "label", "path" (QC'd TSV in the GWASHub format: chr pos ea oa beta se p [eaf n ncase ncontrol rsid info]), "n_cases",
"n_controls", "ancestry", "sex"}]. params.json: the method's own parameters (analysis.yaml).
A method must write OUT_DIR/meta_results.tsv.gz with at least  chr pos ea oa p n_studies  and either
beta + se or z; optional: rsid eaf n q het_p i2 direction. GWASHub's post-processing
(publish.py) then adds lead loci, forest-plot data, Manhattan/QQ plots and summary.json for every method.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from _lib.plotting import lambda_gc

RESULT_COLUMNS = ["chr", "pos", "rsid", "ea", "oa", "beta", "se", "z", "p", "n_studies", "n", "eaf", "q", "het_p", "i2",
                  "direction"]


def parse_args():
    ap = argparse.ArgumentParser()
    for a in ("--inputs", "--params", "--out", "--log"):
        ap.add_argument(a, required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    with open(a.inputs) as f:
        specs = json.load(f)
    with open(a.params) as f:
        params = json.load(f)
    if len(specs) < 2:
        raise SystemExit("A meta-analysis needs at least 2 datasets")
    return a, specs, params


def load_study(spec, maf_min=0.0):
    """One QC'd dataset, filtered. Returns (DataFrame, info for the per-study table)."""
    df = pd.read_csv(spec["path"], sep="\t", dtype={"chr": str})
    info = {"id": spec["id"], "label": spec["label"], "variants_in": len(df)}
    # Every filter records how many variants it removed (shown per dataset with the results).
    n = len(df)
    df = df.dropna(subset=["beta", "se"])
    df = df[df["se"] > 0]
    info["removed_missing_beta_se"] = n - len(df)
    lam = lambda_gc(df["p"]) if "p" in df else float("nan")
    info["lambda_gc"] = round(lam, 4) if np.isfinite(lam) else None
    if maf_min and "eaf" in df:
        n = len(df)
        maf = np.minimum(df["eaf"], 1 - df["eaf"])
        df = df[maf.isna() | (maf >= maf_min)]
        info["removed_maf"] = n - len(df)
    if "n" not in df or df["n"].isna().all():
        df["n"] = (spec.get("n_cases") or 0) + (spec.get("n_controls") or 0)
    info["variants_used"] = len(df)
    return df.reset_index(drop=True), info


def marker_id(df):
    """A study-independent marker name: chr:pos:allele1:allele2 with the alleles sorted, so the same
    variant matches across studies whichever allele each reports as effect allele."""
    a1 = np.where(df["ea"] < df["oa"], df["ea"], df["oa"])
    a2 = np.where(df["ea"] < df["oa"], df["oa"], df["ea"])
    return df["chr"].astype(str) + ":" + df["pos"].astype(int).astype(str) + ":" + pd.Series(a1, index=df.index) + ":" + pd.Series(a2, index=df.index)


def split_marker(markers):
    parts = markers.str.split(":", expand=True)
    return parts[0], parts[1].astype(int)


def sort_results(res):
    res = res.copy()
    res["_c"] = res["chr"].astype(str).map(lambda c: 23 if c == "X" else int(c) if str(c).isdigit() else 99)
    return res.sort_values(["_c", "pos"]).drop(columns="_c")


def write_results(res, out_dir):
    cols = [c for c in RESULT_COLUMNS if c in res.columns] + [c for c in res.columns if c not in RESULT_COLUMNS]
    sort_results(res)[cols].to_csv(os.path.join(out_dir, "meta_results.tsv.gz"), sep="\t", index=False, compression="gzip",
                                   float_format="%.6g")


def write_log(path, method, verdict, metrics=None, messages=None, studies=None, files=None):
    """The method's log (like a QC step log). `studies` (optional): per-dataset rows for the results page.
    `files` (optional): [{"path", "label"}] for files the method wrote in OUT_DIR (e.g. the program's own logs),
    offered for download with the results."""
    with open(path, "w") as f:
        json.dump({"method": method, "verdict": verdict, "metrics": metrics or {}, "messages": messages or [],
                   **({"studies": studies} if studies is not None else {}),
                   **({"files": files} if files else {})}, f, indent=2, default=float)
