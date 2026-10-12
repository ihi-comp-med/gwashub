"""EAF check (GWASHub default step): compares each variant's effect-allele frequency with 1000 Genomes for the
dataset's ancestry. GWASHub format (GRCh38, after gwashub/harmonise_liftover) in and out; nothing is removed unless
remove_large_diff is on.

As the allele-frequency section of the HERMES 3 DCM QC report (hermes3_dcm scripts/gwas_qc_report.R, commit
ff35109): variants are matched on chromosome, GRCh38 position and alleles (either order; the reference frequency is
flipped when EA = REF), and the share differing by more than 0.2 is reported. Strongly negative correlation means
EA and OA were swapped in the upload; a high share of large differences can mean a wrong ancestry or strand problems.
Warns (never fails) when more than 5% of matched variants differ by more than 0.2. With remove_large_diff, those
variants are removed (and written to removed_rows.tsv.gz); the warning is still judged on the share before removal.
Variants not found in 1000 Genomes are always kept.

Three common mistakes are named when seen: a wrong ancestry (the frequencies fit another 1000 Genomes population
clearly better than the declared one), an EAF column that is really the minor allele frequency (frequencies agree with
the reference once both are folded to MAF, but not as given), and strand problems in A/T and C/G SNPs (which
harmonisation can't see from the alleles: those SNPs then disagree with the reference far more often than the others).

Reference: all_afreq_b38.tsv.gz, tab-separated with id, chr, pos_b38, ref, alt and <pop>_afreq (ALT allele
frequency) for afr, amr, eas, eur, sas (resources/build/kg1000_b38.sh).

Contract
--------
The parameters, their labels, limits and help text are in step.yaml in this folder. The contract every QC step
follows is in CONTRIBUTING.md, section 2.

    python eaf_check.py --input working.tsv --resources DIR --params params.json --log step_log.json

Inputs
  --input       GWASHub format on GRCh38 with ea = ALT (after gwashub/harmonise_liftover). Read only, unless
                remove_large_diff is on.
  --resources   all_afreq_b38.tsv.gz (optional: without it the step passes and says it didn't run).
  --params      JSON. Read here: ancestry (EUR, AFR, AMR, EAS or SAS; anything else and the step doesn't run) and,
                under "step", diff_threshold, warn_diff_fraction, remove_large_diff (DEFAULTS).
  Environment   GWASHUB_ARTIFACT_DIR: where the artifacts go.

Outputs
  --input       Unchanged, or without the variants with a large difference when remove_large_diff is on.
  --log         {"step": "eaf_check", "verdict", "metrics", "messages", "artifacts"}. Metrics: ran, population,
                matched, matched_fraction, eaf_correlation, large_diff_fraction, mean_abs_diff, the A/T-C/G and
                as-MAF fractions, ancestry_best_fit and mean_abs_diff_<pop> for every population.
  Artifacts     eaf_vs_reference.png   hexbin of dataset EAF against the reference frequency of the same allele
                removed_rows.tsv.gz    only with remove_large_diff: the removed variants, ref_eaf, eaf_diff, reason

Verdict
  warn   more than warn_diff_fraction of matched variants differ by more than diff_threshold; A/T and C/G SNPs
         differ far more often than the others; or another population fits clearly better
  pass   otherwise, and whenever the check can't run (no population, no reference, no EAF, under 100 matches).
         The step never fails.

Notes for whoever maintains this
--------------------------------
- 1000 Genomes has about 500 to 900 people per super-population, so a reference frequency carries sampling noise
  of sqrt(f(1-f) / 2n), about 0.015 at f = 0.5 with 600 people. Differences of 0.2 are far outside that; small, consistent
  differences are not. A cohort from a sub-population (Finns, Ashkenazi, an admixed sample declared as one
  ancestry) will have more large differences than a mixed European cohort without anything being wrong.
- The super-populations are coarse. AMR in particular is an admixed group, and an admixed cohort can sit between two
  populations, so the ancestry warning only fires when another population fits at least 40% better (0.6 x).
- The swapped-allele and MAF-not-EAF diagnoses are judged on the SNPs that aren't A/T or C/G, because for those a
  strand flip and an allele swap both turn f into 1 - f and can't be told apart from a frequency.
- Variants are matched on chr, pos and both alleles, so a multi-allelic site in 1000 Genomes matches only the record
  with the same pair of alleles.
"""
import os

import numpy as np
import pandas as pd

from _lib.common import load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"diff_threshold": 0.2, "warn_diff_fraction": 0.05, "remove_large_diff": False}
AMBIGUOUS = {("A", "T"), ("T", "A"), ("C", "G"), ("G", "C")}
KEYS = ["chr", "pos", "ea", "oa"]
REFERENCE = "all_afreq_b38.tsv.gz"
POPULATION = {"EUR": "eur", "AFR": "afr", "AMR": "amr", "EAS": "eas", "SAS": "sas"}
CHUNK = 2_000_000


def say(msg):
    """A progress line on the step's console (the website shows a running step's last lines): counts only."""
    print(msg, flush=True)


def reference_frequencies(path, gwas, col):
    """The reference rows at the dataset's positions, read in chunks so the whole file is never in memory: afreq for
    the dataset's population (col), and every population's <pop>_afreq (for the ancestry fit)."""
    keys = gwas[["chr", "pos"]].drop_duplicates()
    header = pd.read_csv(path, sep="\t", comment="#", nrows=0).columns
    pops = [f"{p}_afreq" for p in POPULATION.values() if f"{p}_afreq" in header]
    found, rows, n_found = [], 0, 0
    for chunk in pd.read_csv(path, sep="\t", comment="#", usecols=["chr", "pos_b38", "ref", "alt", *pops], chunksize=CHUNK,
                             dtype={"chr": str, "ref": str, "alt": str}):
        chunk = chunk.rename(columns={"pos_b38": "pos"}).assign(afreq=chunk[col]).dropna(subset=["afreq"])
        chunk["chr"] = chunk["chr"].str.replace(r"^chr", "", regex=True)
        found.append(chunk.merge(keys, on=["chr", "pos"]))
        rows, n_found = rows + len(chunk), n_found + len(found[-1])
        say(f"  1000 Genomes rows read: {rows:,}; at the dataset's positions so far: {n_found:,}")
    return pd.concat(found, ignore_index=True) if found else pd.DataFrame(columns=["chr", "pos", "ref", "alt", "afreq", *pops])


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
    say(f"Reading the dataset (ancestry {pop.upper()})")
    gwas = read_working(args.input)
    say(f"  {len(gwas):,} variants")
    if "eaf" not in gwas or gwas["eaf"].notna().sum() == 0:
        write_log(args.log, "eaf_check", "pass", {"ran": False}, ["The dataset has no EAF; EAF check not run."])
        return

    g = gwas.loc[gwas["eaf"].notna(), ["chr", "pos", "ea", "oa", "eaf"]]
    say(f"Reading 1000 Genomes frequencies ({REFERENCE}) in chunks of {CHUNK:,} rows")
    ref = reference_frequencies(ref_path, g, f"{pop}_afreq")
    same = g.merge(ref, left_on=["chr", "pos", "oa", "ea"], right_on=["chr", "pos", "ref", "alt"])
    same["ref_eaf"] = same["afreq"]
    flipped = g.merge(ref, left_on=["chr", "pos", "ea", "oa"], right_on=["chr", "pos", "ref", "alt"])
    flipped["ref_eaf"] = 1 - flipped["afreq"]
    pops = [c for c in ref.columns if c.endswith("_afreq")]
    flipped[pops] = 1 - flipped[pops]  # every population's frequency of the effect allele
    m = pd.concat([same, flipped], ignore_index=True).drop_duplicates(["chr", "pos", "ea", "oa"])

    n = len(m)
    say(f"Matched {n:,} of {len(g):,} variants to 1000 Genomes by position and alleles")
    metrics = {"ran": True, "population": pop.upper(), "variants_with_eaf": len(g), "matched": n,
               "matched_fraction": n / len(g) if len(g) else 0}
    if n < 100:
        write_log(args.log, "eaf_check", "pass", {**metrics, "ran": False},
                  [f"Only {n} variants matched 1000 Genomes {pop.upper()}; EAF check not run."])
        return
    diff = (m["eaf"] - m["ref_eaf"]).abs()
    large = diff > opts["diff_threshold"]
    frac = float(large.mean())
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
    say("Checking strand-ambiguous SNPs, MAF-for-EAF and ancestry fit")
    # Strand problems hide in A/T and C/G SNPs: they then disagree with the reference far more often than the rest.
    ambiguous = np.array([(a, b) in AMBIGUOUS for a, b in zip(m["ea"], m["oa"])], dtype=bool)
    if ambiguous.sum() >= 100 and (~ambiguous).sum() >= 100:
        amb, other = float(large[ambiguous].mean()), float(large[~ambiguous].mean())
        metrics.update(ambiguous_large_diff_fraction=amb, other_large_diff_fraction=other)
        if amb > opts["warn_diff_fraction"] and amb > 2 * other:
            verdict = "warn"
            messages.append(f"Strand-ambiguous SNPs (A/T, C/G) differ from the reference far more often than the others "
                            f"({100 * amb:.2f}% vs {100 * other:.2f}%): some are probably on the other strand. The filter step "
                            "can remove them (Strand-ambiguous SNPs).")
    # EAF that is really MAF: folding both to MAF removes the differences. Judged on the other SNPs, since a strand
    # flip of an A/T SNP also turns f into 1 - f; and not when 1 - EAF fits (swapped allele columns, also hidden by folding).
    rest = ~ambiguous
    if rest.sum() >= 100:
        maf_diff = (np.minimum(m["eaf"], 1 - m["eaf"]) - np.minimum(m["ref_eaf"], 1 - m["ref_eaf"])).abs()[rest]
        swap_frac = float(((1 - m["eaf"] - m["ref_eaf"]).abs()[rest] > opts["diff_threshold"]).mean())
        maf_frac, rest_frac = float((maf_diff > opts["diff_threshold"]).mean()), float(large[rest].mean())
        metrics["large_diff_fraction_as_maf"] = maf_frac
        if (rest_frac > opts["warn_diff_fraction"] and swap_frac > opts["warn_diff_fraction"]
                and maf_frac < min(opts["warn_diff_fraction"], rest_frac / 3)):
            messages.append(f"As minor allele frequencies only {100 * maf_frac:.2f}% differ: the EAF column looks like the minor "
                            "allele frequency, not the frequency of the effect allele. Upload the effect allele's frequency.")
    # Wrong ancestry declared: the dataset's frequencies sit clearly closer to another population's.
    fit = {c.split("_")[0].upper(): float((m["eaf"] - m[c]).abs().mean()) for c in pops if m[c].notna().sum() >= 100}
    if fit:
        best = min(fit, key=fit.get)
        metrics.update(ancestry_best_fit=best, **{f"mean_abs_diff_{k.lower()}": round(v, 4) for k, v in fit.items()})
        if best != pop.upper() and fit[best] < 0.6 * fit[pop.upper()] and fit[pop.upper()] > 0.03:
            verdict = "warn"
            messages.append(f"Frequencies fit 1000 Genomes {best} better than the declared {pop.upper()} (mean difference "
                            f"{fit[best]:.3f} vs {fit[pop.upper()]:.3f}): check the dataset's ancestry.")
    artifacts = []
    if opts["remove_large_diff"] and large.any():
        drop = m.loc[large, KEYS].assign(ref_eaf=m.loc[large, "ref_eaf"], eaf_diff=diff[large])
        flagged = gwas.merge(drop, on=KEYS, how="left", indicator=True)["_merge"].eq("both").to_numpy()
        removed = gwas[flagged].merge(drop, on=KEYS, how="left")
        removed["reason"] = f"EAF differs from 1000 Genomes {pop.upper()} by more than {opts['diff_threshold']}"
        kept = gwas[~flagged].copy()
        for col in ("n", "ncase", "ncontrol"):  # whole numbers, as the other steps write them
            if col in kept:
                kept[col] = kept[col].round().astype("Int64")
        kept.to_csv(args.input, sep="\t", index=False, na_rep="NA")
        metrics.update(rows_removed=int(flagged.sum()), rows_out=int((~flagged).sum()))
        messages.append(f"Removed {int(flagged.sum()):,} variants with a large difference.")
        if art:
            removed.to_csv(os.path.join(art, "removed_rows.tsv.gz"), sep="\t", index=False, na_rep="NA")
            artifacts.append({"type": "table", "path": "removed_rows.tsv.gz", "label": "Removed rows", "rows": len(removed),
                              "description": "Variants whose EAF differs from 1000 Genomes by more than the threshold, "
                                             "with the reference EAF and the difference."})
    if art:
        say("Drawing the EAF plot")
        plot(m, pop, os.path.join(art, "eaf_vs_reference.png"))
        artifacts.append({"type": "image", "path": "eaf_vs_reference.png", "label": "EAF vs 1000 Genomes"})
    say(f"Done: {verdict}")
    write_log(args.log, "eaf_check", verdict, metrics, messages, artifacts)


if __name__ == "__main__":
    main()
