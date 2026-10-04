"""Example consortium meta-analysis method: weighted Z-score (Stouffer).

Follows the method contract (CONTRIBUTING.md): reads --inputs and --params, writes
<out>/meta_results.tsv.gz with chr pos ea oa z p n_studies (here: no BETA/SE), and a log. GWASHub then adds
the lead loci, plots and summary itself.
"""
import numpy as np
import pandas as pd
from scipy import stats

from _metalib.common import load_study, marker_id, parse_args, write_log, write_results


def main():
    a, specs, p = parse_args()
    frames, infos = [], []
    for i, s in enumerate(specs):
        df, info = load_study(s)
        df = df.assign(marker=marker_id(df), z=df["beta"] / df["se"], study_rank=i)
        frames.append(df[["marker", "chr", "pos", "ea", "oa", "z", "n", "study_rank"]])
        infos.append(info)
    long = pd.concat(frames, ignore_index=True).sort_values(["marker", "study_rank"])
    # Orient every dataset to the effect allele of the first dataset that has the variant.
    first_ea = long.groupby("marker")["ea"].transform("first")
    long["z"] = np.where(long["ea"] == first_ea, long["z"], -long["z"])
    long["w"] = np.sqrt(long["n"].clip(lower=1)) if p.get("weighting", "sqrt_n") == "sqrt_n" else 1.0
    long["wz"], long["w2"] = long["w"] * long["z"], long["w"] ** 2
    long["sym"] = np.where(long["z"] > 0, "+", "-")
    g = long.groupby("marker", sort=False)
    res = g.agg(chr=("chr", "first"), pos=("pos", "first"), ea=("ea", "first"), swz=("wz", "sum"), sw2=("w2", "sum"),
                n_studies=("z", "size"), n=("n", "sum")).reset_index()
    alleles = res["marker"].str.split(":", expand=True)  # chr:pos:allele1:allele2
    res["oa"] = np.where(res["ea"] == alleles[2], alleles[3], alleles[2])
    res["z"] = res["swz"] / np.sqrt(res["sw2"])
    res["p"] = 2 * stats.norm.sf(np.abs(res["z"]))
    # Direction string, one symbol per dataset in input order (? = missing).
    syms = long.pivot_table(index="marker", columns="study_rank", values="sym", aggfunc="first").reindex(columns=range(len(specs)))
    res["direction"] = res["marker"].map(syms.fillna("?").agg("".join, axis=1))
    res = res[res["n_studies"] >= p.get("min_studies", 2)].drop(columns=["marker", "swz", "sw2"])
    write_results(res, a.out)
    write_log(a.log, "custom_example/custom_meta_analysis", "pass", {"weighting": p.get("weighting", "sqrt_n")},
              [f"Weighted Z-score meta-analysis of {len(res):,} variants across {len(specs)} datasets."], studies=infos)


if __name__ == "__main__":
    main()
