#!/usr/bin/env bash
# human_g1k_v37.fasta (+ .fai): the 1000 Genomes GRCh37 reference (contigs 1..22, X, Y, MT without "chr"), as used by
# the HERMES 3 pipeline. gwashub/harmonise_liftover harmonises GRCh37 uploads to it; bcftools +liftover reads it too.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/human_g1k_v37.fasta.gz human_g1k_v37.fasta.gz)
# This file has padding after the gzip stream: gzip warns "trailing garbage ignored" and exits 2. The FASTA is complete
# (checked below), so exit 2 is accepted; anything worse still stops the script.
gzip -dc "$src" > "$OUT/human_g1k_v37.fasta" || [ $? -eq 2 ]
n=$(grep -c '^>' "$OUT/human_g1k_v37.fasta")
[ "$n" = 84 ] || { echo "expected 84 sequences in human_g1k_v37.fasta, found $n" >&2; exit 1; }
in_image gwashub/vcf "/opt/gwas2vcf/bin/python -c 'import pysam; pysam.faidx(\"$OUT/human_g1k_v37.fasta\")'"
record human_g1k_v37.fasta human_g1k_v37.fasta.fai
