"""Summary statistics: genomic inflation (λGC, λ1000, λ at MAF ≥ 1%), Z vs P consistency, lead loci."""
import os

import numpy as np
import pandas as pd
from scipy import stats

from _lib.common import artifact_dir, load_params, parse_args, read_working, step_params, write_log

DEFAULTS = {"warn_lambda": 1.1, "fail_lambda": 1.5, "low_lambda": 0.9, "gws": 5e-8, "suggestive": 1e-5, "clump_kb": 500,
            "warn_inconsistent_fraction": 0.01}


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
    messages.append(f"{len(leads)} genome-wide significant loci (P < {opts['gws']:g}, {opts['clump_kb']} kb windows).")

    metrics = {"lambda_gc": round(lam, 4), "n_variants": len(df), "n_genome_wide_sig": int((df["p"] < opts["gws"]).sum()),
               "n_suggestive": int((df["p"] < opts["suggestive"]).sum()), "n_loci": len(leads), "z_p_inconsistent": inconsistent}
    lam1000 = lambda_1000(lam, params.get("n_cases"), params.get("n_controls"), params.get("n_total"),
                          params.get("trait_type", "binary"))
    if lam1000 is not None:
        metrics["lambda_1000"] = round(lam1000, 4)
    if "eaf" in df.columns:
        eaf = pd.to_numeric(df["eaf"], errors="coerce")
        common = (eaf.between(0.01, 0.99)).to_numpy()
        if common.sum() >= 100:
            metrics["lambda_maf01"] = round(float(np.median(chi2[common]) / stats.chi2.ppf(0.5, 1)), 4)
    write_log(args.log, "stats_summary", verdict, metrics, messages, artifacts)


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
