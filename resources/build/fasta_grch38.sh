#!/usr/bin/env bash
# Homo_sapiens_assembly38_nochr.fasta (+ .fai): the Broad GRCh38 reference (GATK resource bundle, hg38 v0) with the
# "chr" prefix removed from contig names, so GRCh38 contigs are named like GRCh37's (1..22, X, Y; M for chrM).
# gwashub/harmonise_liftover harmonises GRCh38 uploads to it and lifts GRCh37 uploads onto it.
# CHECK: the HERMES 3 HPC file of this name was made the same way? Compare the two .fai files.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://storage.googleapis.com/gcp-public-data--broad-references/hg38/v0/Homo_sapiens_assembly38.fasta \
      Homo_sapiens_assembly38.fasta md5:7ff134953dcca8c8997453bbb80b6b5e)
sed -e 's/^>chr/>/' "$src" > "$OUT/Homo_sapiens_assembly38_nochr.fasta"
in_image gwashub/vcf "/opt/gwas2vcf/bin/python -c 'import pysam; pysam.faidx(\"$OUT/Homo_sapiens_assembly38_nochr.fasta\")'"
record Homo_sapiens_assembly38_nochr.fasta Homo_sapiens_assembly38_nochr.fasta.fai
