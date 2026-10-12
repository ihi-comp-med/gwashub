"""GWAMA wrapper: write each dataset in GWAMA's input format, run GWAMA one chromosome at a time, map its output to the
standard results columns (see _metalib/common.py) and keep GWAMA's own log files for download.

One chromosome at a time because GWAMA holds every variant of every dataset in memory: 15 datasets of 9 to 25 million
variants didn't fit in 60 GB as one run. Each variant is meta-analysed on its own, so the results are the same as one
whole-genome run (pipeline/tests/test_meta_analysis.py compares the two); see the notes below for genomic control.

Always run as -qt (BETA and SE; odds ratios are already log-odds) with --indel_alleles, as the HERMES 3 analyses did.
Variants are matched across datasets by chr:pos and the alleles in either order (_metalib.common.marker_id).

Contract
--------
The parameters, their labels and help text are in analysis.yaml in this folder. The contract every method follows is
in CONTRIBUTING.md, section 6, and at the top of _metalib/common.py.

    python run_gwama.py --inputs inputs.json --params params.json --out OUT_DIR --log log.json

Inputs
  --inputs   [{"id", "label", "path", "n_cases", "n_controls", "n_total", "trait_type", "ancestry", "sex"}, ...], at least
             two. Each path is a QC'd file (GRCh38, ea = ALT): chr pos ea oa beta se p eaf [n ncase ncontrol rsid info].
  --params   model (fixed | random), min_studies, maf_min, genomic_control, genomic_control_output,
             direction_threshold, sex_differentiated (analysis.yaml).

Outputs
  OUT_DIR/meta_results.tsv.gz   chr pos rsid ea oa beta se z p n_studies n eaf q het_p i2 direction, plus
                                gender_differentiated_p and gender_heterogeneity_p with --sex. ea is GWAMA's
                                reference_allele (the allele beta refers to); n is the sum of the datasets' N.
  OUT_DIR/gwama_console.txt, gwama_log.txt, gwama_errors.txt   GWAMA's own output, for download (each chromosome's
                                in turn)
  OUT_DIR/genomic_control.tsv   with genomic_control: each dataset's lambda_GC and whether its SEs were corrected
  --log      {"method": "gwashub/gwama", "verdict": "pass", "metrics", "messages", "studies", "files"}; studies holds
             load_study's per-dataset counts. A failure exits non-zero with how GWAMA ended (its own exit code; 137
             when it was killed, usually for memory) and its last output (_metalib.common.tool_failed).
  _metalib/publish.py then adds the loci, plots and summary.

Notes for whoever maintains this
--------------------------------
- -qt tells GWAMA to read BETA and SE as they are. For case/control traits that is right because QC has already put
  every effect on the log-odds scale; -bt would expect odds ratios with 95% confidence limits instead.
- Fixed effects is inverse-variance weighting, beta = sum(w b) / sum(w) with w = 1 / se^2. --random is
  DerSimonian-Laird; with only a handful of datasets the between-study variance is poorly estimated and the random
  effects P-value is unreliable, so read it alongside Q and I^2.
- --genomic_control divides each dataset's chi^2 by its own lambda_GC (scaling its SE by sqrt(lambda)). With large,
  polygenic datasets this over-corrects, because their lambda reflects real signal.
- Genomic control needs all of a dataset's variants, so it can't be left to GWAMA once it runs per chromosome:
  genomic_control is applied here, before GWAMA, exactly as GWAMA 2.2.2 does it (readFile.cpp getLambda, marker.cpp
  addCohort): lambda = median((beta/se)^2) / 0.4549364 over the variants the dataset gives GWAMA, raised to 1 if
  below, and every SE multiplied by sqrt(lambda). genomic_control_output (GWAMA corrects the results' P-values by the
  lambda of all of them, worked out from statistics this wrapper can't see for the random model and the
  sex-differentiated test) is still left to GWAMA, so with it GWAMA runs once on the whole genome and needs the
  memory for that.
- Every chromosome's run lists every dataset in the same order (a dataset with no variants there gets an empty
  file), so the direction string means the same datasets on every chromosome.
- The direction string is GWAMA's "effects" column as written: "+" and "-" the sign of each dataset's effect, "0" an
  effect of exactly 0, and "?" either a dataset without the variant or, with --threshold below 1, one whose P is
  above the threshold. Because "?" can't tell those apart, each dataset's in_results is counted here from the markers
  it gave GWAMA, not by publish.py from the direction string.
"""
import os
import shutil

import numpy as np
import pandas as pd

from _metalib.common import load_study, marker_id, parse_args, run_tool, say, sort_results, split_marker, tool_failed, \
    write_log, write_results

SEX = {"Male": "M", "Female": "F"}
# GWAMA's output files -> (download name, label); each run's are appended in turn
OUTPUTS = {"gwama.log.out": ("gwama_log.txt", "GWAMA log"), "gwama.err.out": ("gwama_errors.txt", "GWAMA errors and warnings"),
           "gwama.gc.out": ("gwama_gc.txt", "GWAMA genomic control (λ per dataset)")}
COLUMNS = ["MARKERNAME", "EA", "NEA", "BETA", "SE", "N", "EAF"]
CHI2_MEDIAN = 0.4549364  # GWAMA's median of chi-squared with 1 df (qchisq(0.5, 1))


def chrom_key(c):
    return (0, int(c)) if str(c).isdigit() else (1, str(c))


def gwama_lambda(beta, se):
    """lambda_GC as GWAMA 2.2.2 computes it for --genomic_control (readFile.cpp getLambda)."""
    chi2 = (np.asarray(beta, float) / np.asarray(se, float)) ** 2
    return float(np.median(chi2)) / CHI2_MEDIAN if len(chi2) else 1.0


def main():
    a, specs, p = parse_args()
    work = os.path.join(a.out, "gwama_work")
    os.makedirs(work, exist_ok=True)
    if p.get("sex_differentiated") and any(s.get("sex") not in SEX for s in specs):
        raise SystemExit("The sex-differentiated test needs every dataset to be male-only or female-only")
    whole_genome = bool(p.get("genomic_control_output")) or os.environ.get("GWASHUB_GWAMA_WHOLE_GENOME") == "1"
    # With GWASHUB_GWAMA_WHOLE_GENOME=1 (for comparisons) GWAMA also does --genomic_control itself, as before.
    gwama_gc = whole_genome and os.environ.get("GWASHUB_GWAMA_WHOLE_GENOME") == "1"

    # Each dataset's variants, split by chromosome into GWAMA's input format (one dataset in memory at a time).
    infos, chroms, gc_rows = [], set(), []
    for k, s in enumerate(specs, 1):
        say(f"Reading dataset {k} of {len(specs)}: {s.get('label', s['id'])}")
        df, info = load_study(s, p.get("maf_min", 0))
        df["MARKERNAME"] = marker_id(df)
        out = pd.DataFrame({"MARKERNAME": df["MARKERNAME"], "EA": df["ea"], "NEA": df["oa"], "BETA": df["beta"], "SE": df["se"],
                            "N": df["n"].round().astype("Int64"), "EAF": df["eaf"] if "eaf" in df else np.nan,
                            "rsid": df["rsid"] if "rsid" in df else np.nan, "chr": df["chr"].astype(str)})
        out = out.drop_duplicates("MARKERNAME")
        if p.get("genomic_control") and not gwama_gc:
            lam = gwama_lambda(out["BETA"], out["SE"])
            if lam > 1:
                out["SE"] = out["SE"] * np.sqrt(lam)
            info["genomic_control_lambda"] = round(lam, 4)
            gc_rows.append({"dataset": s.get("label", s["id"]), "lambda": lam, "se_corrected": lam > 1})
        groups = [("all", out)] if whole_genome else list(out.groupby("chr", sort=False))
        for c, part in groups:
            chroms.add(c)
            folder = os.path.join(work, f"chr_{c}")
            os.makedirs(folder, exist_ok=True)
            part[[c for c in COLUMNS if c != "EAF" or "eaf" in df]].to_csv(os.path.join(folder, f"study_{s['id']}.txt"), sep="\t", index=False, na_rep="NA")
            part[["MARKERNAME", "rsid"]].dropna().to_csv(os.path.join(folder, f"rsid_{s['id']}.txt"), sep="\t", index=False)
        infos.append(info)
        del df, out, groups
    if gc_rows:
        pd.DataFrame(gc_rows).to_csv(os.path.join(a.out, "genomic_control.tsv"), sep="\t", index=False, float_format="%.6g")

    flags = ["-qt", "--indel_alleles", "--threshold", str(p.get("direction_threshold", 1))]
    if p.get("model") == "random":
        flags.append("--random")
    if gwama_gc and p.get("genomic_control"):
        flags.append("--genomic_control")
    if p.get("genomic_control_output"):
        flags.append("--genomic_control_output")
    if p.get("sex_differentiated"):
        flags.append("--sex")

    order = sorted(chroms, key=chrom_key)
    console = os.path.join(a.out, "gwama_console.txt")
    open(console, "w").close()
    for name, _ in OUTPUTS.values():
        open(os.path.join(a.out, name), "w").close()
    results, in_results = [], [0] * len(specs)
    for c in order:
        folder = os.path.join(work, f"chr_{c}")
        where = "" if c == "all" else f" on chromosome {c}"
        lines = []
        for s in specs:  # every dataset, in the same order, on every chromosome (the direction string)
            path = os.path.join(folder, f"study_{s['id']}.txt")
            if not os.path.exists(path):
                pd.DataFrame(columns=COLUMNS).to_csv(path, sep="\t", index=False)
            lines.append(f"{path}\t{SEX[s['sex']]}" if p.get("sex_differentiated") else path)
        with open(os.path.join(folder, "gwama.in"), "w") as f:
            f.write("\n".join(lines) + "\n")
        cmd = ["GWAMA", "-i", os.path.join(folder, "gwama.in"), "-o", os.path.join(folder, "gwama"), *flags]
        say(f"Running GWAMA on {len(specs)} datasets{where}" + ("" if c == "all" else f" ({order.index(c) + 1} of {len(order)})"))
        code, output = run_tool(cmd)
        with open(console, "a") as f:
            f.write(("" if c == "all" else f"===== chromosome {c}\n") + "$ " + " ".join(cmd) + "\n\n" + output + "\n")
        for src, (name, _) in OUTPUTS.items():
            if os.path.exists(os.path.join(folder, src)):
                with open(os.path.join(folder, src)) as fin, open(os.path.join(a.out, name), "a") as fout:
                    fout.write(("" if c == "all" else f"===== chromosome {c}\n") + fin.read())
        if code != 0 or not os.path.exists(os.path.join(folder, "gwama.out")):
            err = os.path.join(folder, "gwama.err.out")
            tail = [ln for ln in open(err).read().splitlines() if ln.strip()][-5:] if os.path.exists(err) else []
            tool_failed("GWAMA", code, output, where, "Its error file ends:\n" + "\n".join(tail) if tail else "")

        g = pd.read_csv(os.path.join(folder, "gwama.out"), sep="\t")
        chrom, pos = split_marker(g["rs_number"])
        rsids = pd.concat([pd.read_csv(os.path.join(folder, f"rsid_{s['id']}.txt"), sep="\t", dtype=str)
                           for s in specs if os.path.exists(os.path.join(folder, f"rsid_{s['id']}.txt"))] or
                          [pd.DataFrame(columns=["MARKERNAME", "rsid"])]).drop_duplicates("MARKERNAME").set_index("MARKERNAME")["rsid"]
        res = pd.DataFrame({"chr": chrom, "pos": pos, "rsid": g["rs_number"].map(rsids), "ea": g["reference_allele"],
                            "oa": g["other_allele"], "beta": g["beta"], "se": g["se"], "z": g["z"], "p": g["p-value"],
                            "n_studies": g["n_studies"], "n": g["n_samples"], "q": g["q_statistic"], "het_p": g["q_p-value"],
                            "i2": g["i2"], "direction": g["effects"].astype(str)})  # as GWAMA writes it: + - 0 ?
        if "eaf" in g:
            res["eaf"] = g["eaf"].where(g["eaf"] >= 0)
        for extra in ("gender_differentiated_p-value", "gender_heterogeneity_p-value"):  # with --sex
            if extra in g:
                res[extra.replace("-value", "").replace("-", "_")] = g[extra]
        keep = res["n_studies"] >= p.get("min_studies", 2)
        kept = set(g.loc[keep, "rs_number"])
        for i, s in enumerate(specs):  # variants in the results each dataset contributed to
            mk = pd.read_csv(os.path.join(folder, f"study_{s['id']}.txt"), sep="\t", usecols=["MARKERNAME"])["MARKERNAME"]
            in_results[i] += int(mk.isin(kept).sum())
        results.append(res[keep])
        shutil.rmtree(folder)  # this chromosome's inputs: reproducible from the datasets
    for info, n in zip(infos, in_results):
        info["in_results"] = n

    res = sort_results(pd.concat(results, ignore_index=True))
    say(f"Writing {len(res):,} variants (in at least {p.get('min_studies', 2)} datasets)")
    write_results(res, a.out)
    files = [{"path": "gwama_console.txt", "label": "GWAMA console output"},
             *({"path": name, "label": label} for name, label in OUTPUTS.values() if os.path.getsize(os.path.join(a.out, name)))]
    for name, _ in OUTPUTS.values():  # GWAMA writes no genomic control file unless it does genomic control itself
        if not os.path.getsize(os.path.join(a.out, name)):
            os.remove(os.path.join(a.out, name))
    if gc_rows:
        files.append({"path": "genomic_control.tsv", "label": "Genomic control: λGC per dataset (SEs corrected when above 1)"})
    options = [o for o in ("random", "genomic_control", "genomic_control_output", "sex") if f"--{o}" in flags]
    if p.get("genomic_control") and not gwama_gc:
        options.append("genomic control per dataset, as GWAMA's --genomic_control")
    how = "on the whole genome in one run" if whole_genome else f"one chromosome at a time ({len(order)} runs)"
    write_log(a.log, "gwashub/gwama", "pass", {"model": p.get("model", "fixed"), "runs": len(order)},
              [f"GWAMA {p.get('model', 'fixed')}-effects meta-analysis of {len(res):,} variants across {len(specs)} datasets, "
               f"{how}" + (f" (options: {', '.join(o if ' ' in o else '--' + o for o in options)})." if options else ".")],
              studies=infos, files=files)
    shutil.rmtree(work)  # the inputs are reproducible from the datasets


if __name__ == "__main__":
    main()
