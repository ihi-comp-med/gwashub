"""EAF check (GWASHub default step): compares each variant's effect-allele frequency with 1000 Genomes for the
dataset's ancestry. GWASHub format (GRCh38, after gwashub/harmonise_liftover) in and out; nothing is removed.

As the allele-frequency section of the HERMES 3 DCM QC report (hermes3_dcm scripts/gwas_qc_report.R, commit
ff35109): variants are matched on chromosome, GRCh38 position and alleles (either order; the reference frequency is
flipped when EA = REF), and the share differing by more than 0.2 is reported. Strongly negative correlation means
EA and OA were swapped in the upload; a high share of large differences can mean a wrong ancestry or strand problems.
Warns (never fails) when more than 5% of matched variants differ by more than 0.2.

Reference: all_afreq_b38.tsv.gz, tab-separated with id, chr, pos_b38, ref, alt and <pop>_afreq (ALT allele
frequency) for afr, amr, eas, eur, sas (resources/build/kg1000_b38.sh).
"""
import os

import numpy as np
import pandas as pd

from _lib.common import load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"diff_threshold": 0.2, "warn_diff_fraction": 0.05}
REFERENCE = "all_afreq_b38.tsv.gz"
POPULATION = {"EUR": "eur", "AFR": "afr", "AMR": "amr", "EAS": "eas", "SAS": "sas"}
CHUNK = 2_000_000


def reference_frequencies(path, gwas, col):
    """The reference rows at the dataset's positions, read in chunks so the whole file is never in memory."""
    keys = gwas[["chr", "pos"]].drop_duplicates()
    found = []
    for chunk in pd.read_csv(path, sep="\t", comment="#", usecols=["chr", "pos_b38", "ref", "alt", col], chunksize=CHUNK,
                             dtype={"chr": str, "ref": str, "alt": str}):
        chunk = chunk.rename(columns={"pos_b38": "pos", col: "afreq"}).dropna(subset=["afreq"])
        chunk["chr"] = chunk["chr"].str.replace(r"^chr", "", regex=True)
        found.append(chunk.merge(keys, on=["chr", "pos"]))
    return pd.concat(found, ignore_index=True) if found else pd.DataFrame(columns=["chr", "pos", "ref", "alt", "afreq"])


def plot(m, pop, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5, 5), dpi=110)
    hb = ax.hexbin(m["ref_eaf"], m["eaf"], gridsize=60, bins="log", cmap="Blues", mincnt=1, extent=(0, 1, 0, 1))
    ax.plot([0, 1], [0, 1], color="#c0392b", lw=0.8)
    ax.set_xlabel(f"1000 Genomes {pop.upper()} frequency of the effect allele")
    ax.set_ylabel("Effect allele frequency (dataset)")
    ax.set_title("EAF vs reference")
    fig.colorbar(hb, ax=ax, label="Variants (log scale)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    art = os.environ.get("GWASHUB_ARTIFACT_DIR")
    ancestry = str(params.get("ancestry") or "").upper()
    pop = POPULATION.get(ancestry)
    ref_path = os.path.join(args.resources, REFERENCE)
    if not pop:
        write_log(args.log, "eaf_check", "pass", {"ran": False}, [f"No 1000 Genomes population for ancestry {ancestry or 'not given'}; EAF check not run."])
        return
    if not os.path.exists(ref_path):
        write_log(args.log, "eaf_check", "pass", {"ran": False}, [f"{REFERENCE} not uploaded yet; EAF check not run."])
        return
    gwas = read_working(args.input)
    if "eaf" not in gwas or gwas["eaf"].notna().sum() == 0:
        write_log(args.log, "eaf_check", "pass", {"ran": False}, ["The dataset has no EAF; EAF check not run."])
        return

    g = gwas.loc[gwas["eaf"].notna(), ["chr", "pos", "ea", "oa", "eaf"]]
    ref = reference_frequencies(ref_path, g, f"{pop}_afreq")
    same = g.merge(ref, left_on=["chr", "pos", "oa", "ea"], right_on=["chr", "pos", "ref", "alt"])
    same["ref_eaf"] = same["afreq"]
    flipped = g.merge(ref, left_on=["chr", "pos", "ea", "oa"], right_on=["chr", "pos", "ref", "alt"])
    flipped["ref_eaf"] = 1 - flipped["afreq"]
    m = pd.concat([same, flipped], ignore_index=True).drop_duplicates(["chr", "pos", "ea", "oa"])

    n = len(m)
    metrics = {"ran": True, "population": pop.upper(), "variants_with_eaf": len(g), "matched": n,
               "matched_fraction": n / len(g) if len(g) else 0}
    if n < 100:
        write_log(args.log, "eaf_check", "pass", {**metrics, "ran": False},
                  [f"Only {n} variants matched 1000 Genomes {pop.upper()}; EAF check not run."])
        return
    diff = (m["eaf"] - m["ref_eaf"]).abs()
    frac = float((diff > opts["diff_threshold"]).mean())
    r = float(np.corrcoef(m["eaf"], m["ref_eaf"])[0, 1])
    metrics.update(eaf_correlation=r, large_diff_fraction=frac, large_diff=int((diff > opts["diff_threshold"]).sum()),
                   mean_abs_diff=float(diff.mean()))
    messages = [f"{n:,} variants matched 1000 Genomes {pop.upper()}: EAF correlation {r:.3f}; "
                f"{100 * frac:.2f}% differ by more than {opts['diff_threshold']}."]
    verdict = "pass"
    if frac > opts["warn_diff_fraction"]:
        verdict = "warn"
        messages.append(f"More than {100 * opts['warn_diff_fraction']:.0f}% of variants differ from the reference by more than "
                        f"{opts['diff_threshold']}: check the plot.")
    if r < 0:
        messages.append("Frequencies are negatively correlated with the reference: effect and other allele may be swapped.")
    artifacts = []
    if art:
        plot(m, pop, os.path.join(art, "eaf_vs_reference.png"))
        artifacts.append({"type": "image", "path": "eaf_vs_reference.png", "label": "EAF vs 1000 Genomes"})
    write_log(args.log, "eaf_check", verdict, metrics, messages, artifacts)


if __name__ == "__main__":
    main()
