"""GWAMA wrapper: write each dataset in GWAMA's input format, run GWAMA, map its output to the standard
results columns (see _metalib/common.py) and keep GWAMA's own log files for download.

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
  OUT_DIR/gwama_console.txt, gwama_log.txt, gwama_errors.txt, gwama_gc.txt   GWAMA's own output, for download
  --log      {"method": "gwashub/gwama", "verdict": "pass", "metrics", "messages", "studies", "files"}; studies holds
             load_study's per-dataset counts. A failure exits non-zero (SystemExit) with GWAMA's tail as the message.
  _metalib/publish.py then adds the loci, plots and summary.

Notes for whoever maintains this
--------------------------------
- -qt tells GWAMA to read BETA and SE as they are. For case/control traits that is right because QC has already put
  every effect on the log-odds scale; -bt would expect odds ratios with 95% confidence limits instead.
- Fixed effects is inverse-variance weighting, beta = sum(w b) / sum(w) with w = 1 / se^2. --random is
  DerSimonian-Laird; with only a handful of datasets the between-study variance is poorly estimated and the random
  effects P-value is unreliable, so read it alongside Q and I^2.
- --genomic_control divides each dataset's chi^2 by its own lambda_GC (scaling its SE by sqrt(lambda)). With large, polygenic datasets this over-corrects, because their lambda reflects real signal.
- The direction string is GWAMA's "effects" column as written: "+" and "-" the sign of each dataset's effect, "0" an
  effect of exactly 0, and "?" either a dataset without the variant or, with --threshold below 1, one whose P is
  above the threshold. Because "?" can't tell those apart, each dataset's in_results is counted here from the markers
  it gave GWAMA, not by publish.py from the direction string.
"""
import os
import shutil
import subprocess

import pandas as pd

from _metalib.common import load_study, marker_id, parse_args, split_marker, write_log, write_results

SEX = {"Male": "M", "Female": "F"}
# GWAMA's output files -> (download name, label)
OUTPUTS = {"gwama.log.out": ("gwama_log.txt", "GWAMA log"), "gwama.err.out": ("gwama_errors.txt", "GWAMA errors and warnings"),
           "gwama.gc.out": ("gwama_gc.txt", "GWAMA genomic control (λ per dataset)")}


def main():
    a, specs, p = parse_args()
    work = os.path.join(a.out, "gwama_work")
    os.makedirs(work, exist_ok=True)
    if p.get("sex_differentiated") and any(s.get("sex") not in SEX for s in specs):
        raise SystemExit("The sex-differentiated test needs every dataset to be male-only or female-only")
    lines, infos, rsids, markers = [], [], {}, []
    for s in specs:
        df, info = load_study(s, p.get("maf_min", 0))
        df["MARKERNAME"] = marker_id(df)
        if "rsid" in df:
            rsids.update(dict(zip(df["MARKERNAME"], df["rsid"])))
        out = pd.DataFrame({"MARKERNAME": df["MARKERNAME"], "EA": df["ea"], "NEA": df["oa"], "BETA": df["beta"], "SE": df["se"],
                            "N": df["n"].round().astype("Int64")})
        if "eaf" in df:
            out["EAF"] = df["eaf"]
        path = os.path.join(work, f"study_{s['id']}.txt")
        out.drop_duplicates("MARKERNAME").to_csv(path, sep="\t", index=False, na_rep="NA")
        lines.append(f"{path}\t{SEX[s['sex']]}" if p.get("sex_differentiated") else path)
        markers.append(set(out["MARKERNAME"]))
        infos.append(info)
    with open(os.path.join(work, "gwama.in"), "w") as f:
        f.write("\n".join(lines) + "\n")
    cmd = ["GWAMA", "-i", os.path.join(work, "gwama.in"), "-o", os.path.join(work, "gwama"), "-qt", "--indel_alleles",
           "--threshold", str(p.get("direction_threshold", 1))]
    if p.get("model") == "random":
        cmd.append("--random")
    if p.get("genomic_control"):
        cmd.append("--genomic_control")
    if p.get("genomic_control_output"):
        cmd.append("--genomic_control_output")
    if p.get("sex_differentiated"):
        cmd.append("--sex")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    with open(os.path.join(a.out, "gwama_console.txt"), "w") as f:
        f.write("$ " + " ".join(cmd) + "\n\n" + proc.stdout + proc.stderr)
    files = [{"path": "gwama_console.txt", "label": "GWAMA console output"}]
    for src, (name, label) in OUTPUTS.items():
        if os.path.exists(os.path.join(work, src)):
            shutil.move(os.path.join(work, src), os.path.join(a.out, name))
            files.append({"path": name, "label": label})
    if proc.returncode != 0 or not os.path.exists(os.path.join(work, "gwama.out")):
        raise SystemExit(f"GWAMA failed: {(proc.stderr or proc.stdout)[-1500:]}")

    g = pd.read_csv(os.path.join(work, "gwama.out"), sep="\t")
    chrom, pos = split_marker(g["rs_number"])
    res = pd.DataFrame({"chr": chrom, "pos": pos, "rsid": g["rs_number"].map(rsids), "ea": g["reference_allele"],
                        "oa": g["other_allele"], "beta": g["beta"], "se": g["se"], "z": g["z"], "p": g["p-value"],
                        "n_studies": g["n_studies"], "n": g["n_samples"], "q": g["q_statistic"], "het_p": g["q_p-value"],
                        "i2": g["i2"], "direction": g["effects"].astype(str)})  # as GWAMA writes it: + - 0 ?
    if "eaf" in g:
        res["eaf"] = g["eaf"].where(g["eaf"] >= 0)
    for extra in ("gender_differentiated_p-value", "gender_heterogeneity_p-value"):  # with --sex
        if extra in g:
            res[extra.replace("-value", "").replace("-", "_")] = g[extra]
    res = res[res["n_studies"] >= p.get("min_studies", 2)]
    for info, mk in zip(infos, markers):  # variants in the results this dataset contributed to
        info["in_results"] = int(g.loc[res.index, "rs_number"].isin(mk).sum())
    write_results(res, a.out)
    options = [o for o in ("random", "genomic_control", "genomic_control_output", "sex") if f"--{o}" in cmd]
    write_log(a.log, "gwashub/gwama", "pass", {"model": p.get("model", "fixed")},
              [f"GWAMA {p.get('model', 'fixed')}-effects meta-analysis of {len(res):,} variants across {len(specs)} datasets"
               + (f" (options: {', '.join('--' + o for o in options)})." if options else ".")],
              studies=infos, files=files)
    shutil.rmtree(work)  # the inputs are reproducible from the datasets


if __name__ == "__main__":
    main()
