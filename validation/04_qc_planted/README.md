# Test 4: QC catches planted problems

**Claim:** each QC rule removes or fixes exactly the variants it describes and nothing else, harmonisation flips
effects correctly, and each common whole-file mistake is caught and named.

**Why it matters:** QC is where most mistakes in consortium meta-analyses come from. Showing each rule against a known
answer makes the QC report trustworthy, and showing that each classic upload mistake (odds ratios as BETA, the wrong
build, MAF as EAF, linear-scale effects, a file cut short, ...) is stopped or named shows the site catches what a
careful analyst would.

## Two parts

**A. Planted rows.** One clean file (test 1's UKB subset, about 69,000 variants, plus an INFO column of 0.95) with
20 problems planted, each in its own 100 rows (test.yaml `planted`): missing or impossible values, values beyond the
filter limits, indels, duplicates, other chromosomes, a repeated header line, swapped alleles, minus-strand alleles and
impossible positions. (Minus-strand SNPs are expected to be removed, not complemented back: harmonisation is gwas2vcf's,
as in HERMES 3, which switches REF and ALT but doesn't complement.) `2_reference.R` works out, from the inputs alone, what should happen to every row: removed by
which step and rule, or kept with which values. The site's output must account for every row exactly.

**B. Whole-file mistakes.** The same clean file uploaded 15 times: once unchanged (the control: no gotcha may be
named) and once per mistake, each made by one change (test.yaml `cases`). Each must be stopped or named by the expected
step, with the expected verdict and words.

## Steps

1. Run test 1's `1_prepare.R` if not done (this test starts from its `inputs/UKB.tsv`).
2. `Rscript validation/04_qc_planted/1_prepare.R` writes `local/validation/04_qc_planted/inputs/` (`planted.tsv`,
   `planted_key.tsv`, `cases/`) and `uploads.tsv`: what to enter for each upload.
3. `Rscript validation/04_qc_planted/2_reference.R` writes the known answer to `reference/`.
4. On the site, in the `validation` project:
   - Make a QC pipeline named **planted problems**: the default steps, with the filter settings in test.yaml
     `site.planted_pipeline.filter` (keep chromosomes 1-22, X; |BETA| max 20; SE max 10; INFO min 0.8 and missing INFO
     removed; MAF min 0.01; N min 1000; strand-ambiguous SNPs max MAF 0.4; remove indels; remove duplicates).
   - Upload `planted.tsv` (cohort `planted`), pin it to that pipeline and run QC. Save, to `site/`:
     the QC'd file as `planted_qc.tsv`; from the step outputs, each `removed_rows.tsv.gz` of parse, filter and
     harmonise_liftover as `planted_parse_removed_rows.tsv.gz`, `planted_filter_removed_rows.tsv.gz` and
     `planted_harmonise_liftover_removed_rows.tsv.gz`; and the dataset's record, `<site>/api/datasets/<id>` opened in
     the signed-in browser, as `planted.json`.
   - Upload each file in `cases/` (cohort = the case id; build and effect type as `uploads.tsv` says, the rest as
     UKB) and run QC with the default pipeline. For `or_as_beta` the wizard suggests "Odds ratios" (its own check
     of the values): switch it back to BETA, so the test reaches parse's check, which guards uploads by script. A case's QC may fail: that is often the expected outcome. Save each
     dataset's record, `<site>/api/datasets/<id>`, as `site/cases/<id>.json`.
5. Fill in `site_run.yaml`.
6. `Rscript validation/04_qc_planted/3_compare.R` writes `result.json`, `plot.json` and `manifest.tsv`; the per-row
   outcome is in `reference/rows_observed.tsv.gz` and the cases in `reference/cases_observed.tsv`.
7. Commit `result.json`, `plot.json`, `manifest.tsv` and `site_run.yaml`; archive `local/validation/04_qc_planted/`
   (see `validation/README.md`) and put its location in `test.yaml` `archive`.

## Checks (thresholds in `test.yaml`)

| Check | Threshold |
|---|---|
| Each planted problem: rows removed by the expected step and rule, or kept with the original values (aligned) | 0 rows wrong |
| Repeated header line removed by parse, reason "repeated header line" | exactly 1 |
| Unplanted rows lost other than by the stated rules or the liftover | 0 |
| Unplanted rows kept with changed values (β, se, EAF, N, aligned to the same allele) | 0 |
| Rows neither in the QC'd file nor in any step's removed rows | 0 |
| Liftover losses vs harmonise_liftover's own counts (unmapped, another chromosome, two ALT alleles) | equal |
| Rows removed by parse and by filter vs expected | equal |
| Each whole-file case: the expected step's verdict and message | caught |
| Control (unchanged file): messages naming a gotcha | 0 |

Not covered here (unit tests only, `pipeline/qc_steps/gwashub/*/test_*.py`): chromosome 25 / XY read as X (the
validation file has no X variants), and alleles coded I/D.
