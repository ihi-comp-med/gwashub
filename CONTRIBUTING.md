# Contributing QC steps and meta-analysis methods

Consortia add their own QC steps (sections 1 to 5) and meta-analysis methods (section 6) by pull request.

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
params:                              # optional; reviewers can change them per project (see below)
  - {key: window_kb, label: Window around each gene (kb), type: integer, min: 0, max: 5000, default: 500,
     help: Variants this close to a listed gene are removed too}
always_run: false                    # true only for steps that must run after a block (like the report)
```

`params` is a list of typed fields. The pipeline editor draws a form from it, and GWASHub rejects values of the
wrong type or out of range when a pipeline is saved:

| Field | Meaning |
|---|---|
| `key` | the name your script reads (lower case, underscores) |
| `label`, `help` | what the form shows |
| `type` | `number`, `integer`, `boolean`, `select` (with `options: [a, b]`) or `text` |
| `default` | used unless a project changes it |
| `min`, `max` | optional limits for numbers |

The older form, a plain mapping such as `params: {window_kb: 500}`, still works (types come from the defaults).

## 2. The step contract

The runner calls your script as

```
<command> --input working.tsv --resources <dir> --params params.json --log step_log.json
```

- `--input` is the dataset so far: tab-separated, with the standard columns `chr pos ea oa beta se p`
  (and `eaf n rsid info` when present). Rewrite it in place to remove or change rows.
- `--resources` holds only the reference files your `step.yaml` declares: `resources:` are required (the run
  fails without them), `optional_resources:` are staged when available (the step must cope without them, as
  `gwashub/ldsc` does). Reference files are uploaded by a platform admin, so list any new ones in the pull
  request (source URL, build, licence).
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

## 6. Contributing a meta-analysis method

Meta-analysis methods live in `pipeline/analyses/meta_analysis/<owner>/<method>/`. GWASHub's own are in
`gwashub/`: `ivw` (GWASHub's inverse-variance method, the default), `gwama` (a wrapper around GWAMA 2.2.2) and
`metal` (a wrapper around METAL). A consortium adds its own in `pipeline/analyses/meta_analysis/<consortium>/`;
once merged and deployed, every project can choose it when starting a meta-analysis. The smallest example is
`custom_example/custom_meta_analysis` (a weighted Z-score method in about 40 lines).

```
pipeline/analyses/meta_analysis/<consortium>/
  README.md                       who maintains these methods, and what each one does
  <method>/
    analysis.yaml                 how GWASHub runs it (below)
    <method>.py                   the script (or a wrapper around other software)
```

```yaml
name: custom_meta_analysis           # must match the folder name
title: Weighted Z-score (example)    # shown in the method picker
description: One or two sentences on what it does and when to use it.
reference: Stouffer et al. 1949      # citation shown with the results
command: [python, stouffer.py]
image: gwashub/base                  # an image from pipeline/images (section 4)
params:                              # typed fields, as for QC steps (section 1)
  - {key: weighting, label: Weighting, type: select, options: [sqrt_n, equal], default: sqrt_n}
  - {key: min_studies, label: Minimum datasets per variant, type: integer, min: 1, default: 2}
```

The method is called once per meta-analysis, in the method's folder, as

```
<command> --inputs inputs.json --params params.json --out OUT_DIR --log log.json
```

- `inputs.json` lists the datasets: `[{"id", "label", "path", "n_cases", "n_controls", "ancestry", "sex"}]`. Each
  `path` is a QC'd file: tab-separated, GRCh38, aligned to the reference (effect allele = ALT), with
  `chr pos ea oa beta se p` and, when present, `eaf n rsid info`.
- `params.json` holds the method's own parameters, already checked against `analysis.yaml`.
- Write `OUT_DIR/meta_results.tsv.gz` (tab-separated, gzipped) with one row per variant and the columns
  `chr pos ea oa p n_studies` plus either `beta se` or `z`. Optional: `rsid eaf n i2 het_p direction`.
- Write `--log` as JSON: `{"verdict": "pass", "metrics": {}, "messages": [], "studies": []}`.
- Exit 0 when it ran. No network calls, no database.

GWASHub then does the rest for every method: lead loci (with the project's significance threshold and window),
forest plots, Manhattan and QQ plots, λGC, the summary, `datasets.tsv` (the datasets in direction-string order
with how many variants each filter removed) and a `README.txt` describing the columns. If your log's `studies`
entries carry counts such as `variants_in`, `removed_<reason>` and `variants_used` (as `_metalib.common.load_study`
does), they appear there. Python methods may use the helpers in
`pipeline/analyses/meta_analysis/_metalib/common.py` (reading the inputs with MAF and genomic-control filters,
variant IDs, writing results and the log).

To wrap other software, add an image for it (section 4) that downloads a pinned release and checks its
checksum, as `pipeline/images/gwashub/gwama/` and `pipeline/images/gwashub/metal/` do, and have your script
write that program's input files, run it and convert its output to `meta_results.tsv.gz`.

Add a test in `pipeline/tests/` that runs the method on the synthetic datasets (see
`test_weighted_z_example_method` in `pipeline/tests/test_meta_analysis.py`), and list the method in the pull
request template's checklist.

