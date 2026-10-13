"""GWASHub code around the Skin Genetics Consortium's qc_munge.R (copied unchanged from github.com/Katie-Watts/SGC; see
SOURCE.md). qc_munge.R runs MungeSumstats with SGC's filters, lifts the data to GRCh38, checks and flips alleles against
the reference genome and fills rsIDs from dbSNP 155. Nothing here changes what it does: this code only gives it a cohort
file and turns its output back into GWASHub's.

Contract
--------
The parameters are in step.yaml in this folder. The contract every QC step follows is in CONTRIBUTING.md, section 2.

    python gwashub_qc_munge.py --input working.tsv --resources DIR --params params.json --log step_log.json

Inputs
  --input       GWASHub format (after gwashub/parse), in the build declared at upload.
  --params      JSON. Read here: build (GRCh37 or GRCh38), cohort, phenotype, ancestry, sex (for SGC's file name), and
                under "step": pass_build, threads, warn_removed_fraction.
  Environment   GWASHUB_ARTIFACT_DIR: where the artifacts go.

What it does
  1. Before: writes the dataset as a cohort file with MungeSumstats' own column names (COHORT_COLUMNS), named as SGC
     names cohort files (cohort_PHENOTYPE_stratum).
  2. Runs  Rscript qc_munge.R --in <cohort file> --out <dir> --threads N --chain <file> [--ref-genome <declared build>]
     --chain (GWASHub edit 2 to qc_munge.R, SOURCE.md) is a copy of the Ensembl GRCh37-to-GRCh38 chain file that
     MungeSumstats ships, the chain qc_munge.R would otherwise download: every run uses the same file, and steps never
     use the network.
  3. After: reads qc_munge.R's output (SGC's columns) back into the GWASHub format (SGC_COLUMNS), and lists the rows
     MungeSumstats removed (the files it writes for each filter, in its log folder) and those qc_munge.R's own MAF filter
     removed.

Outputs
  --input       Rewritten in the GWASHub format: GRCh38, effect allele = ALT (MungeSumstats' A2 after its flip check).
                n is ncase + ncontrol where both are given, else the study's total (qc_munge.R doesn't carry N).
  --log         {"step": "qc_munge", "verdict", "metrics", "messages", "artifacts"}.
  Artifacts     <cohort file>.tsv.gz  qc_munge.R's output as written (SGC's columns), for SGC's own tools
                removed_rows.tsv.gz   rows removed, with MungeSumstats' or qc_munge.R's reason

Verdict
  fail   qc_munge.R wrote no output (MungeSumstats stopped: its message is in the step log)
  warn   more than warn_removed_fraction of the rows removed
  pass   otherwise
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

from _lib.common import STANDARD, load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"pass_build": True, "threads": 4, "warn_removed_fraction": 0.05}
HERE = os.path.dirname(os.path.abspath(__file__))
# GWASHub column -> the name written for MungeSumstats (its own standard names, which its header map keeps as they are;
# A2 is the effect allele, A1 the other, as qc_munge.R says).
COHORT_COLUMNS = {"rsid": "SNP", "chr": "CHR", "pos": "BP", "ea": "A2", "oa": "A1", "beta": "BETA", "se": "SE", "p": "P",
                  "eaf": "FRQ", "info": "INFO", "n": "N", "ncase": "N_CAS", "ncontrol": "N_CON"}
# qc_munge.R's output column -> GWASHub column
SGC_COLUMNS = {"Chromosome": "chr", "Position": "pos", "Effect_allele": "ea", "Non-effect_allele": "oa", "Beta": "beta",
               "SE": "se", "P-value": "p", "Effect_AF": "eaf", "Imp_Quality": "info", "RSID": "rsid", "N_CAS": "ncase",
               "N_CON": "ncontrol"}
CHROMS = {str(c) for c in range(1, 23)} | {"X", "Y"}
# The chain file MungeSumstats ships (Ensembl GRCh37 to GRCh38, its copy of 2022-11-25).
SHIPPED_CHAIN = 'cat(system.file("extdata", "GRCh37_to_GRCh38.chain.gz", package = "MungeSumstats"))'


def say(msg):
    print(msg, flush=True)


def sgc_name(params):
    """A cohort file name as SGC names them: cohort_PHENOTYPE_stratum (letters, digits and _ only)."""
    parts = [params.get("cohort") or "cohort", params.get("phenotype") or "PHENO", params.get("ancestry") or "",
             params.get("sex") or ""]
    clean = [re.sub(r"[^A-Za-z0-9]+", "_", str(p)).strip("_") for p in parts if p]
    return "_".join(c for c in clean if c) or "cohort"


def reason_of(path, folder):
    """MungeSumstats names each file of removed rows after its filter (e.g. info_filter.tsv.gz)."""
    name = os.path.relpath(path, folder)
    name = re.sub(r"\.(tsv|txt|csv)(\.gz)?$", "", os.path.basename(name))
    return "MungeSumstats: " + name.replace("_", " ")


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    art = os.environ.get("GWASHUB_ARTIFACT_DIR") or tempfile.mkdtemp()
    os.makedirs(art, exist_ok=True)

    df = read_working(args.input)
    rows_in = len(df)
    work = tempfile.mkdtemp(prefix="sgc_")
    base = sgc_name(params)
    cohort = os.path.join(work, "cohort_files", f"{base}.tsv.gz")
    os.makedirs(os.path.dirname(cohort))
    cols = [c for c in COHORT_COLUMNS if c in df]
    df[cols].rename(columns=COHORT_COLUMNS).to_csv(cohort, sep="\t", index=False, na_rep="NA", compression="gzip")
    del df
    out = os.path.join(work, "qc")
    # A copy: MungeSumstats unzips a local chain file where it is.
    shipped = subprocess.run(["Rscript", "-e", SHIPPED_CHAIN], capture_output=True, text=True, check=True).stdout.strip()
    chain = os.path.join(work, "GRCh37_to_GRCh38.chain.gz")
    shutil.copy(shipped, chain)
    cmd = ["Rscript", os.path.join(HERE, "qc_munge.R"), "--in", cohort, "--out", out, "--threads", str(int(opts["threads"])),
           "--chain", chain]
    build = params.get("build")
    if opts["pass_build"] and build in ("GRCh37", "GRCh38"):
        cmd += ["--ref-genome", build]
    say("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    console = []
    for line in proc.stdout:
        console.append(line)
        print(line, end="", flush=True)
    code = proc.wait()

    logs = os.path.join(out, "_logs", base)
    result = os.path.join(out, f"{base}.tsv.gz")
    if code != 0 or not os.path.exists(result):
        fails = [ln.strip() for ln in console if "[FAIL" in ln or "Error" in ln][-5:]
        write_log(args.log, "qc_munge", "fail", {"rows_in": rows_in, "exit_code": code},
                  ["qc_munge.R wrote no output: " + (" ".join(fails) or f"it exited with {code}") + " The whole console is in the step's output."])
        return
    shutil.copy(result, os.path.join(art, f"{base}.tsv.gz"))

    # After: qc_munge.R's columns -> the GWASHub format.
    sgc = pd.read_csv(result, sep="\t", dtype={"Chromosome": str, "RSID": str}, low_memory=False)
    res = sgc.rename(columns=SGC_COLUMNS)[[c for c in SGC_COLUMNS.values() if c in sgc.rename(columns=SGC_COLUMNS)]]
    res["chr"] = res["chr"].astype(str).str.replace(r"^chr", "", regex=True, case=False).str.upper().replace({"23": "X", "24": "Y"})
    other = ~res["chr"].isin(CHROMS)
    removed = []
    if other.any():
        removed.append(sgc[other.to_numpy()].assign(reason="chromosome not 1-22, X or Y"))
        res = res[~other]
    for c in ("ncase", "ncontrol", "info", "eaf"):
        if c in res and res[c].isna().all():
            res = res.drop(columns=c)
    total = params.get("n_total") or ((params.get("n_cases") or 0) + (params.get("n_controls") or 0)) or np.nan
    res["n"] = (res["ncase"] + res["ncontrol"]) if {"ncase", "ncontrol"} <= set(res) else total
    if {"ncase", "ncontrol"} <= set(res):
        res["n"] = res["n"].fillna(total)
    res["pos"] = res["pos"].astype("Int64")
    res[[c for c in STANDARD if c in res]].to_csv(args.input, sep="\t", index=False, na_rep="NA")
    rows_out = len(res)

    # Removed rows: MungeSumstats writes those each filter removed in its log folder; qc_munge.R's own MAF filter
    # (on the munged file, before its output) is listed here by applying the same rule to the munged file.
    for path in sorted(glob.glob(os.path.join(logs, "**", "*.tsv.gz"), recursive=True)) if os.path.isdir(logs) else []:
        try:
            removed.append(pd.read_csv(path, sep="\t", dtype=str).assign(reason=reason_of(path, logs)))
        except Exception:
            pass
    munged = os.path.join(out, "_munged", f"{base}.munged.tsv.gz")
    if os.path.exists(munged):
        m = pd.read_csv(munged, sep="\t", low_memory=False)
        if "FRQ" in m:
            frq = pd.to_numeric(m["FRQ"], errors="coerce")
            low = frq.notna() & (np.minimum(frq, 1 - frq) < 0.005)
            if low.any():
                removed.append(m[low.to_numpy()].astype(str).assign(reason="qc_munge.R: MAF below 0.005"))
    artifacts = [{"type": "table", "path": f"{base}.tsv.gz", "label": "SGC format (qc_munge.R output)", "rows": rows_out,
                  "description": "qc_munge.R's output as written, in SGC's columns."}]
    n_removed = sum(len(r) for r in removed)
    if removed:
        table = pd.concat(removed, ignore_index=True, sort=False)
        table.to_csv(os.path.join(art, "removed_rows.tsv.gz"), sep="\t", index=False, na_rep="NA", compression="gzip")
        artifacts.append({"type": "table", "path": "removed_rows.tsv.gz", "label": "Removed rows", "rows": len(table),
                          "description": "Each row MungeSumstats or qc_munge.R removed, with the filter that removed it."})

    frac = 1 - rows_out / rows_in if rows_in else 0
    messages = [f"qc_munge.R (MungeSumstats) kept {rows_out:,} of {rows_in:,} rows ({100 * frac:.2f}% removed); now on GRCh38.",
                *[ln.strip() for ln in console if ln.strip().startswith("[MAF]")]]
    by_reason = pd.concat(removed)["reason"].value_counts() if removed else pd.Series(dtype=int)
    messages += [f"{reason}: {count:,} rows." for reason, count in by_reason.items()]
    verdict = "warn" if frac > opts["warn_removed_fraction"] else "pass"
    if verdict == "warn":
        messages.append(f"More than {100 * opts['warn_removed_fraction']:.0f}% of rows removed.")
    write_log(args.log, "qc_munge", verdict,
              {"rows_in": rows_in, "rows_out": rows_out, "removed_fraction": round(frac, 4), "rows_listed_removed": n_removed,
               "build_passed": cmd[cmd.index("--ref-genome") + 1] if "--ref-genome" in cmd else "inferred"}, messages, artifacts)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
