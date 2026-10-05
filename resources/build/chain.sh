#!/usr/bin/env bash
# hg19ToHg38.over.chain.gz: UCSC liftOver chain, GRCh37 -> GRCh38 (used by gwashub/harmonise_liftover).
source "$(dirname "$0")/_common.sh"
src=$(fetch https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz hg19ToHg38.over.chain.gz \
      md5:35887f73fe5e2231656504d1f6430900)
cp "$src" "$OUT/hg19ToHg38.over.chain.gz"
record hg19ToHg38.over.chain.gz
