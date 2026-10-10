# Test 3: METAL, both schemes, and agreement with GWAMA

**Claim:** METAL on GWASHub matches METAL run by hand in both schemes and with its options, and two independent
programs (METAL and GWAMA) give the same fixed-effect answer through GWASHub.

**Why it matters:** the cross-program check is the strongest statement on the page: two independent implementations
of the same model, run through GWASHub, give the same answer.

## Steps

1. Run test 1 first: its three QC'd files are this test's inputs, and its GWAMA results the cross-program reference.
   `Rscript validation/03_metal/1_prepare.R` checks they are there and prints the two runs' settings.
2. On the site, in the `validation` project, run three meta-analyses of test 1's three datasets with **METAL**, each
   **twice**: `stderr` (defaults), `samplesize_plain` (sample-size scheme, defaults) and `samplesize` (overlap
   correction on, Z cut-off 1, minimum weight 10,000, min/max EAF on, precision 8); settings in test.yaml
   `runs.<run>.site_params`. For each save to `site/<run>/`:
   `meta_results.tsv.gz` (the second run's as `meta_results_rerun.tsv.gz`) and `lead_loci.tsv`. If the runs used
   other QC runs than test 1 did (datasets.tsv `qc_run_id`), save those QC'd files as `site/<SOURCE>_qc.tsv` too.
3. Fill in `site_run.yaml`, including `dataset_order` (datasets.tsv, `position` column).
4. `Rscript validation/03_metal/2_reference.R` (METAL by hand, in Docker, with a script written in R that mirrors
   the settings).
5. `Rscript validation/03_metal/3_compare.R` writes `result.json`, `plot.json` and `manifest.tsv`.
6. Commit `result.json`, `plot.json`, `manifest.tsv` and `site_run.yaml`; archive `local/validation/03_metal/`.

## Checks (thresholds in `test.yaml`)

| Check | Against | Threshold |
|---|---|---|
| Variants, including MINWEIGHT removals (each run) | METAL by hand | none only in one |
| β, se or Z, weight, EAF, min/max EAF, Q, I² (each run) | METAL by hand | ≤ 1e-12 (the site re-writes METAL's printed numbers) |
| p and heterogeneity p (each run) | METAL by hand | relative ≤ 1e-10 |
| n_studies and direction (each run) | METAL by hand | no rows differ |
| Re-run (each run) | second site run | same sha256 |
| METAL stderr vs GWAMA fixed effects: β, se | test 1's site GWAMA run | ≤ 1e-5 |
| METAL stderr vs GWAMA fixed effects: −log10 p, for p > 1e-5 | test 1's site GWAMA run | ≤ 0.01 |
| METAL samplesize (plain) vs stderr: −log10 p correlation | each other | ≥ 0.99 |
| METAL samplesize (plain) vs stderr: lead loci | each other | every lead locus with p < 5e-9 within 500 kb of one in the other run (weaker ones listed) |

**Found by the dry run.** (1) A BETA of exactly 0 negated by an allele switch was written as −0.0, which METAL's
sample-size scheme counted as a negative effect (135 HERMES variants), shifting its overlap-corrected weights:
harmonise_liftover and the meta-analysis loader now write it as 0. (2) GWAMA's own p-values drift from the exact
normal tail as Z grows (0.046 in −log10 p at p ≈ 1e-50; METAL stays within 0.0002), so the cross-program p check
covers p > 1e-5 and the drift is reported as a note. (3) With the overlap correction on, METAL bins variants by total
weight; with per-variant N (HERMES) most bins hold one variant, so the correction is erratic. The cross-scheme check
therefore uses a plain sample-size run. (4) Three lead loci with p ≈ 4e-8 were significant in one scheme only, as
two weightings can put a borderline locus either side of 5e-8: the lead-locus check covers loci with p < 5e-9.
