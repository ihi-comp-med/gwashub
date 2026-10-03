"""The liftover step's chain parser agrees with the generator's reference implementation."""
import numpy as np
import pandas as pd

from _lib.chain import Chain


def test_chain_matches_truth(synth_small):
    truth = pd.read_csv(f"{synth_small['dir']}/truth_variants.tsv.gz", sep="\t", dtype={"chr": str})
    chain = Chain(f"{synth_small['resources']}/hg19ToHg38.over.chain.gz")
    for c, g in truth.groupby("chr"):
        _, pos, strand = chain.lift(c, g["pos37"].to_numpy())
        expected = g["pos38"].fillna(-1).astype(int).to_numpy()
        assert np.array_equal(pos, expected), c
        mapped = expected > 0
        assert (strand[mapped] == g["strand37to38"].to_numpy()[mapped]).all()
