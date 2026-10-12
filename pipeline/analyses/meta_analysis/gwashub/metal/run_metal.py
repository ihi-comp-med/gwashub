"""METAL wrapper: write each dataset and a METAL script, run METAL, map its output to the standard results
columns (see _metalib/common.py) and keep METAL's script, console output and .info file for download.
Variants are matched across datasets by chr:pos and the alleles in either order (_metalib.common.marker_id).

Contract
--------
The parameters, their labels and help text are in analysis.yaml in this folder. The contract every method follows is
in CONTRIBUTING.md, section 6, and at the top of _metalib/common.py.

    python run_metal.py --inputs inputs.json --params params.json --out OUT_DIR --log log.json

Inputs
  --inputs   [{"id", "label", "path", "n_cases", "n_controls", "n_total", "trait_type", "ancestry", "sex"}, ...], at least
             two. Each path is a QC'd file (GRCh38, ea = ALT): chr pos ea oa beta se p eaf [n ncase ncontrol rsid info].
  --params   scheme (stderr | samplesize), min_studies, maf_min, genomic_control, overlap, zcutoff, min_weight,
             minmax_freq, precision (analysis.yaml).

Outputs
  OUT_DIR/meta_results.tsv.gz   chr pos rsid ea oa p direction n_studies q het_p i2 eaf [eaf_min eaf_max], plus beta se
                                (stderr scheme) or z n (samplesize scheme). ea is METAL's Allele1 (upper-cased); i2 is
                                METAL's HetISq / 100; n_studies is HetDf + 1.
  OUT_DIR/metal_script.txt, metal_console.txt, metal_info.txt   METAL's script and its own output, for download
  --log      {"method": "gwashub/metal", "verdict": "pass", "metrics", "messages", "studies", "files"}. A failure exits
             non-zero with how METAL ended (its own exit code; 137 when it was killed, usually for memory) and its
             last output (_metalib.common.tool_failed).
  _metalib/publish.py then adds the loci, plots and summary.

Notes for whoever maintains this
--------------------------------
- SCHEME STDERR is fixed-effects inverse-variance weighting and should agree with GWAMA's fixed model to the printed
  precision (validation/03_metal checks this). SCHEME SAMPLESIZE combines Z-scores taken from P and the sign of BETA,
  weighted by sqrt(N): it ignores the SEs, so it is robust to datasets whose effects are on different scales, but it
  gives no pooled effect size and relies on N being right.
- In the samplesize scheme N is the dataset's per-variant N (or its total when it has no N column). For case/control
  traits an effective N, 4 / (1/cases + 1/controls), is the better weight when case fractions differ a lot between
  datasets; the total N is used here.
- OVERLAP ON (samplesize scheme only) estimates the correlation of each pair of datasets' Z-scores from variants
  with |Z| < ZCUTOFF and adjusts the weights for it. In the stderr scheme METAL ignores it.
- GENOMICCONTROL ON corrects each file by the lambda METAL estimates from that file; the same over-correction caveat
  as for GWAMA applies to large polygenic datasets.
- METAL's default print precision (4 significant digits) rounds effects and SEs noticeably, hence precision = 8.
- The direction string is METAL's as written: "+", "-", "0" (an effect of exactly 0) or "?" (variant missing).
- A dataset without EAF is given 0.5 so METAL's frequency columns stay complete; the pooled eaf is then pulled
  towards 0.5 and shouldn't be used for those variants.
"""
import os
import shutil

import pandas as pd

from _metalib.common import load_study, marker_id, parse_args, run_tool, say, split_marker, tool_failed, write_log, write_results


def main():
    a, specs, p = parse_args()
    work = os.path.join(a.out, "metal_work")
    os.makedirs(work, exist_ok=True)
    scheme = p.get("scheme", "stderr")
    digits = int(p.get("precision", 8))
    script = ["SCHEME " + ("STDERR" if scheme == "stderr" else "SAMPLESIZE"), "AVERAGEFREQ ON",
              "MINMAXFREQ " + ("ON" if p.get("minmax_freq") else "OFF"),
              "GENOMICCONTROL " + ("ON" if p.get("genomic_control") else "OFF"),
              "OVERLAP " + ("ON" if p.get("overlap") else "OFF"), f"ZCUTOFF {p.get('zcutoff', 1)}",
              f"MINWEIGHT {p.get('min_weight', 1)}", f"EFFECT_PRINT_PRECISION {digits}", f"STDERR_PRINT_PRECISION {digits}",
              "MARKER MARKERNAME", "ALLELE EA NEA", "EFFECT BETA", "STDERR SE", "PVALUE P", "WEIGHT N", "FREQ EAF",
              "COLUMNCOUNTING LENIENT"]
    infos, rsids = [], {}
    for k, s in enumerate(specs, 1):
        say(f"Reading dataset {k} of {len(specs)}: {s.get('label', s['id'])}")
        df, info = load_study(s, p.get("maf_min", 0))
        df["MARKERNAME"] = marker_id(df)
        if "rsid" in df:
            rsids.update(dict(zip(df["MARKERNAME"], df["rsid"])))
        out = pd.DataFrame({"MARKERNAME": df["MARKERNAME"], "EA": df["ea"], "NEA": df["oa"], "BETA": df["beta"], "SE": df["se"],
                            "P": df["p"], "N": df["n"], "EAF": df["eaf"] if "eaf" in df else 0.5})
        path = os.path.join(work, f"study_{s['id']}.txt")
        out.drop_duplicates("MARKERNAME").to_csv(path, sep="\t", index=False, na_rep="NA")
        script.append(f"PROCESS {path}")
        infos.append(info)
    script += [f"OUTFILE {os.path.join(work, 'metal')} .tbl", "ANALYZE HETEROGENEITY", "QUIT"]
    with open(os.path.join(a.out, "metal_script.txt"), "w") as f:
        f.write("\n".join(script) + "\n")
    say(f"Running METAL on {len(specs)} datasets")
    code, output = run_tool(["metal", os.path.join(a.out, "metal_script.txt")])
    with open(os.path.join(a.out, "metal_console.txt"), "w") as f:
        f.write(output)
    files = [{"path": "metal_script.txt", "label": "METAL script"}, {"path": "metal_console.txt", "label": "METAL console output"}]
    tbl = os.path.join(work, "metal1.tbl")
    if os.path.exists(tbl + ".info"):
        shutil.move(tbl + ".info", os.path.join(a.out, "metal_info.txt"))
        files.append({"path": "metal_info.txt", "label": "METAL run information"})
    if code != 0 or not os.path.exists(tbl):
        tool_failed("METAL", code, output)

    say("Reading METAL's results")
    m = pd.read_csv(tbl, sep="\t")
    chrom, pos = split_marker(m["MarkerName"])
    res = pd.DataFrame({"chr": chrom, "pos": pos, "rsid": m["MarkerName"].map(rsids), "ea": m["Allele1"].str.upper(),
                        "oa": m["Allele2"].str.upper(), "p": m["P-value"], "direction": m["Direction"],
                        "n_studies": m["HetDf"] + 1, "q": m["HetChiSq"], "het_p": m["HetPVal"], "i2": m["HetISq"] / 100})
    if scheme == "stderr":
        res["beta"], res["se"] = m["Effect"], m["StdErr"]
    else:
        res["z"], res["n"] = m["Zscore"], m["Weight"]
    for src, dst in (("Freq1", "eaf"), ("MinFreq", "eaf_min"), ("MaxFreq", "eaf_max")):
        if src in m:
            res[dst] = m[src]
    res = res[res["n_studies"] >= p.get("min_studies", 2)]
    say(f"Writing {len(res):,} variants (in at least {p.get('min_studies', 2)} datasets)")
    write_results(res, a.out)
    write_log(a.log, "gwashub/metal", "pass", {"scheme": scheme},
              [f"METAL ({scheme} scheme) meta-analysis of {len(res):,} variants across {len(specs)} datasets."],
              studies=infos, files=files)
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
