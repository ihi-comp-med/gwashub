"""Reference check: align every variant to the reference panel so the effect allele is the panel ALT.

- Matches, allele flips and strand flips are aligned (BETA and EAF negated/complemented as needed).
- Palindromic SNPs are resolved by comparing EAF with the panel frequency; ambiguous ones dropped.
- Variants whose alleles don't match the panel are dropped.
- Two whole-file checks catch common submission mistakes:
  * most positions missing from the panel  -> wrong genome build declared
  * EAF anti-correlated with the panel     -> effect/other allele columns swapped
"""
import os

import numpy as np
import pandas as pd

from _lib.common import complement, load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {
    "panel": "ref_panel_grch38.tsv.gz",
    "palindromic_max_maf": 0.4,
    "fail_not_in_reference_fraction": 0.5,
    "fail_mismatch_fraction": 0.05,
    "warn_eaf_correlation": 0.8,
}


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    df = read_working(args.input).reset_index(drop=True)
    n0 = len(df)
    panel = pd.read_csv(os.path.join(args.resources, opts["panel"]), sep="\t", comment="#", dtype={"chr": str})

    ancestry = params.get("ancestry") or "EUR"
    af_cols = [c for c in panel.columns if c.startswith("af_")]
    panel["af"] = panel[f"af_{ancestry}"] if f"af_{ancestry}" in panel else panel[af_cols].mean(axis=1)

    df["_row"] = np.arange(n0)
    m = df.merge(panel[["chr", "pos", "rsid", "ref", "alt", "af"]].rename(columns={"rsid": "ref_rsid"}),
                 on=["chr", "pos"], how="left")
    in_ref = m["ref"].notna()
    not_in_ref = n0 - m.loc[in_ref, "_row"].nunique()
    if n0 and not_in_ref / n0 > opts["fail_not_in_reference_fraction"]:
        write_log(args.log, "reference_check", "fail", {"not_in_reference": not_in_ref, "total_rows": n0},
                  [f"{not_in_ref / n0:.0%} of positions are not in the GRCh38 reference panel. "
                   "The file is probably on a different genome build than declared (e.g. GRCh37 declared as GRCh38)."])
        return

    r = m[in_ref].copy()
    ea, oa, ref, alt = r["ea"], r["oa"], r["ref"], r["alt"]
    cea, coa = ea.map(complement), oa.map(complement)
    snp = (ea.str.len() == 1) & (oa.str.len() == 1)
    pal = snp & (cea == oa)
    match = (ea == alt) & (oa == ref)
    flip = (ea == ref) & (oa == alt)
    strand = snp & ~pal & (cea == alt) & (coa == ref)
    strand_flip = snp & ~pal & (cea == ref) & (coa == alt)

    effect_is_alt = pd.Series(np.nan, index=r.index, dtype=object)
    effect_is_alt[(match | strand) & ~pal] = True
    effect_is_alt[(flip | strand_flip) & ~pal] = False

    # Palindromic: same letters either way round; decide with EAF where the panel MAF is informative.
    pal_alleles_ok = pal & (match | flip)
    maf = np.minimum(r["af"], 1 - r["af"])
    has_eaf = r["eaf"].notna() if "eaf" in r else pd.Series(False, index=r.index)
    resolvable = pal_alleles_ok & has_eaf & (maf < opts["palindromic_max_maf"])
    if resolvable.any():
        eaf_if_alt = np.where(ea == alt, r["eaf"], 1 - r["eaf"])  # EAF of ALT if the letters are taken at face value
        closer_alt = np.abs(eaf_if_alt - r["af"]) <= np.abs((1 - eaf_if_alt) - r["af"])
        letters_alt = (ea == alt)
        decided = np.where(closer_alt, letters_alt, ~letters_alt)
        effect_is_alt[resolvable] = decided[resolvable.to_numpy()]
    pal_dropped = int((pal & ~resolvable).sum())

    aligned = r[effect_is_alt.notna()].copy()
    is_alt = effect_is_alt[aligned.index].astype(bool)
    aligned["beta"] = np.where(is_alt, aligned["beta"], -aligned["beta"])
    if "eaf" in aligned:
        aligned["eaf"] = np.where(is_alt, aligned["eaf"], 1 - aligned["eaf"])
    aligned["ea"], aligned["oa"] = aligned["alt"], aligned["ref"]
    if "rsid" not in aligned or aligned["rsid"].isna().all():
        aligned["rsid"] = aligned["ref_rsid"]
    aligned = aligned.drop_duplicates("_row")

    mismatched = int(((~pal) & effect_is_alt.isna()).sum())
    rows_with_match = set(aligned["_row"])
    mismatched = len(set(r["_row"]) - rows_with_match) - pal_dropped
    mismatched = max(mismatched, 0)

    keep_not_in_ref = df[~df["_row"].isin(m.loc[in_ref, "_row"])]
    out = pd.concat([aligned[df.columns], keep_not_in_ref], ignore_index=True)
    out = out.sort_values("_row").drop(columns="_row")

    metrics = {
        "matched": int((match & ~pal).sum()), "flipped": int((flip & ~pal).sum()),
        "strand_flipped": int((strand | strand_flip).sum()), "palindromic_resolved": int(resolvable.sum()),
        "palindromic_dropped": pal_dropped, "mismatched": mismatched, "not_in_reference": not_in_ref,
        "rows_out": len(out),
    }
    messages, verdict = [], "pass"

    if "eaf" in aligned and aligned["eaf"].notna().sum() > 100:
        nonpal = aligned[~aligned["ea"].map(complement).eq(aligned["oa"])]
        corr = float(np.corrcoef(nonpal["eaf"], nonpal["af"])[0, 1])
        metrics["eaf_correlation"] = round(corr, 3)
        if corr < 0:
            write_log(args.log, "reference_check", "fail", metrics,
                      [f"Effect-allele frequencies are anti-correlated with the reference (r = {corr:.2f}): "
                       "the effect and other allele columns look swapped. Fix the column mapping and re-run."])
            return
        if corr < opts["warn_eaf_correlation"]:
            verdict = "warn"
            messages.append(f"EAF correlates only r = {corr:.2f} with the {ancestry} reference: check the declared ancestry.")

    frac = mismatched / max(n0, 1)
    if frac > opts["fail_mismatch_fraction"]:
        verdict = "fail"
        messages.append(f"{frac:.1%} of variants have alleles that don't match the reference.")
    messages.insert(0, f"Aligned {len(aligned):,} variants to the reference (effect allele = ALT); "
                       f"{metrics['flipped'] + metrics['strand_flipped']:,} flipped, {pal_dropped:,} ambiguous palindromic dropped, "
                       f"{mismatched:,} mismatched dropped, {not_in_ref:,} not in panel kept.")
    write_working(out, args.input)
    write_log(args.log, "reference_check", verdict, metrics, messages)


if __name__ == "__main__":
    main()
