#!/usr/bin/env bash
# UKBB.<POP>.l2.ldscore.gz and UKBB.<POP>.l2.M_5_50 for AFR, AMR, CSA, EAS, EUR, MID: the Pan-UK Biobank LD scores in
# LDSC's format (Pan-UKB team 2023), as used by the HERMES 3 analyses (through ldscr). gwashub/ldsc runs the authors' LDSC
# with them. The release's rsID-keyed files (UKBB.<POP>.rsid.l2.ldscore.gz) are kept, under the names LDSC reads as one
# prefix; its chr:pos-keyed files and the 8/25 LD-MAF-bin (LDMS) annotations aren't needed.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://pan-ukb-us-east-1.s3.amazonaws.com/ld_release/UKBB.ALL.ldscore.tar.gz UKBB.ALL.ldscore.tar.gz)
keys=()
for pop in AFR AMR CSA EAS EUR MID; do
  tar xzf "$src" -C "$DL" "UKBB.ALL.ldscore/UKBB.$pop.rsid.l2.ldscore.gz" "UKBB.ALL.ldscore/UKBB.$pop.l2.M_5_50"
  mv "$DL/UKBB.ALL.ldscore/UKBB.$pop.rsid.l2.ldscore.gz" "$OUT/UKBB.$pop.l2.ldscore.gz"
  mv "$DL/UKBB.ALL.ldscore/UKBB.$pop.l2.M_5_50" "$OUT/UKBB.$pop.l2.M_5_50"
  keys+=("UKBB.$pop.l2.ldscore.gz" "UKBB.$pop.l2.M_5_50")
done
record "${keys[@]}"
