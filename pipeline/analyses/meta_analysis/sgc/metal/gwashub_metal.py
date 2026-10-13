"""GWASHub code around the Skin Genetics Consortium's METAL script, METAL_example_single.metal (copied unchanged from
github.com/Katie-Watts/SGC; see SOURCE.md). The script holds SGC's METAL settings: SCHEME STDERR (fixed-effect,
inverse-variance weighted), AVERAGEFREQ and MINMAXFREQ ON, cases and controls summed across datasets (CUSTOMVARIABLE),
ANALYZE HETEROGENEITY. Nothing here changes them: as SGC's README says to do for each run, only its PROCESS lines (one
per dataset) and its OUTFILE line are replaced.

Contract
--------
The parameters are in analysis.yaml in this folder. The contract every method follows is in CONTRIBUTING.md,
section 6, and at the top of _metalib/common.py.

    python gwashub_metal.py --inputs inputs.json --params params.json --out OUT_DIR --log log.json

What it does
  1. Before: writes each dataset as qc_munge.R writes its output (SGC_COLUMNS: SNP_ID = chromosome:position:alleles in
     alphabetical order, Effect_allele, Beta, SE, P-value, Effect_AF, N_CAS, N_CON, ...), the columns the script reads.
  2. Writes the script with the PROCESS and OUTFILE lines for this run, every other line as SGC wrote it, and runs
     metal <script> (METAL 2020-05-05, the gwashub/metal image).
  3. After: maps METAL's table to the standard results columns (_metalib/common.py), with the summed cases and controls.

Outputs
  OUT_DIR/meta_results.tsv.gz   chr pos rsid ea oa beta se p n_studies direction q het_p i2 eaf eaf_min eaf_max
                                ncase ncontrol n (n = ncase + ncontrol). Effects and SEs are printed by METAL with its
                                default precision (4 significant digits), as SGC's script leaves it.
  OUT_DIR/sgc_metal_script.txt, metal_console.txt, metal_info.txt   for download
"""
import os
import re
import shutil

import pandas as pd

from _metalib.common import load_study, marker_id, parse_args, run_tool, say, split_marker, tool_failed, write_log, write_results

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "METAL_example_single.metal")


def sgc_table(df, spec):
    """A dataset as qc_munge.R writes its output (its standardise())."""
    cases = df["ncase"] if "ncase" in df else spec.get("n_cases")
    controls = df["ncontrol"] if "ncontrol" in df else spec.get("n_controls")
    return pd.DataFrame({
        "SNP_ID": marker_id(df), "RSID": df["rsid"] if "rsid" in df else "NA", "Chromosome": df["chr"], "Position": df["pos"],
        "Effect_allele": df["ea"], "Non-effect_allele": df["oa"], "Beta": df["beta"], "SE": df["se"], "P-value": df["p"],
        "Effect_AF": df["eaf"] if "eaf" in df else "NA", "Imp_Quality": df["info"] if "info" in df else "NA",
        "N_CAS": cases if cases is not None else "NA", "N_CON": controls if controls is not None else "NA"})


def run_script(template, inputs, outfile):
    """SGC's script with this run's PROCESS lines (where its first PROCESS line was) and OUTFILE; the rest as written."""
    out, placed = [], False
    for line in template.splitlines():
        word = line.split()[0].upper() if line.split() else ""
        if word == "PROCESS":
            if not placed:
                out += [f"PROCESS   {path}" for path in inputs]
                placed = True
            continue
        out.append(f"OUTFILE   {outfile} .tbl" if word == "OUTFILE" else line)
    if not placed:
        raise SystemExit("SGC's METAL script has no PROCESS line to replace")
    return "\n".join(out) + "\n"


def main():
    a, specs, p = parse_args()
    work = os.path.join(a.out, "metal_work")
    os.makedirs(work, exist_ok=True)
    infos, rsids, paths = [], {}, []
    for k, s in enumerate(specs, 1):
        say(f"Reading dataset {k} of {len(specs)}: {s.get('label', s['id'])}")
        df, info = load_study(s)
        t = sgc_table(df, s).drop_duplicates("SNP_ID")
        if "rsid" in df:
            rsids.update(dict(zip(t["SNP_ID"], t["RSID"])))
        path = os.path.join(work, f"dataset_{s['id']}.tsv")
        t.to_csv(path, sep="\t", index=False, na_rep="NA")
        paths.append(path)
        infos.append(info)
        del df, t
    # METAL runs in the working folder, so the script names the files as SGC's example does (dataset_1.tsv, ...).
    with open(SCRIPT) as f:
        script = run_script(f.read(), [os.path.basename(x) for x in paths], "meta_")
    with open(os.path.join(a.out, "sgc_metal_script.txt"), "w") as f:
        f.write(script)
    with open(os.path.join(work, "sgc.metal"), "w") as f:
        f.write(script)
    say(f"Running METAL with SGC's script on {len(specs)} datasets")
    os.chdir(work)
    code, output = run_tool(["metal", "sgc.metal"])
    os.chdir(a.out)
    with open(os.path.join(a.out, "metal_console.txt"), "w") as f:
        f.write(output)
    files = [{"path": "sgc_metal_script.txt", "label": "METAL script (SGC's, with this run's datasets)"},
             {"path": "metal_console.txt", "label": "METAL console output"}]
    tbl = os.path.join(work, "meta_1.tbl")
    if os.path.exists(tbl + ".info"):
        shutil.move(tbl + ".info", os.path.join(a.out, "metal_info.txt"))
        files.append({"path": "metal_info.txt", "label": "METAL run information"})
    if code != 0 or not os.path.exists(tbl):
        tool_failed("METAL", code, output)

    say("Reading METAL's results")
    m = pd.read_csv(tbl, sep="\t")
    chrom, pos = split_marker(m["MarkerName"])
    res = pd.DataFrame({"chr": chrom, "pos": pos, "rsid": m["MarkerName"].map(rsids), "ea": m["Allele1"].str.upper(),
                        "oa": m["Allele2"].str.upper(), "beta": m["Effect"], "se": m["StdErr"], "p": m["P-value"],
                        "direction": m["Direction"], "n_studies": m["HetDf"] + 1, "q": m["HetChiSq"], "het_p": m["HetPVal"],
                        "i2": m["HetISq"] / 100})
    for src, dst in (("Freq1", "eaf"), ("MinFreq", "eaf_min"), ("MaxFreq", "eaf_max"), ("Ncases", "ncase"), ("Ncontrols", "ncontrol")):
        if src in m:
            res[dst] = m[src]
    if {"ncase", "ncontrol"} <= set(res):
        res["n"] = res["ncase"] + res["ncontrol"]
    res = res[res["n_studies"] >= p.get("min_studies", 1)]
    say(f"Writing {len(res):,} variants")
    write_results(res, a.out)
    custom = [ln.split()[1] for ln in script.splitlines() if re.match(r"\s*CUSTOMVARIABLE\s", ln)]
    write_log(a.log, "sgc/metal", "pass", {"scheme": "stderr", "datasets": len(specs)},
              [f"METAL with SGC's script (METAL_example_single.metal): {len(res):,} variants across {len(specs)} datasets"
               + (f"; {', '.join(custom)} summed across datasets." if custom else ".")],
              studies=infos, files=files)
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
