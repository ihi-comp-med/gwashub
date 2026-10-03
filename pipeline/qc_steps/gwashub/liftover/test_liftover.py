import gzip

import pandas as pd

from _lib.testing import run_step

# chr1: GRCh37 [0,1000) -> GRCh38 [500,1500) on +; GRCh37 [2000,3000) -> inverted on - (qSize 10000).
CHAIN = """# test chain
chain 1 chr1 5000 + 0 1000 chr1 10000 + 500 1500 1
1000

chain 1 chr1 5000 + 2000 3000 chr1 10000 - 7000 8000 2
1000
"""


def setup(tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    with gzip.open(res / "hg19ToHg38.over.chain.gz", "wt") as f:
        f.write(CHAIN)
    return res


def test_lifts_plus_and_minus_strand_and_drops_unmapped(tmp_path):
    res = setup(tmp_path)
    df = pd.DataFrame({"chr": "1", "pos": [1, 1000, 1500, 2001, 3000], "ea": ["A", "C", "G", "AC", "T"],
                       "oa": ["G", "T", "A", "A", "C"], "beta": 0.1, "se": 0.1, "p": 0.5})
    log, out = run_step("liftover", df, {"build": "GRCh37"}, tmp_path, resources=res)
    assert log["metrics"]["unmapped"] == 1  # pos 1500 is in the gap
    out = out.set_index("pos")
    assert 501 in out.index and 1500 in out.index  # +500 shift
    # minus strand: t=2000 (pos 2001) -> q_strand 7000 -> fwd 10000-1-7000 = 2999 -> pos 3000
    assert out.loc[3000, "ea"] == "GT" and out.loc[3000, "oa"] == "T"  # reverse complement of AC / A
    assert log["metrics"]["strand_flipped"] == 2
    assert log["verdict"] == "fail"  # 1/5 unmapped > 10%


def test_grch38_is_a_no_op(tmp_path):
    df = pd.DataFrame({"chr": "1", "pos": [5], "ea": "A", "oa": "G", "beta": 0.1, "se": 0.1, "p": 0.5})
    log, out = run_step("liftover", df, {"build": "GRCh38"}, tmp_path)
    assert log["verdict"] == "pass" and out["pos"].tolist() == [5]
