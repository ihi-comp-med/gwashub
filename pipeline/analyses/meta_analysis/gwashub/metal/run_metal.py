"""METAL wrapper: write each dataset and a METAL script, run METAL, map its output to the standard results
columns (see _metalib/common.py) and keep METAL's script, console output and .info file for download.
Variants are matched across datasets by chr:pos and the alleles in either order (_metalib.common.marker_id)."""
import os
import shutil
import subprocess

import pandas as pd

from _metalib.common import load_study, marker_id, parse_args, split_marker, write_log, write_results


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
    for s in specs:
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
    proc = subprocess.run(["metal", os.path.join(a.out, "metal_script.txt")], capture_output=True, text=True)
    with open(os.path.join(a.out, "metal_console.txt"), "w") as f:
        f.write(proc.stdout + proc.stderr)
    files = [{"path": "metal_script.txt", "label": "METAL script"}, {"path": "metal_console.txt", "label": "METAL console output"}]
    tbl = os.path.join(work, "metal1.tbl")
    if os.path.exists(tbl + ".info"):
        shutil.move(tbl + ".info", os.path.join(a.out, "metal_info.txt"))
        files.append({"path": "metal_info.txt", "label": "METAL run information"})
    if proc.returncode != 0 or not os.path.exists(tbl):
        raise SystemExit(f"METAL failed: {(proc.stderr or proc.stdout)[-1500:]}")

    m = pd.read_csv(tbl, sep="\t")
    chrom, pos = split_marker(m["MarkerName"])
    res = pd.DataFrame({"chr": chrom, "pos": pos, "rsid": m["MarkerName"].map(rsids), "ea": m["Allele1"].str.upper(),
                        "oa": m["Allele2"].str.upper(), "p": m["P-value"], "direction": m["Direction"].str.replace("0", "?", regex=False),
                        "n_studies": m["HetDf"] + 1, "q": m["HetChiSq"], "het_p": m["HetPVal"], "i2": m["HetISq"] / 100})
    if scheme == "stderr":
        res["beta"], res["se"] = m["Effect"], m["StdErr"]
    else:
        res["z"], res["n"] = m["Zscore"], m["Weight"]
    for src, dst in (("Freq1", "eaf"), ("MinFreq", "eaf_min"), ("MaxFreq", "eaf_max")):
        if src in m:
            res[dst] = m[src]
    res = res[res["n_studies"] >= p.get("min_studies", 2)]
    write_results(res, a.out)
    write_log(a.log, "gwashub/metal", "pass", {"scheme": scheme},
              [f"METAL ({scheme} scheme) meta-analysis of {len(res):,} variants across {len(specs)} datasets."],
              studies=infos, files=files)
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
