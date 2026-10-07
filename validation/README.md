# Validation

Tests that compare GWASHub with trusted references on the same inputs. The plan, the five tests and their thresholds
are in `docs/VALIDATION_PLAN.md`; this folder holds what each test needs to run and what it found. The results
become the site's **Validation** page.

## Layout

```
validation/
├── README.md          this file
├── sources.yaml       every public GWAS used: where from, which release, checksum, licence, citation, columns
├── _lib/              shared R code (sourced by every test)
│   ├── common.R       paths, reading test.yaml / sources.yaml, the standard columns
│   ├── get_sources.R  download a source and check its checksum
│   ├── subset.R       the shared size recipe (HF loci windows + a seeded random sample)
│   ├── compare.R      join two result tables on chr:pos:alleles, align signs, measure differences
│   └── write_result.R result.json in the format the Validation page reads
└── 01_gwama_default/  one folder per test (more to come)
    ├── README.md      claim, steps, why it matters
    ├── test.yaml      inputs, site settings, thresholds (the comparison and the page both read these)
    ├── 1_prepare.R    sources → the files to upload to the site
    ├── 2_reference.R  the reference analysis (here: GWAMA by hand) on the site's QC'd files
    ├── 3_compare.R    site vs reference → result.json and plot.json
    ├── site_run.yaml  what was done on the site (ids, references, versions), filled in by hand
    ├── manifest.tsv   sha256 and size of every large file the result came from
    ├── result.json    the outcome (status "not_run" until the test has been run)
    └── plot.json      written by 3_compare.R
```

**Large files are not committed.** Inputs, site downloads and reference outputs live in
`local/validation/<test>/{sources,inputs,site,reference}/` (git-ignored). `manifest.tsv` records each one's sha256
so anyone can check they have the same files. When a test is final, archive its `local/validation/<test>/` folder:
privately to `s3://<resources bucket>/validation/<test>/<date>/`, and before the page is public to Zenodo, with the
DOI in `result.json`. Cohort data (the HPC replication test) is never archived publicly.

## Running a test

From the repository root, with R (packages: data.table, yaml, jsonlite; `renv::init()` here once to pin them) and
Docker (the reference GWAMA and METAL run from the site's own images, so the release is the same):

```bash
Rscript validation/01_gwama_default/1_prepare.R     # downloads, subsets, writes local/validation/01_gwama_default/inputs/
# upload the inputs to the site, run QC and the meta-analysis as in the test's README; save downloads to .../site/
# and fill in site_run.yaml
Rscript validation/01_gwama_default/2_reference.R   # writes .../reference/
Rscript validation/01_gwama_default/3_compare.R     # writes result.json, plot.json, manifest.tsv
```

## Conventions

- Scripts are numbered and do one job each; only `3_compare.R` writes the committed files.
- Thresholds live in `test.yaml`, never in R code.
- `result.json` is overwritten on each run, with the run date and versions inside; git history is the run history,
  and a re-run's pull request shows exactly what changed.
- Every site analysis is run twice; the two downloads must have the same sha256 (the re-run check).
