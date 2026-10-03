## What this changes

<!-- e.g. "Adds hcm/sarcomere_filter: removes variants in sarcomere gene regions" -->

## Checklist for new or changed QC steps

- [ ] Folder is `pipeline/qc_steps/<consortium>/<step>/` with the script, `step.yaml` and `test_<step>.py`
- [ ] The step follows the contract in CONTRIBUTING.md (writes a step log with a verdict, exits 0 when it ran)
- [ ] It works only with its inputs: no network calls, no database
- [ ] Any reference files it needs are listed under `resources:` and are available as project or shared resources
- [ ] `.venv/bin/python -m pytest` passes
- [ ] New image (if any) is built `FROM gwashub/base` or `FROM gwashub/base-r` and installs only what the step needs
- [ ] The consortium's `README.md` lists the step and who maintains it
