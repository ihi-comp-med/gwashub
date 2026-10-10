# Test 2: GWAMA with every option on

**Claim:** each GWAMA setting on GWASHub (random effects, genomic control on the datasets and on the results, the MAF
filter, minimum datasets, the direction threshold and the sex-differentiated test) does what GWAMA does with that
option.

**Why it matters:** the defaults are what most people run, but the options are where wrappers usually go wrong: a flag
not passed, or a filter applied after GWAMA instead of before. One run with everything on shows they are all wired up.

Two site meta-analyses (test.yaml `runs`): **options** (random effects, genomic control per dataset and on the
results, minimum MAF 0.01, minimum 3 datasets, direction shown below P 0.05) on test 1's three datasets; and **sex**
(GWAMA's sex-differentiated test) on UK Biobank females and males, since that test needs every dataset to be male-only
or female-only. The female and male files are Neale lab's (linear regression on the 0/1 trait): QC is expected to warn
that their effects are on the linear scale, which doesn't affect this comparison.

## Steps

1. Run test 1 first (this test uses its inputs and its three QC'd files). Check `UKB_F` and `UKB_M` in
   `validation/sources.yaml`.
2. `Rscript validation/02_gwama_options/1_prepare.R` downloads the Neale lab files (about 2 GB with the rsID table),
   keeps test 1's UKB variants and writes `local/validation/02_gwama_options/inputs/UKB_F.tsv` and `UKB_M.tsv`.
3. On the site, in the `validation` project: upload `UKB_F.tsv` (sex Female) and `UKB_M.tsv` (sex Male) with the
   counts the script prints; run QC (pass or warn). Download each QC'd file to `site/UKB_F_qc.tsv` and
   `site/UKB_M_qc.tsv`.
4. Run the two meta-analyses with GWAMA and the settings in test.yaml `runs.<run>.site_params`, each **twice**. For each
   run save to `site/<run>/`: `meta_results.tsv.gz` (and the second run's as `meta_results_rerun.tsv.gz`) and
   `datasets.tsv`; for the options run also `gwama_gc.txt` (the GWAMA genomic control file on the results page). If
   the options run used other QC runs of UKB, HERMES or FINNGEN than test 1 did (datasets.tsv `qc_run_id`), download
   those QC'd files to `site/<SOURCE>_qc.tsv` too: the reference must use the same files.
5. Fill in `site_run.yaml`, including each run's `dataset_order` (datasets.tsv, `position` column).
6. `Rscript validation/02_gwama_options/2_reference.R` (GWAMA by hand, in Docker).
7. `Rscript validation/02_gwama_options/3_compare.R` writes `result.json`, `plot.json` and `manifest.tsv`.
8. Commit `result.json`, `plot.json`, `manifest.tsv` and `site_run.yaml`; archive `local/validation/02_gwama_options/`.

## Checks (thresholds in `test.yaml`), for each run

| Check | Against | Threshold |
|---|---|---|
| Same variants (after the MAF filter and minimum datasets) | GWAMA by hand | none only in one |
| β and se | GWAMA by hand | ≤ 1e-6 |
| p | GWAMA by hand | relative ≤ 1e-4 |
| n_studies and direction (with the 0.05 threshold) | GWAMA by hand | no rows differ |
| Q, its p and I² | GWAMA by hand | ≤ 1e-6 |
| Total N and EAF | GWAMA by hand | ≤ 1e-6 |
| Variants removed by the MAF filter, per dataset | R | identical |
| λ per dataset (options run) | GWAMA by hand's genomic control file | ≤ 1e-6 |
| Sex-differentiated and sex-heterogeneity p (sex run) | GWAMA by hand | relative ≤ 1e-4 |
| Re-run | second site run | same sha256 |
