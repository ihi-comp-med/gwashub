"""Inverse-variance weighted meta-analysis: fixed effects, or DerSimonian-Laird random effects."""
import numpy as np
import pandas as pd
from scipy import stats

from _metalib.common import load_study, parse_args, write_log, write_results


def meta(long, study_order, model, min_studies):
    # Canonical allele pair per variant; orient every study to the effect allele of the first study seen.
    long["a1"] = np.where(long["ea"] < long["oa"], long["ea"], long["oa"])
    long["a2"] = np.where(long["ea"] < long["oa"], long["oa"], long["ea"])
    key = ["chr", "pos", "a1", "a2"]
    long = long.sort_values(key + ["study_rank"])
    first_ea = long.groupby(key, sort=False)["ea"].transform("first")
    sign = np.where(long["ea"] == first_ea, 1.0, -1.0)
    long["b"] = long["beta"] * sign
    if "eaf" in long:
        long["f"] = np.where(sign > 0, long["eaf"], 1 - long["eaf"])
    long["ea"] = first_ea
    long["oa"] = np.where(long["ea"] == long["a1"], long["a2"], long["a1"])
    long["w"] = 1.0 / long["se"] ** 2
    long["wb"] = long["w"] * long["b"]

    g = long.groupby(key, sort=False)
    agg = g.agg(ea=("ea", "first"), oa=("oa", "first"), k=("b", "size"), sw=("w", "sum"), swb=("wb", "sum"),
                sw2=("w", lambda w: float((w ** 2).sum())), n=("n", "sum"))
    if "rsid" in long:
        agg["rsid"] = g["rsid"].first()
    agg = agg[agg["k"] >= min_studies]
    beta_fe = agg["swb"] / agg["sw"]
    long = long.join(beta_fe.rename("beta_fe"), on=key, how="inner")
    long["qterm"] = long["w"] * (long["b"] - long["beta_fe"]) ** 2
    agg["q"] = long.groupby(key, sort=False)["qterm"].sum()
    df_ = agg["k"] - 1
    agg["het_p"] = np.where(df_ > 0, stats.chi2.sf(agg["q"], df_.clip(lower=1)), np.nan)
    agg["i2"] = np.where(agg["q"] > 0, np.clip((agg["q"] - df_) / agg["q"], 0, 1), 0.0)

    if model == "random":
        tau2 = np.clip((agg["q"] - df_) / (agg["sw"] - agg["sw2"] / agg["sw"]), 0, None).fillna(0)
        long = long.join(tau2.rename("tau2"), on=key)
        long["wr"] = 1.0 / (long["se"] ** 2 + long["tau2"])
        long["wrb"] = long["wr"] * long["b"]
        gr = long.groupby(key, sort=False)
        agg["beta"] = gr["wrb"].sum() / gr["wr"].sum()
        agg["se"] = np.sqrt(1.0 / gr["wr"].sum())
        agg["tau2"] = tau2
    else:
        agg["beta"] = beta_fe
        agg["se"] = np.sqrt(1.0 / agg["sw"])

    agg["p"] = 2 * stats.norm.sf(np.abs(agg["beta"] / agg["se"]))
    if "f" in long:
        long["fn"] = long["f"] * long["n"]
        agg["eaf"] = long.groupby(key, sort=False)["fn"].sum() / long.groupby(key, sort=False)["n"].sum()

    # METAL-style direction string in study order: + / - / ?
    rank = {s: i for i, s in enumerate(study_order)}
    long["sym"] = np.where(long["b"] > 0, "+", "-")
    long["r"] = long["study"].map(rank)
    syms = long.pivot_table(index=key, columns="r", values="sym", aggfunc="first").reindex(columns=range(len(study_order)))
    agg["direction"] = syms.fillna("?").agg("".join, axis=1)
    res = agg.reset_index().rename(columns={"k": "n_studies"})
    return res.drop(columns=["a1", "a2", "sw", "swb", "sw2"])


def main():
    a, specs, p = parse_args()
    frames, infos = [], []
    for i, s in enumerate(specs):
        df, info = load_study(s, p.get("maf_min", 0.01), p.get("drop_palindromic", False), p.get("genomic_control", False))
        cols = ["chr", "pos", "ea", "oa", "beta", "se", "n"] + [c for c in ("eaf", "rsid") if c in df]
        df = df[cols].copy()
        df["study"], df["study_rank"] = s["id"], i
        frames.append(df)
        infos.append(info)
    res = meta(pd.concat(frames, ignore_index=True), [s["id"] for s in specs], p.get("model", "fixed"), p.get("min_studies", 2))
    write_results(res, a.out)
    write_log(a.log, "gwashub/ivw", "pass", {"model": p.get("model", "fixed")},
              [f"Inverse-variance {p.get('model', 'fixed')}-effects meta-analysis of {len(res):,} variants across {len(specs)} datasets."],
              studies=infos)


if __name__ == "__main__":
    main()
