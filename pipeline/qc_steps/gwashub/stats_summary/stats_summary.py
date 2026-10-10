"""Summary statistics: genomic inflation (λGC, λ1000, λ at MAF ≥ 1%), Z vs P consistency, SE vs sample size, lead loci.

SE vs sample size (the "SE-N" check of Winkler et al. 2014, Nat Protoc 9:1192): with N, cases/controls and EAF known,
the SE a variant should have follows from them. For a case/control trait on the log-odds scale it is
sqrt(2 / (p q Neff)), p = EAF, q = 1 - p, Neff = 4 / (1/cases + 1/controls), so the median of observed / expected
SE (common, well-imputed variants) should be near 1. Far below: effects on the linear 0/1 scale (BOLT-LMM, linear
regression; then the ratio is about φ(1 - φ), φ the case fraction) or N understated; far above: N overstated or the SE
of the odds ratio rather than of log-odds. For a quantitative trait the same ratio is the trait's SD in the units of
BETA (about 1 for an inverse-normal transformed trait): reported, not judged."""
import os

import numpy as np
import pandas as pd
from scipy import stats

from _lib.common import artifact_dir, load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"warn_lambda": 1.1, "fail_lambda": 1.5, "low_lambda": 0.9, "gws": 5e-8, "suggestive": 1e-5, "clump_kb": 500,
            "warn_inconsistent_fraction": 0.01, "se_ratio_low": 0.4, "se_ratio_high": 2.5}


def lead_loci(df, p_thresh, window):
    hits = df[df["p"] < p_thresh].sort_values("p")
    leads = []
    for row in hits.itertuples():
        if any(l.chr == row.chr and abs(l.pos - row.pos) < window for l in leads):
            continue
        leads.append(row)
    return leads


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    df = read_working(args.input)

    chi2 = stats.chi2.isf(df["p"].clip(lower=1e-300), 1)
    lam = float(np.median(chi2) / stats.chi2.ppf(0.5, 1))
    z = df["beta"] / df["se"]
    p_from_z = 2 * stats.norm.sf(np.abs(z))
    inconsistent = int((np.abs(np.log10(np.clip(p_from_z, 1e-300, None)) - np.log10(df["p"].clip(lower=1e-300))) > 1).sum())
    leads = lead_loci(df, opts["gws"], opts["clump_kb"] * 1000)

    artifacts = []
    adir = artifact_dir()
    if adir and leads:
        with open(os.path.join(adir, "lead_loci.tsv"), "w") as f:
            f.write("chr\tpos\trsid\tea\toa\tbeta\tse\tp\n")
            for l in leads:
                f.write(f"{l.chr}\t{l.pos}\t{getattr(l, 'rsid', '')}\t{l.ea}\t{l.oa}\t{l.beta:.5g}\t{l.se:.5g}\t{l.p:.3g}\n")
        artifacts.append({"type": "table", "path": "lead_loci.tsv", "label": "Lead loci"})

    frac_bad = inconsistent / max(len(df), 1)
    verdict, messages = "pass", []
    if not np.isfinite(lam) or len(df) == 0:
        write_log(args.log, "stats_summary", "fail", {"n_variants": len(df)},
                  ["Could not compute λGC: no usable P-values. Check the earlier steps."])
        return
    if lam > opts["fail_lambda"]:
        verdict = "fail"
        messages.append(f"λGC = {lam:.3f} is extreme: likely uncorrected stratification or a units problem.")
    elif lam > opts["warn_lambda"]:
        verdict = "warn"
        messages.append(f"λGC = {lam:.3f} is above {opts['warn_lambda']}: possible inflation.")
    elif lam < opts["low_lambda"]:
        verdict = "warn"
        messages.append(f"λGC = {lam:.3f} is unusually low: check that P-values aren't over-corrected.")
    else:
        messages.append(f"λGC = {lam:.3f} is within the expected range.")
    if frac_bad > opts["warn_inconsistent_fraction"]:
        verdict = "warn" if verdict == "pass" else verdict
        messages.append(f"{frac_bad:.1%} of variants have P inconsistent with BETA/SE: check the SE column.")
    zr = z_ratio(z, df["p"])
    if zr is not None and not 0.8 < zr < 1.25:
        verdict = "warn" if verdict == "pass" else verdict
        messages.append(f"BETA/SE is consistently {zr:.2f} times the Z implied by P: SE looks to be on another scale "
                        "(for example the SE of the odds ratio rather than of log-odds).")
    messages.append(f"{len(leads)} genome-wide significant loci (P < {opts['gws']:g}, {opts['clump_kb']} kb windows).")

    metrics = {"lambda_gc": round(lam, 4), "n_variants": len(df), "n_genome_wide_sig": int((df["p"] < opts["gws"]).sum()),
               "n_suggestive": int((df["p"] < opts["suggestive"]).sum()), "n_loci": len(leads), "z_p_inconsistent": inconsistent}
    lam1000 = lambda_1000(lam, params.get("n_cases"), params.get("n_controls"), params.get("n_total"),
                          params.get("trait_type", "binary"))
    if lam1000 is not None:
        metrics["lambda_1000"] = round(lam1000, 4)
    if zr is not None:
        metrics["z_ratio_median"] = round(zr, 4)
    trait_type = params.get("trait_type", "binary")
    ratio = se_ratio(df, params, trait_type)
    if ratio is not None and trait_type == "quantitative":
        metrics["implied_trait_sd"] = round(ratio, 4)
        messages.append(f"SE, N and EAF imply a trait SD of {ratio:.3g} in the units of BETA (about 1 for an inverse-normal "
                        "transformed trait).")
    elif ratio is not None:
        metrics["se_ratio_expected"] = round(ratio, 4)
        phi = case_fraction(df, params)
        if ratio < opts["se_ratio_low"] or ratio > opts["se_ratio_high"]:
            verdict = "warn" if verdict == "pass" else verdict
            hint = ("effects look like they are on the linear 0/1 scale (as from BOLT-LMM or linear regression), not log-odds: "
                    "convert them before uploading" if ratio < opts["se_ratio_low"] and phi and 0.5 < ratio / (phi * (1 - phi)) < 2
                    else "N, cases or controls are understated, or effects are not log-odds" if ratio < opts["se_ratio_low"]
                    else "N, cases or controls are overstated, or SE is not that of log-odds")
            messages.append(f"SEs are {ratio:.2g} times what log-odds ratios would have with these cases, controls and EAF: {hint}.")
        else:
            messages.append(f"SEs agree with the sample size (observed / expected {ratio:.2f}).")
    if "eaf" in df.columns:
        eaf = pd.to_numeric(df["eaf"], errors="coerce")
        common = (eaf.between(0.01, 0.99)).to_numpy()
        if common.sum() >= 100:
            metrics["lambda_maf01"] = round(float(np.median(chi2[common]) / stats.chi2.ppf(0.5, 1)), 4)
    write_log(args.log, "stats_summary", verdict, metrics, messages, artifacts)


def z_ratio(z, p):
    """Median |BETA/SE| / |Z from P| over variants with 1e-300 < P < 0.5 (where both are well away from 0); None if few."""
    use = (p > 1e-300) & (p < 0.5) & np.isfinite(z)
    if use.sum() < 100:
        return None
    return float(np.median(np.abs(z[use]) / stats.norm.isf(p[use] / 2)))


def counts(df, params, col, key):
    """Per-variant counts from the column when there, else the study's."""
    if col in df.columns and df[col].notna().any():
        return pd.to_numeric(df[col], errors="coerce")
    try:
        return pd.Series(float(params.get(key)), index=df.index)
    except (TypeError, ValueError):
        return None


def case_fraction(df, params):
    cases, controls = counts(df, params, "ncase", "n_cases"), counts(df, params, "ncontrol", "n_controls")
    if cases is None or controls is None:
        return None
    phi = float((cases / (cases + controls)).median())
    return phi if 0 < phi < 1 else None


def se_ratio(df, params, trait_type, min_variants=1000):
    """Median observed / expected SE (module docstring) over variants with MAF ≥ 5% and INFO ≥ 0.9 (where INFO is given).
    None without EAF and sample sizes, or with fewer than min_variants such variants."""
    if "eaf" not in df.columns:
        return None
    eaf = pd.to_numeric(df["eaf"], errors="coerce")
    pq = eaf * (1 - eaf)
    use = eaf.between(0.05, 0.95)
    if "info" in df.columns:
        use &= pd.to_numeric(df["info"], errors="coerce").fillna(1) >= 0.9
    if trait_type == "quantitative":
        n = counts(df, params, "n", "n_total")
        if n is None:
            return None
        expected = 1 / np.sqrt(2 * pq * n)  # for a trait SD of 1
    else:
        cases, controls = counts(df, params, "ncase", "n_cases"), counts(df, params, "ncontrol", "n_controls")
        if cases is None or controls is None:
            return None
        neff = 4 / (1 / cases + 1 / controls)
        expected = np.sqrt(2 / (pq * neff))
    r = (df["se"] / expected)[use & np.isfinite(expected) & (expected > 0)]
    return float(r.median()) if len(r) >= min_variants else None


def lambda_1000(lam, n_cases, n_controls, n_total=None, trait_type="binary"):
    """λ rescaled to a study of 1,000 cases and 1,000 controls (de Bakker et al. 2008), or for a quantitative trait to a
    study of 1,000 people: 1 + (λ - 1) × 1000 / N. None without the counts it needs."""
    try:
        if trait_type == "quantitative":
            total = float(n_total)
            return 1 + (lam - 1) * 1000 / total if total > 0 else None
        cases, controls = float(n_cases), float(n_controls)
    except (TypeError, ValueError):
        return None
    if cases <= 0 or controls <= 0:
        return None
    return 1 + (lam - 1) * (1 / cases + 1 / controls) / (1 / 1000 + 1 / 1000)


if __name__ == "__main__":
    main()
