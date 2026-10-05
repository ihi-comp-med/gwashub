#!/usr/bin/env bash
# dbSNP157_b38_clean.vcf.gz (+ .tbi): dbSNP build 157 on GRCh38 (NCBI), reduced to what gwashub/harmonise_liftover's
# rsID annotation needs: primary chromosomes renamed from RefSeq accessions to 1..22, X, Y, MT; INFO dropped;
# multi-allelic records split so each REF/ALT pair matches on its own. About 30 GB to download; an hour or two to build
# (bcftools runs natively in gwashub/base; needs ~45 GB free disk: the download plus the output).
# CHECK: the HERMES 3 HPC file of this name was "cleaned" the same way? Compare record counts on one chromosome.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://ftp.ncbi.nih.gov/snp/archive/b157/VCF/GCF_000001405.40.gz GCF_000001405.40.vcf.gz \
      md5:6a6f313e92a39c337571174dad12cfe1)
fetch https://ftp.ncbi.nih.gov/snp/archive/b157/VCF/GCF_000001405.40.gz.tbi GCF_000001405.40.vcf.gz.tbi >/dev/null
# RefSeq accession -> chromosome name, for the primary chromosomes, from NCBI's report for the same assembly.
report=$(fetch https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.40_GRCh38.p14/GCF_000001405.40_GRCh38.p14_assembly_report.txt \
         GCF_000001405.40_GRCh38.p14_assembly_report.txt)
map=$DL/refseq_to_chr.txt
grep -v '^#' "$report" | awk -F'\t' '$2 == "assembled-molecule" {print $7 "\t" $1}' > "$map"
[ "$(wc -l < "$map" | tr -d ' ')" = 25 ] || { echo "expected 25 primary chromosomes in $report" >&2; exit 1; }
regions=$(cut -f1 "$map" | paste -sd, -)
in_image gwashub/base "bcftools view -r $regions $src -Ou \
  | bcftools annotate --rename-chrs $map -x INFO -Ou \
  | bcftools norm -m -any -Oz -o $OUT/dbSNP157_b38_clean.vcf.gz --threads 4 \
  && tabix -f -p vcf $OUT/dbSNP157_b38_clean.vcf.gz"
record dbSNP157_b38_clean.vcf.gz dbSNP157_b38_clean.vcf.gz.tbi
