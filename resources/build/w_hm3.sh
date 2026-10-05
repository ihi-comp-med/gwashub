#!/usr/bin/env bash
# w_hm3.snplist: the HapMap 3 SNP list (SNP, A1, A2) that LDSC's munge_sumstats.py --merge-alleles uses. The LDSC
# authors' Broad download link is gone; this is the copy bundled with ldscr (mglev1n/ldscr, pinned commit), which is
# the list the HERMES 3 LDSC results used.
source "$(dirname "$0")/_common.sh"
src=$(fetch https://raw.githubusercontent.com/mglev1n/ldscr/d6b23694d8d4f6f2120c98a2c410cf1e455b9246/inst/extdata/w_hm3.snplist \
      w_hm3.snplist sha256:ec73fca0b696e8beba465b51e52911676fcade375bcc9475d99c0ec30509d3ed)
cp "$src" "$OUT/w_hm3.snplist"
record w_hm3.snplist
