## What this changes

<!-- e.g. "Adds hcm/sarcomere_filter: removes variants in sarcomere gene regions" -->

## Checklist for new or changed QC steps

- [ ] Folder is `pipeline/qc_steps/<consortium>/<step>/` with the script, `step.yaml` and `test_<step>.py`
- [ ] The step follows the contract in CONTRIBUTING.md (writes a step log with a verdict, exits 0 when it ran)
- [ ] It works only with its inputs: no network calls, no database
- [ ] Any reference files it needs are listed under `resources:` (or `optional_resources:`), and new ones are described in this PR (source URL, build, licence) for a platform admin to upload
- [ ] `.venv/bin/python -m pytest` passes
- [ ] New image (if any) is built `FROM gwashub/base` or `FROM gwashub/base-r` and installs only what the step needs
- [ ] The consortium's `README.md` lists the step and who maintains it

## Checklist for new or changed meta-analysis methods

- [ ] Folder is `pipeline/analyses/meta_analysis/<consortium>/<method>/` with `analysis.yaml` and the script
- [ ] It writes `meta_results.tsv.gz` with `chr pos ea oa p n_studies` and either `beta se` or `z`, and a log (CONTRIBUTING.md, section 6)
- [ ] Parameters are typed in `analysis.yaml`, with labels and limits
- [ ] Any software it wraps is a pinned release with a checksum, in its own image
- [ ] A test runs it on the synthetic datasets, and `.venv/bin/python -m pytest` passes
