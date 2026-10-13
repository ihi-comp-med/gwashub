"""Test data for the SGC steps: a small GRCh37 file of real chromosome 22 SNPs (positions, rsIDs and alleles taken from
the dbSNP 155 and genome packages inside the sgc/mungesumstats image, so nothing is committed or downloaded)."""
import subprocess

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from _lib.testing import images_built

IMAGE = "sgc/mungesumstats"
# n SNVs from dbSNP 155 on GRCh37 chromosome 22, with the reference base (hs37d5) and one alternative allele.
REAL_SNPS = r'''
suppressPackageStartupMessages({ library(BSgenome); library(BSgenome.Hsapiens.1000genomes.hs37d5);
                                 library(SNPlocs.Hsapiens.dbSNP155.GRCh37) })
snps <- SNPlocs.Hsapiens.dbSNP155.GRCh37
chr <- grep("22$", seqlevels(snps), value = TRUE)[1]
s <- snpsBySeqname(snps, chr)
s <- s[seq(1, length(s), length.out = 40000)]
iupac <- Biostrings::IUPAC_CODE_MAP[as.character(s$alleles_as_ambig)]
s <- s[nchar(iupac) == 2]; iupac <- iupac[nchar(iupac) == 2]
ref <- as.character(getSeq(BSgenome.Hsapiens.1000genomes.hs37d5, GRanges("22", IRanges(start(s), width = 1))))
alt <- mapply(function(a, r) sub(r, "", a), iupac, ref)
ok <- nchar(alt) == 1
d <- data.frame(rsid = s$RefSNP_id[ok], pos = start(s)[ok], ref = ref[ok], alt = alt[ok])
write.table(d[seq_len(min(nrow(d), N)), ], stdout(), sep = "\t", quote = FALSE, row.names = FALSE)
'''


def real_snps(n=3000):
    out = subprocess.run(["docker", "run", "--rm", "-i", IMAGE, "Rscript", "-"], input=REAL_SNPS.replace("N)", f"{n})"),
                         capture_output=True, text=True, check=True).stdout
    return pd.read_csv(pd.io.common.StringIO(out), sep="\t")


@pytest.fixture(scope="session")
def grch37_file():
    if not images_built(IMAGE):
        pytest.skip(f"needs Docker image {IMAGE}")
    snps = real_snps()
    rng = np.random.default_rng(7)
    n = len(snps)
    flip = rng.random(n) < 0.4  # effect allele the reference allele for some rows: MungeSumstats flips them
    beta = rng.normal(0, 0.05, n)
    se = rng.uniform(0.02, 0.06, n)
    df = pd.DataFrame({"chr": "22", "pos": snps["pos"], "ea": np.where(flip, snps["ref"], snps["alt"]),
                       "oa": np.where(flip, snps["alt"], snps["ref"]), "beta": beta, "se": se,
                       "p": 2 * stats.norm.sf(np.abs(beta / se)), "eaf": rng.uniform(0.05, 0.95, n),
                       "n": 20000, "ncase": 2000, "ncontrol": 18000, "rsid": snps["rsid"], "info": rng.uniform(0.5, 1, n)})
    df.loc[:49, "info"] = 0.1      # below SGC's INFO 0.3
    df.loc[50:79, "eaf"] = 0.001   # below SGC's 0.005
    return df


