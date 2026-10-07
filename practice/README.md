# Practice files

Synthetic summary statistics for the site's **Practice project**, where people learn GWASHub (with the guided tours in
the Help menu) without touching real data. They come from `devtools/synth.py` (a synthetic genome with injected
heart-failure loci), in three common formats: PLINK (GRCh37), REGENIE (GRCh38) and GWAS-SSF (GRCh38). `files.json`
gives each file's details and column headers, shown on the Practice project's Overview so people know what to enter.

They don't match real reference genomes, so on a deployment with real references QC warns about them (fewer variants
harmonised): that is expected, and gives the review tour something to review.
