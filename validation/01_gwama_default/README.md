# Test 1: GWAMA meta-analysis, default settings

**Claim:** a fixed-effect meta-analysis on GWASHub gives the same results as GWAMA run by hand on the same files.

**Why it matters:** the default meta-analysis is what most people run. Running the same GWAMA release by hand, with
inputs prepared independently in R, checks everything GWASHub does around GWAMA: preparing inputs, matching variants
across datasets, passing flags and reading the output.

## Steps

1. Check `UKB`, `HERMES` and `FINNGEN` in `validation/sources.yaml` (url, md5, columns, n) and confirm the lead SNPs in
   `test.yaml`.
2. `Rscript validation/01_gwama_default/1_prepare.R` writes `local/validation/01_gwama_default/inputs/<SOURCE>.tsv`
   and prints the details to enter when uploading.
3. On the site, in the `validation` project: upload the three files (cohort name = the source name), let QC finish
   (pass or warn; a failure stops the test). Download each dataset's **QC'd file** to
   `local/validation/01_gwama_default/site/<SOURCE>_qc.tsv`.
4. Run a meta-analysis of the three datasets with **GWAMA, default settings**. Download `meta_results.tsv.gz` to
   `site/meta_results.tsv.gz`. Run the same meta-analysis a **second time** and save its file as
   `site/meta_results_rerun.tsv.gz` (the re-run check).
5. Fill in `site_run.yaml`: dataset, run and meta-analysis ids, the reference lines from the bottom of each page
   (copy button), the pipeline version and image digests, and `dataset_order` (the meta-analysis's datasets.tsv,
   `position` column: the direction string's order).
6. `Rscript validation/01_gwama_default/2_reference.R` (GWAMA by hand, in Docker).
7. `Rscript validation/01_gwama_default/3_compare.R` writes `result.json`, `plot.json` and `manifest.tsv`.
8. Commit `result.json`, `plot.json`, `manifest.tsv` and `site_run.yaml`; archive `local/validation/01_gwama_default/`
   (see `validation/README.md`) and put its location in `test.yaml` `archive`.

## Checks (thresholds in `test.yaml`)

| Check | Against | Threshold |
|---|---|---|
| Same variants | GWAMA by hand | none only in one |
| β and se | GWAMA by hand | largest absolute difference ≤ 1e-6 |
| p | GWAMA by hand | largest relative difference ≤ 1e-4 |
| n_studies and direction | GWAMA by hand | no rows differ |
| Q, its p and I² | GWAMA by hand | ≤ 1e-6 |
| Total N and EAF | GWAMA by hand | ≤ 1e-6 |
| Re-run | second site run | same sha256 |
