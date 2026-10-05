#!/usr/bin/env bash
# 1000 Genomes high-coverage GRCh38 (Byrska-Bishop et al. 2022; 3,202 samples), from the PLINK 2.0 resources page
# (https://www.cog-genomics.org/plink/2.0/resources#phase3_1kg: "2022-08-04", no INFO annotations, KING-corrected
# pedigree). Builds, for the founders only, on chromosomes 1-22 and X, biallelic variants, with IDs set to
# chr:pos:ref:alt (no "chr"), matching GWASHub's QC output (effect allele = ALT):
#   1kg_b38_<POP>.pgen/.pvar.zst/.psam   LD reference panels per super-population (AFR AMR EAS EUR SAS): lead-locus clumping
#   all_afreq_b38.tsv.gz                 ALT allele frequency per super-population: gwashub/eaf_check
# About 4 GB to download; an hour or so to build.
# NOTE: the HERMES 3 EAF check used a 1000 Genomes phase 3 frequency file lifted to GRCh38; this is the newer
# high-coverage GRCh38 callset, so frequencies can differ slightly.
source "$(dirname "$0")/_common.sh"
pgen=$(fetch "https://www.dropbox.com/s/j72j6uciq5zuzii/all_hg38.pgen.zst?dl=1" all_hg38.pgen.zst)
fetch "https://www.dropbox.com/s/ngbo2xm5ojw9koy/all_hg38_noannot.pvar.zst?dl=1" all_hg38.pvar.zst >/dev/null
fetch "https://www.dropbox.com/scl/fi/u5udzzaibgyvxzfnjcvjc/hg38_corrected.psam?rlkey=oecjnk4vmbhc8b1p202l0ih4x&dl=1" all_hg38.psam \
      sha256:727a01b637d5e1ef02516a53069dc552b1fa46f442004ad50ea3d069c7741cdd >/dev/null
POPS="AFR AMR EAS EUR SAS"
in_image gwashub/base "set -e
  [ -s all_hg38.pgen ] || plink2 --zst-decompress all_hg38.pgen.zst all_hg38.pgen
  # Founders, chr1-22 + X, biallelic, IDs chr:pos:ref:alt; duplicated IDs (rare) dropped.
  plink2 --pfile all_hg38 vzs --keep-founders --chr 1-22,X --max-alleles 2 --output-chr MT \
         --set-all-var-ids @:#:\\\$r:\\\$a --new-id-max-allele-len 1000 missing --rm-dup exclude-all \
         --make-pgen vzs --out kg_b38_founders
  for pop in $POPS; do
    # Panel: this super-population's samples and the variants polymorphic in them.
    plink2 --pfile kg_b38_founders vzs --keep-if SuperPop==\$pop --mac 1 --make-pgen vzs --out $OUT/1kg_b38_\$pop
    # Frequencies: the same variants for every population, in the same order, so the columns line up.
    plink2 --pfile kg_b38_founders vzs --keep-if SuperPop==\$pop --freq cols=chrom,pos,ref,alt,altfreq --out freq_\$pop
  done"
# all_afreq_b38.tsv.gz: id chr pos_b38 ref alt afr_afreq amr_afreq eas_afreq eur_afreq sas_afreq
python3 - "$DL" "$OUT/all_afreq_b38.tsv.gz" $POPS <<'PY'
import gzip, sys
dl, out, pops = sys.argv[1], sys.argv[2], sys.argv[3:]
files = [open(f"{dl}/freq_{p}.afreq") for p in pops]
with gzip.open(out, "wt") as w:
    w.write("id\tchr\tpos_b38\tref\talt\t" + "\t".join(f"{p.lower()}_afreq" for p in pops) + "\n")
    for f in files:
        f.readline()  # header: #CHROM POS ID REF ALT ALT_FREQS
    for rows in zip(*files):
        cols = [r.rstrip("\n").split("\t") for r in rows]
        chrom, pos, vid, ref, alt = cols[0][:5]
        assert all(c[2] == vid for c in cols), f"frequency files out of step at {vid}"
        w.write("\t".join([vid, chrom, pos, ref, alt, *(c[5] for c in cols)]) + "\n")
PY
keys=(all_afreq_b38.tsv.gz)
for pop in $POPS; do keys+=("1kg_b38_$pop.pgen" "1kg_b38_$pop.pvar.zst" "1kg_b38_$pop.psam"); done
record "${keys[@]}"
