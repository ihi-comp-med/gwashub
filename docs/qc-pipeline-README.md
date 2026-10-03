# qc-pipeline

Self-service GWAS quality-control (QC) pipelines. A QC run is a sequence of small
**scripts** that each read/transform a working copy of an uploaded GWAS file and emit a
structured verdict; a final report step produces an HTML summary. Which scripts run, and
in what order, is declared by a **pipeline manifest**.

The key idea: **the environment is one shared Docker image; the QC logic is these
scripts.** The image (Python + git + the `orchestrator/` runtime) is built rarely; the
scripts change often. So contributing or customizing a QC pipeline is almost always a
**pure code change to this repo — no image rebuild, no infrastructure change.** Once your
PR is merged, your pipeline is immediately runnable by name.

At run time the platform clones this repo **at a pinned commit**, downloads the input
(and any reference files) from S3, runs the pipeline, and uploads the results. Your
scripts only ever see local files — they never touch S3, a database, or the network.

---

## How a run works (so you know what your script must do)

For a given pipeline, the runtime (`orchestrator/run_pipeline.py`) does this:

1. Copies the uploaded GWAS file to `working.tsv`.
2. Runs each step **in `id` order**, invoking your script as a subprocess:
   ```
   python scripts/<your-script>.py \
     --input <working.tsv> \
     --resources <resources-dir> \
     --params  <params.json> \
     --log     <this-step's-log.json>
   ```
   Each step reads `working.tsv`, may rewrite it in place (a *transform* step) or leave
   it unchanged (a *validation* step), and writes a JSON **step log** to `--log`.
3. Honors each step's `on_fail` (see below).
4. Runs the final `qc_report.py` step, which additionally gets `--step-logs <all prior
   logs>` and `--output-dir <dir>`, and writes `qc_report.html`.
5. Produces the run outputs: `gwas_filtered.tsv` (the final `working.tsv`),
   `qc_report.html`, and `result.json` (the machine-readable roll-up).

---

## What goes in a PR

Most contributions are one to three files:

| You want to… | Add / edit |
|---|---|
| Add or change a QC step | a script under `scripts/<group>/<name>.py` |
| Make a new (or customized) pipeline | a manifest under `pipelines/<name>.yaml` |
| Have a step read a reference file (FASTA, dbSNP, …) | an entry in `resources/<name>.yaml` |
| **Always** | a fixture + a test under `fixtures/` and `tests/` so CI can validate your script |

You do **not** touch `orchestrator/`, the API, or any infrastructure. You do **not** need
AWS credentials — everything a PR needs is validated locally by CI.

---

## 1. The script contract

Put your script under `scripts/<group>/` — e.g. `scripts/consortium_x/maf_filter.py`.
`<group>` is usually a project/consortium name (or `default` for a shared step).

**Every script MUST:**

- Accept exactly these arguments: `--input`, `--resources`, `--params`, `--log`.
  *(The final report step additionally accepts `--step-logs` and `--output-dir`.)*
- Read the working GWAS file from `--input` (tab-separated).
- Read any run parameters it needs from the JSON file at `--params` (e.g. the column
  mapping, case/control counts, ancestry).
- If it transforms the data, **rewrite `--input` in place** with the same header. If it
  only validates, leave `--input` untouched.
- Write a single JSON **step log** to `--log` (schema below).
- Exit `0` when it ran (its judgment is in the step log). A **non-zero exit means the
  script crashed** — the run stops and is marked failed. This is distinct from a `fail`
  *verdict*, which is a valid, deliberate outcome.

**Every script MUST NOT:**

- Import `boto3`, open network connections, or touch a database. Scripts are pure
  functions over local files. (This is what lets CI test them with no AWS, and keeps the
  runtime sandbox tight.) The one sanctioned exception is that a script may
  `from orchestrator.report import ...` — the `orchestrator` package is on `PYTHONPATH`.
- Read or write files outside `--input`, `--resources`, `--log` (and, for the report step,
  `--output-dir`).

### The `step log` schema

```json
{
  "step": "maf_filter.py",
  "verdict": "pass",
  "metrics": { "total_rows": 1000000, "dropped_low_maf": 1234 },
  "messages": ["Dropped 1234 variants with MAF < 0.01"],
  "artifacts": []
}
```

- `verdict` — one of `pass`, `warn`, `fail`. Use `fail` for a real QC failure, `warn` for
  a non-fatal issue (e.g. rows were dropped), `pass` otherwise.
- `metrics` — any JSON object of numbers/values you want surfaced in the report and
  `result.json`.
- `messages` — human-readable notes shown in the report.
- `artifacts` — a list of `{type, path, label}`. See *Current limitations* below on file
  artifacts.

A minimal, complete example is the shipped `scripts/default/row_check.py` — read it; it
is the canonical template.

---

## 2. The pipeline manifest

A pipeline manifest (`pipelines/<name>.yaml`) declares the ordered steps. The cleanest way
to customize is a **new named pipeline that `extends` the default** and overrides or adds
only the steps you need — everything else falls through to the default.

```yaml
name: consortium_x
extends: default            # inherit default's steps; override/add by id
environment: default        # which docker/<env>/ image to run in (defaults to `default`)
steps:
  - id: 1
    script: default/row_check.py     # keep the default first step
    on_fail: block
  - id: 3
    script: consortium_x/maf_filter.py   # NEW step inserted before the report
    on_fail: flag
  - id: 5
    script: default/qc_report.py     # keep the report last
    on_fail: flag
```

- Steps run in ascending `id` order. `extends` merges by `id`: a child step with the same
  `id` as the base **replaces** it; a new `id` **adds** a step. Leave gaps in the numbering
  (10, 20, 30…) if you expect to insert steps later.
- `script` is the path **relative to `scripts/`** (`consortium_x/maf_filter.py`).
- `environment` picks which `docker/<env>/` image runs the pipeline. Almost always
  `default`. Only change it if your steps need a genuinely different base image (see
  *Adding a dependency*).

### `on_fail` — what a `fail` verdict does

| value | effect when the step's verdict is `fail` |
|---|---|
| `block` | stop the pipeline immediately; the whole run is marked **failed** |
| `flag` | record the failure, **keep going**; it counts toward the run's overall verdict |
| `continue` | record the failure, keep going, but **ignore** it for the overall verdict |

(These are the only three valid values; anything else fails manifest validation.)

To run your pipeline, a request specifies its `name` (e.g. `"pipeline": "consortium_x"`).

---

## 3. Reference files (optional)

If a step needs a reference file (FASTA, dbSNP VCF, a liftover chain, an LD panel),
declare it in **`resources/default.yaml`** — the one shared resource manifest every
pipeline reads — so the runtime stages it into the `--resources` directory before the
run:

```yaml
# resources/default.yaml
files:
  - key: GRCh38.fa
    bucket: qc-resource-files
    required_by: [consortium_x/maf_filter.py]
```

`required_by` is a list of script paths relative to `scripts/` (matching a pipeline
step's `script:` value exactly). **The runtime only downloads an entry if `required_by`
overlaps the scripts the requested pipeline actually runs** — so adding a reference file
for your pipeline doesn't force every other pipeline to also fetch (or require) it. An
entry with no `required_by` at all is always staged, for the rare case of a resource
genuinely shared unconditionally across every pipeline.

Your script then reads it from `<resources-dir>/GRCh38.fa`. The runtime **fails fast** if a
listed file is required by the running pipeline but missing.

**Do not commit the reference files themselves** — they're large and live in S3. Uploading
them to the resource bucket is done out-of-band (ask a maintainer). The PR only lists the
S3 keys it expects.

---

## 4. Tests & fixtures (required)

Every new or changed script must ship a test so CI can validate it **before merge, with no
AWS**. Add:

- A small fixture GWAS file (a few rows, including any edge cases your step handles) under
  `fixtures/` — or reuse `fixtures/sample_gwas.tsv`.
- A `fixtures/*.json` params file with whatever your step reads from `--params`.
- A test under `tests/` that runs your script against the fixture and asserts the rewritten
  `working.tsv` and the `step log` are what you expect.

`tests/test_row_check.py` is the template: it invokes the script as a subprocess with the
four flags and asserts on the filtered output and the step log. Run the suite locally:

```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

CI runs this on every PR. A red test blocks the merge.

---

## 5. Adding a dependency (the one heavier case)

If your script needs a library that isn't in the environment image, you must also edit the
image — `requirements.txt` and/or `docker/default/Dockerfile` (or add a new
`docker/<env>/Dockerfile` and point your pipeline's `environment:` at it). This is the one
change that requires the environment image to be **rebuilt and republished** (a maintainer
step) before your pipeline can run in production. Call it out clearly in your PR so a
maintainer knows an image rebuild is needed. Script-only changes never need this.

---

## The PR & review process

1. Open a PR to this repo with your script(s), manifest, resource entries, and test(s).
2. CI builds and runs the test suite against your fixtures — **no cloud credentials are
   used**. This is both the correctness gate and the safety gate.
3. A maintainer reviews and merges. (Review is a real human gate; new contributors are
   vetted before their code is trusted to run.)
4. On merge, your pipeline is **immediately usable** — a run just requests it by `name`.
   No API or UI change is needed.
5. Each run **pins the exact commit** it ran, so results are reproducible and you can
   re-run an old pipeline version later.

## Current limitations (be aware)

- **File artifacts beyond the report aren't uploaded yet.** A run currently surfaces
  `gwas_filtered.tsv`, `qc_report.html`, and `result.json`. A step can list `artifacts` in
  its step log (they appear in `result.json` and the report), but if your step writes its
  own file (e.g. a plot image), that file isn't uploaded today. For now, put summary values
  in `metrics` (they render in the report). Richer per-step artifact upload is on the
  roadmap.
- **The report step is matched by filename** (`qc_report.py`). Keep the final report step
  named that, and keep it last.

## Repository layout

```
docker/default/Dockerfile     # the environment image (rebuilt rarely; maintainers)
orchestrator/                 # the platform runtime — do NOT edit in a QC-content PR
scripts/<group>/*.py          # QC step scripts  ← you add here
pipelines/*.yaml              # pipeline manifests ← you add here
resources/*.yaml              # reference-file manifests ← you add here
fixtures/                     # test inputs        ← you add here
tests/                        # tests              ← you add here
```
