# Skin Genetics Consortium (SGC) steps

Maintainers: Katie Watts (@Katie-Watts), with the GWASHub maintainers (`.github/CODEOWNERS`).

SGC's own code runs here, copied from https://github.com/Katie-Watts/SGC (each folder's `SOURCE.md` says which
files, from which commit). `qc_munge.R` has two minor edits, marked in it and listed in `qc_munge/SOURCE.md` (loading
MungeSumstats' header map with `data()`, and a `--chain` option so the liftover doesn't download); the rest is as SGC
wrote it. GWASHub code only goes around it: it hands SGC's scripts their input as SGC writes it,
and turns their output into GWASHub's format.

| Step | What it does | SGC's code | GWASHub's code |
|---|---|---|---|
| `sgc/check_build` | Checks the data are on the build declared at upload; stops the run if not | (none) | `check_build/check_build.R` |
| `sgc/qc_munge` | SGC's QC: MungeSumstats with SGC's filters, lifted to GRCh38 | `qc_munge/qc_munge.R` | `qc_munge/gwashub_qc_munge.py` |

The SGC pipeline template (`pipeline/qc_steps/templates/sgc.yaml`): `gwashub/parse` (reads the upload with the column
mapping from the upload wizard) → `sgc/check_build` → `sgc/qc_munge` → GWASHub's summary statistics, plots and report.

SGC's meta-analysis is `pipeline/analyses/meta_analysis/sgc/metal/` (their `METAL_example_single.metal`, unchanged).

Both steps run in `pipeline/images/sgc/mungesumstats`, built by running SGC's `qc_setup.R` (copied unchanged into
that folder).

Not brought in: `sgc_pipeline/` ("Code not for portal", per SGC's README), `QC_MungeSumstats_old_basic.r` and
`run_metal_old.py` (superseded).
