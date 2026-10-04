"""LD score regression intercept.

Regresses each variant's χ² (from BETA/SE) on its LD score: E[χ²] = N·h²·ℓ/M + intercept. An intercept
near 1 means the inflation is polygenic signal; well above 1 points to confounding or stratification.

Reference: ldscores_<POP>.tsv(.gz) in --resources, tab-separated with columns chr, pos (GRCh38) and l2
(an optional rsid column is ignored). Populations follow Pan-UK Biobank: AFR, AMR, CSA, EAS, EUR, MID;
GWASHub's SAS maps to CSA. When it can't run (no matching reference uploaded yet, a multi-ancestry dataset,
too few overlapping variants, no sample size) the step *passes* with a message saying why, so it never
holds a dataset up; only a high intercept gives a warning.

Weights follow the LDSC software: 1 / (ℓ · 2·E[χ²]²), refined twice; the intercept's standard error is a
block jackknife over contiguous blocks of variants.
"""
import os

import numpy as np
import pandas as pd

from _lib.common import load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"warn_intercept": 1.1, "min_snps": 5000, "jackknife_blocks": 200}
POPULATION = {"EUR": "EUR", "AFR": "AFR", "EAS": "EAS", "SAS": "CSA", "AMR": "AMR", "CSA": "CSA", "MID": "MID"}


def fit(chi2, l2, n_bar, m):
    """Weighted least squares of χ² on LD score, with the LDSC heteroskedasticity weights. Returns (intercept, slope)."""
    l2c = np.maximum(l2, 1.0)
    w = 1 / l2c
    for _ in range(3):
        x = np.column_stack([np.ones_like(l2), l2])
        sw = np.sqrt(w)
        coef, *_ = np.linalg.lstsq(x * sw[:, None], chi2 * sw, rcond=None)
        intercept, slope = coef
        h2 = np.clip(slope * m / n_bar, 0, 1)
        pred = np.maximum(intercept + n_bar * h2 * l2c / m, 1.0)
        w = 1 / (l2c * 2 * pred ** 2)
    return float(intercept), float(slope)


def jackknife_se(chi2, l2, n_bar, m, blocks):
    idx = np.array_split(np.arange(len(chi2)), blocks)
    est = []
    for b in idx:
        keep = np.ones(len(chi2), bool)
        keep[b] = False
        est.append(fit(chi2[keep], l2[keep], n_bar, m)[0])
    est = np.array(est)
    k = len(est)
    return float(np.sqrt((k - 1) / k * ((est - est.mean()) ** 2).sum()))


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    pop = POPULATION.get(str(params.get("ancestry", "")).upper())
    ref = os.path.join(args.resources, f"ldscores_{pop}.tsv.gz") if pop else None
    if not pop:
        write_log(args.log, "ldsc", "pass", {"skipped": 1}, [f"Not run: no LD-score reference for ancestry "
                                                             f"'{params.get('ancestry')}' (a multi-ancestry dataset)."])
        return
    if not os.path.exists(ref):
        write_log(args.log, "ldsc", "pass", {"skipped": 1}, [f"Not run: the LD-score reference for {pop} "
                                                             f"(ldscores_{pop}.tsv.gz) isn't uploaded yet."])
        return

    df = read_working(args.input)
    ld = pd.read_csv(ref, sep="\t", comment="#", dtype={"chr": str}, usecols=lambda c: c in ("chr", "pos", "l2"))
    m = len(ld)
    df["chr"] = df["chr"].astype(str).str.removeprefix("chr")
    merged = df.merge(ld, on=["chr", "pos"], how="inner")
    if "n" in merged.columns and pd.to_numeric(merged["n"], errors="coerce").notna().any():
        n = pd.to_numeric(merged["n"], errors="coerce").fillna(pd.to_numeric(merged["n"], errors="coerce").median()).to_numpy()
    else:
        n = np.full(len(merged), float((params.get("n_cases") or 0) + (params.get("n_controls") or 0)))
    chi2 = ((merged["beta"] / merged["se"]) ** 2).to_numpy(dtype=float)
    l2 = merged["l2"].to_numpy(dtype=float)
    ok = np.isfinite(chi2) & np.isfinite(l2) & (n > 0)
    n_bar = float(np.mean(n[ok])) if ok.any() else 0.0
    ok &= chi2 < max(80.0, 0.001 * n_bar)  # LDSC drops very large χ² (outliers dominate the fit)
    chi2, l2 = chi2[ok], l2[ok]
    if len(chi2) < opts["min_snps"] or n_bar <= 0:
        write_log(args.log, "ldsc", "pass", {"skipped": 1, "n_snps": int(len(chi2))},
                  [f"Not run: only {len(chi2):,} variants matched the {pop} LD-score reference (need {opts['min_snps']:,})."
                   if n_bar > 0 else "Not run: no sample size given."])
        return
    order = np.lexsort((merged["pos"].to_numpy()[ok], merged["chr"].astype(int, errors="ignore").to_numpy()[ok]
                        if merged["chr"].str.isdigit().all() else np.zeros(ok.sum())))
    chi2, l2 = chi2[order], l2[order]
    intercept, slope = fit(chi2, l2, n_bar, m)
    se = jackknife_se(chi2, l2, n_bar, m, min(opts["jackknife_blocks"], len(chi2) // 50 or 1))
    mean_chi2 = float(chi2.mean())
    # The ratio (share of inflation not due to polygenicity) is only meaningful for an intercept above 1.
    ratio = (intercept - 1) / (mean_chi2 - 1) if mean_chi2 > 1.02 and intercept > 1 else None
    h2 = slope * m / n_bar
    metrics = {"intercept": round(intercept, 4), "intercept_se": round(se, 4), "mean_chi2": round(mean_chi2, 4),
               "h2_observed": round(h2, 4), "n_snps": int(len(chi2)), "population": pop}
    if ratio is not None:
        metrics["ratio"] = round(ratio, 4)
    msg = f"LD score regression ({pop} reference, {len(chi2):,} variants): intercept {intercept:.3f} (SE {se:.3f}), mean χ² {mean_chi2:.3f}."
    if intercept > opts["warn_intercept"]:
        write_log(args.log, "ldsc", "warn", metrics,
                  [msg, f"The intercept is above {opts['warn_intercept']}: inflation not explained by polygenicity "
                        "(possible population stratification or cryptic relatedness)."])
    else:
        write_log(args.log, "ldsc", "pass", metrics, [msg])


if __name__ == "__main__":
    main()
