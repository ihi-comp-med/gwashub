"""LD score regression (GWASHub default step) with the authors' software: bulik/ldsc (Bulik-Sullivan et al. 2015),
pinned in the gwashub/ldsc image and run unchanged in its Python 2.7 environment. GWASHub format in and out
(the working file isn't changed).

    munge_sumstats.py --merge-alleles w_hm3.snplist   (LDSC's own defaults: INFO >= 0.9, MAF >= 0.01)
    ldsc.py --h2 ... --ref-ld UKBB.<POP> --w-ld UKBB.<POP>

An intercept near 1 means the inflation is polygenic signal; well above 1 points to confounding or stratification.
Reference: Pan-UK Biobank LD scores in LDSC format for the dataset's ancestry (UKBB.<POP>.l2.ldscore.gz and
UKBB.<POP>.l2.M_5_50; POP = AFR, AMR, CSA, EAS, EUR, MID, with GWASHub's SAS as CSA) and the HapMap 3 SNP list
w_hm3.snplist. When it can't run (reference not uploaded, an ancestry with no reference, no rsIDs) the step passes
and says why, so it never holds a dataset up; a high intercept gives a warning.

Contract
--------
The parameters, their labels, limits and help text are in step.yaml in this folder; the LD-score files are listed
there as optional resources. The contract every QC step follows is in CONTRIBUTING.md, section 2.

    python ldsc.py --input working.tsv --resources DIR --params params.json --log step_log.json

Inputs
  --input       GWASHub format on GRCh38 (after gwashub/harmonise_liftover). Read only. Needs rsid (to match
                HapMap 3 and the LD scores) and n.
  --resources   w_hm3.snplist and UKBB.<POP>.l2.ldscore.gz / .l2.M_5_50 for the dataset's ancestry (all optional).
  --params      JSON. Read here: ancestry and, under "step", warn_intercept, info_min, maf_min, n_blocks (DEFAULTS).
  Environment   LDSC_PYTHON, LDSC_DIR: LDSC's Python 2.7 and its checkout (set in the gwashub/ldsc image).
                GWASHUB_ARTIFACT_DIR: where the artifacts go.

Outputs
  --input       Unchanged.
  --log         {"step": "ldsc", "verdict", "metrics", "messages", "artifacts"}. Metrics, as ldsc.py prints them:
                h2, h2_se (observed scale), lambda_gc, mean_chisq, intercept, intercept_se, ratio, ratio_se, n_snps.
  Artifacts     munge.log, ldsc.log   the two programs' own logs

Verdict
  warn   intercept above warn_intercept; LDSC missing from the image; munge or ldsc.py stopped or gave no intercept
  pass   otherwise, including when it can't run (no reference for the ancestry, no rsIDs, no N). Never fails.

Notes for whoever maintains this
--------------------------------
- The intercept estimates the inflation not due to polygenicity: about 1 + N a, where a measures confounding
  (population stratification, cryptic relatedness). The ratio, (intercept - 1) / (mean chi^2 - 1), is the share of the
  inflation it attributes to that. With mean chi^2 close to 1 (small or underpowered cohorts) both are poorly
  estimated: look at intercept_se before reading anything into a value of 1.1.
- An intercept well below 1 usually means the cohort already applied genomic control to its P-values, which
  deflates the statistics. stats_summary's low lambda_GC warning catches the same thing.
- h2 is on the observed scale. For a case/control trait it depends on the case fraction of the sample and is not
  comparable between cohorts; converting to the liability scale needs the sample and population prevalence, which
  this step isn't given.
- munge_sumstats.py applies its own filters on top of info_min and maf_min, notably removing SNPs whose N is below
  the 90th percentile divided by 1.5, so n_snps can be well below the HapMap 3 count in files where N varies a lot.
- The LD scores are Pan-UK Biobank's, estimated in UK Biobank participants of each ancestry. They suit large
  European cohorts best; for the other groups they are an approximation of the cohort's own LD.
- A1 is the effect allele (ea) and BETA its effect, so the signed statistic matches LDSC's convention.
"""
import gzip
import os
import re
import subprocess
import tempfile

from _lib.common import load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"warn_intercept": 1.1, "info_min": 0.9, "maf_min": 0.01, "n_blocks": 200}
POPULATION = {"EUR": "EUR", "AFR": "AFR", "AMR": "AMR", "EAS": "EAS", "SAS": "CSA", "CSA": "CSA", "MID": "MID"}
SNPLIST = "w_hm3.snplist"


def ld_files(pop):
    return [f"UKBB.{pop}.l2.ldscore.gz", f"UKBB.{pop}.l2.M_5_50"]


def run_streamed(cmd, cwd):
    """Run an LDSC program, echoing its output to this step's console line by line as it comes (the website shows a
    running step's last lines), and return (exit code, all of its output)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    seen = []
    for line in proc.stdout:
        seen.append(line)
        print(line, end="", flush=True)
    return proc.wait(), "".join(seen)


def number(pattern, text, group=1):
    m = re.search(pattern, text)
    return float(m.group(group)) if m else None


def parse_h2_log(text):
    """The numbers ldsc.py --h2 prints."""
    out = {"h2": number(r"Total Observed scale h2: (-?[\d.e-]+) \(", text),
           "h2_se": number(r"Total Observed scale h2: -?[\d.e-]+ \(([\d.e-]+)\)", text),
           "lambda_gc": number(r"Lambda GC: ([\d.e-]+)", text),
           "mean_chisq": number(r"Mean Chi\^2: ([\d.e-]+)", text),
           "intercept": number(r"Intercept: (-?[\d.e-]+) \(", text),
           "intercept_se": number(r"Intercept: -?[\d.e-]+ \(([\d.e-]+)\)", text),
           "ratio": number(r"Ratio: (-?[\d.e-]+) \(", text),
           "ratio_se": number(r"Ratio: -?[\d.e-]+ \(([\d.e-]+)\)", text),
           "n_snps": number(r"After merging with regression SNP LD, (\d+) SNPs remain", text)}
    if out["n_snps"] is not None:
        out["n_snps"] = int(out["n_snps"])
    return out


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    art = os.environ.get("GWASHUB_ARTIFACT_DIR") or tempfile.mkdtemp()
    os.makedirs(art, exist_ok=True)

    def skip(msg, metrics=None):
        write_log(args.log, "ldsc", "pass", {"ran": False, **(metrics or {})}, [msg])

    ancestry = str(params.get("ancestry") or "").upper()
    pop = POPULATION.get(ancestry)
    if not pop:
        return skip(f"No LD-score reference for ancestry {ancestry or 'not given'}; LD score regression not run.")
    needed = [*ld_files(pop), SNPLIST]
    missing = [k for k in needed if not os.path.exists(os.path.join(args.resources, k))]
    if missing:
        return skip(f"LD-score reference not uploaded yet ({', '.join(missing)}); LD score regression not run.")
    python2, ldsc_dir = os.environ.get("LDSC_PYTHON"), os.environ.get("LDSC_DIR")
    if not (python2 and ldsc_dir):
        write_log(args.log, "ldsc", "warn", {"ran": False}, ["LDSC isn't installed here (LDSC_PYTHON, LDSC_DIR): this step runs in the gwashub/ldsc image."])
        return

    df = read_working(args.input)
    if "rsid" not in df or df["rsid"].notna().sum() == 0:
        return skip("No rsIDs to match the LD-score reference; LD score regression not run.")
    if "n" not in df or df["n"].notna().sum() == 0:
        return skip("No sample size (N); LD score regression not run.")
    tmp = tempfile.mkdtemp(prefix="ldsc_")
    sumstats = os.path.join(tmp, "sumstats.tsv.gz")
    cols = {"rsid": "SNP", "ea": "A1", "oa": "A2", "beta": "BETA", "p": "P", "n": "N", "info": "INFO", "eaf": "FRQ"}
    out = df[[c for c in cols if c in df]].rename(columns=cols).dropna(subset=["SNP", "N"])
    with gzip.open(sumstats, "wt") as f:
        out.to_csv(f, sep="\t", index=False, na_rep="NA")

    munged, h2 = os.path.join(tmp, "munged"), os.path.join(tmp, "h2")
    munge_cmd = [python2, os.path.join(ldsc_dir, "munge_sumstats.py"), "--sumstats", sumstats, "--out", munged,
                 "--merge-alleles", os.path.join(args.resources, SNPLIST), "--chunksize", "500000",
                 "--info-min", str(opts["info_min"]), "--maf-min", str(opts["maf_min"])]
    prefix = os.path.join(args.resources, f"UKBB.{pop}")
    h2_cmd = [python2, os.path.join(ldsc_dir, "ldsc.py"), "--h2", munged + ".sumstats.gz", "--ref-ld", prefix, "--w-ld", prefix,
              "--n-blocks", str(int(opts["n_blocks"])), "--out", h2]
    artifacts = []
    for name, cmd, log_file in (("munge", munge_cmd, munged + ".log"), ("ldsc", h2_cmd, h2 + ".log")):
        print(f"== {'munge_sumstats.py' if name == 'munge' else 'ldsc.py --h2'}", flush=True)
        code, output = run_streamed(cmd, ldsc_dir)
        text = open(log_file).read() if os.path.exists(log_file) else output
        with open(os.path.join(art, f"{name}.log"), "w") as f:
            f.write(text + (output if code and output not in text else ""))
        artifacts.append({"type": "text", "path": f"{name}.log", "label": f"{'munge_sumstats.py' if name == 'munge' else 'ldsc.py --h2'} log"})
        if code != 0:
            last = output.strip().splitlines()[-3:]
            write_log(args.log, "ldsc", "warn", {"ran": False, "population": pop},
                      [f"{name} did not finish: " + " ".join(last)], artifacts)
            return

    m = parse_h2_log(open(h2 + ".log").read())
    if m["intercept"] is None:
        write_log(args.log, "ldsc", "warn", {"ran": False, "population": pop}, ["ldsc.py ran but reported no intercept; see its log."], artifacts)
        return
    metrics = {"ran": True, "population": pop, **m}
    msg = (f"LDSC intercept {m['intercept']:.3f} (SE {m['intercept_se']:.3f}) from {m['n_snps']:,} HapMap 3 SNPs "
           f"(Pan-UKB {pop} LD scores); λGC {m['lambda_gc']}, mean χ² {m['mean_chisq']}.")
    verdict = "pass"
    messages = [msg]
    if m["intercept"] > opts["warn_intercept"]:
        verdict = "warn"
        messages.append(f"Intercept above {opts['warn_intercept']}: inflation beyond polygenic signal (confounding or stratification).")
    write_log(args.log, "ldsc", verdict, metrics, messages, artifacts)


if __name__ == "__main__":
    main()
