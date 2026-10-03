# Contributing a QC step

GWASHub's QC steps live in this repository. The default steps are in `pipeline/qc_steps/gwashub/`;
consortia add their own in `pipeline/qc_steps/<consortium>/` by pull request. Once merged and deployed,
a step appears in the pipeline editor of every project, and project reviewers can add it to their pipeline.

## 1. The folder

```
pipeline/qc_steps/<consortium>/
  README.md                     who maintains these steps, and what each one does
  <step>/
    <step>.py  or  <step>.R     the script
    step.yaml                   how GWASHub runs it (below)
    test_<step>.py              a test that runs the script on a small table
```

`<consortium>` and `<step>` are lower-case with underscores, e.g. `hcm/sarcomere_filter`. The step's key
in pipelines is `<consortium>/<step>`. Examples: `pipeline/qc_steps/custom_example/hello_world/` (the smallest
possible Python step: a console message, a log message and an image artifact) and `pipeline/qc_steps/hcm/` (R).

```yaml
name: sarcomere_filter               # must match the folder name
title: Sarcomere-variant filter
description: One or two sentences on what it removes or checks.
command: [Rscript, sarcomere_filter.R]   # or [python, my_step.py]
image: gwashub/base-r                # an image from pipeline/images (see 4)
default_on_fail: flag                # block (stop the pipeline) | flag (mark for review) | continue (ignore)
resources: [sarcomere_genes_grch38.bed]  # reference files it needs (project or shared resources)
params:                              # optional defaults, editable per project
  window_kb: 500
always_run: false                    # true only for steps that must run after a block (like the report)
```

## 2. The step contract

The runner calls your script as

```
<command> --input working.tsv --resources <dir> --params params.json --log step_log.json
```

- `--input` is the dataset so far: tab-separated, with the standard columns `chr pos ea oa beta se p`
  (and `eaf n rsid info` when present). Rewrite it in place to remove or change rows.
- `--resources` holds only the reference files your `step.yaml` declares.
- `--params` is JSON: the dataset's details (`build`, `ancestry`, `n_cases`, ...) and, under `"step"`,
  your step's parameters.
- `--log`: write JSON like
  ```json
  {"step": "sarcomere_filter", "verdict": "pass", "metrics": {"rows_removed": 12},
   "messages": ["Removed 12 variants in 2 regions."], "artifacts": [{"type": "table", "path": "removed.tsv", "label": "Removed variants"}]}
  ```
  `verdict` is `pass`, `warn` or `fail`. Artifact files go in `$GWASHUB_ARTIFACT_DIR`; everything written
  there can be viewed and downloaded from the run.
- Exit 0 when the step ran, whatever the verdict. A non-zero exit is reported as a crash.
- Work only with your inputs: no network calls, no database, nothing outside the given paths.

Python steps may import the shared helpers in `pipeline/qc_steps/_lib` (argument parsing, reading the
working file, writing the log); R steps follow the same contract by hand.

## 3. Tests

`test_<step>.py` runs the script on a few rows and checks the output and the log (see
`pipeline/qc_steps/hcm/sarcomere_filter/test_sarcomere_filter.py`; Python steps can use
`_lib.testing.run_step`). Run everything with `.venv/bin/python -m pytest`.

## 4. Images

Steps run in the image named in `step.yaml`. Use an existing one if you can:

| Image | Contents |
|---|---|
| `gwashub/base` | Python 3.12, pandas, numpy, scipy, matplotlib (the default steps) |
| `gwashub/base-r` | `gwashub/base` plus R with data.table and jsonlite |

If your step needs more, add `pipeline/images/<consortium>/<image>/Dockerfile` (plus an optional
`image.yaml` with `title` and `description`). It must start `FROM gwashub/base` or `FROM gwashub/base-r`,
which contain the GWASHub runner, and install only what your step needs:

```dockerfile
FROM gwashub/base-r
USER root
RUN apt-get update && apt-get install -y --no-install-recommends r-cran-ggplot2 && rm -rf /var/lib/apt/lists/*
USER gwashub
```

Images are built for ARM64 (AWS Graviton) and pinned by digest at deploy time; each run records the
image digests it used. Consecutive steps that share an image run together as one job; each change of
image starts another job, which adds a few minutes to a run.

## 5. The pull request

Use the pull request template. Automated checks run every step's tests and build every image. The
consortium's maintainers (listed in `.github/CODEOWNERS`) and a GWASHub maintainer review it. After
it's merged, the next deploy makes the step available.
