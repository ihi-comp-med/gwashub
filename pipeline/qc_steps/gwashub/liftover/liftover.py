"""Liftover: GRCh37 -> GRCh38 using a chain file; reverse-complement alleles on strand flips."""
import os

import numpy as np
import pandas as pd

from _lib.chain import Chain
from _lib.common import artifact_dir, load_params, parse_args, read_working, step_params, write_log, write_working

DEFAULTS = {"chain": "hg19ToHg38.over.chain.gz", "warn_unmapped_fraction": 0.01, "fail_unmapped_fraction": 0.1}
COMP = str.maketrans("ACGT", "TGCA")


def revcomp(s):
    return s.translate(COMP)[::-1]


def main():
    args = parse_args()
    params = load_params(args.params)
    opts = step_params(params, DEFAULTS)
    build = (params.get("build") or "GRCh38").upper()
    if build in ("GRCH38", "HG38"):
        write_log(args.log, "liftover", "pass", {"lifted": 0}, ["Already GRCh38; nothing to do."])
        return

    chain = Chain(os.path.join(args.resources, opts["chain"]))
    df = read_working(args.input)
    n0 = len(df)
    new_chr = np.empty(n0, dtype=object)
    new_pos = np.full(n0, -1, dtype=np.int64)
    strand = np.empty(n0, dtype=object)
    for c, idx in df.groupby("chr").indices.items():
        qc, qp, st = chain.lift(c, df["pos"].to_numpy()[idx])
        new_chr[idx], new_pos[idx], strand[idx] = qc, qp, st

    mapped = new_pos > 0
    unmapped = df[~mapped]
    out = df[mapped].copy()
    out["chr"] = new_chr[mapped]
    out["pos"] = new_pos[mapped]
    minus = strand[mapped] == "-"
    out.loc[minus, "ea"] = out.loc[minus, "ea"].map(revcomp)
    out.loc[minus, "oa"] = out.loc[minus, "oa"].map(revcomp)
    out = out.sort_values(["chr", "pos"], key=lambda s: pd.to_numeric(s.replace({"X": "23"}), errors="coerce") if s.name == "chr" else s)

    artifacts = []
    adir = artifact_dir()
    if adir and len(unmapped):
        unmapped.to_csv(os.path.join(adir, "unmapped.tsv"), sep="\t", index=False)
        artifacts.append({"type": "table", "path": "unmapped.tsv", "label": "Variants that could not be lifted"})

    frac = len(unmapped) / max(n0, 1)
    verdict = "fail" if frac > opts["fail_unmapped_fraction"] else "warn" if frac > opts["warn_unmapped_fraction"] else "pass"
    msg = [f"Lifted {len(out):,} variants to GRCh38; {len(unmapped):,} ({frac:.2%}) could not be mapped."]
    if int(minus.sum()):
        msg.append(f"{int(minus.sum()):,} variants were on an inverted segment; alleles reverse-complemented.")
    write_working(out, args.input)
    write_log(args.log, "liftover", verdict,
              {"lifted": len(out), "unmapped": len(unmapped), "unmapped_fraction": round(frac, 5),
               "strand_flipped": int(minus.sum()), "rows_out": len(out)}, msg, artifacts)


if __name__ == "__main__":
    main()
