# GWASHub

Upload GWAS summary statistics, QC them with a transparent, reproducible pipeline, review the
results, and meta-analyse across cohorts. A HERMES Consortium resource.

The same code runs **locally** (synthetic data, SQLite, a local folder for files, jobs in threads,
pick-a-user sign-in) and **on AWS** (S3, Postgres, AWS Batch, Cognito sign-in with MFA, CloudFront).

![How GWASHub works](docs/images/GWASHub-how-it-works.png)

![The GWASHub stack](docs/images/GWASHub-stack.png)

| Doc | What's in it |
|---|---|
| `docs/PROGRESS.md` | Where it stands: what's built, decisions, known behaviour, what's not done |
| `docs/AWS_FULL.md` | **The AWS deployment**: why it's built that way, what runs where, deploying, admin commands, the console, costs |
| `docs/PRODUCTION_ROADMAP.md` | **The road to production**: the domain plan, the tasks in order (how, effort, cost), go-live checks, running it, costs |
| `docs/AWS_ACCOUNT_SETUP.md` | The AWS accounts: what exists, what's open, how to rebuild them |
| `docs/AWS_CREDITS_PROPOSAL.md` | The AWS Cloud Credit for Research proposal (October 2026): USD 7,500 for production's first year |
| `docs/VALIDATION_PLAN.md` | The four validation tests and their results (`validation/` holds them; the site's Validation page shows them) |
| `CONTRIBUTING.md` | How a consortium adds its own QC step, meta-analysis method (and image) by pull request |

## Quick start (local)

```bash
./devtools/dev.sh --fresh   # first time: venv, pipeline images, synthetic data, seeded DB, 14 demo uploads, 3 meta-analyses
./devtools/dev.sh           # afterwards
```

QC steps run in their Docker images locally too (`GWASHUB_STEP_RUNNER=docker`): the default pipeline needs R,
bcftools, gwas2vcf and LDSC, which live in the images. `dev.sh` builds any image that's missing
(`./devtools/build_images.sh`, about 15 minutes the first time; the two x86-64 images build under emulation on Apple silicon).

Open http://localhost:3000 and sign in as any listed user (local mode has no passwords):

| User | HERMES 3 | HCM |
|---|---|---|
| demo@gwashub.org (platform admin) | reviewer | uploader |
| ama@example.org | reviewer | – |
| jonas@example.org, mei@example.org | uploader | – |
| tom@example.org | – | reviewer |
| noor@example.org | – | uploader |

Uploaders open only their own datasets; reviewers see everything in the project, review QC, run
meta-analyses and manage the pipeline and members; platform admins can also create projects.

## Bulk upload

Many files at once, with their metadata: download `gwashub_upload.py` from the site's Account page (it is
`client/gwashub_upload.py` with the site's address filled in), make an API token there, then:

```bash
export GWASHUB_TOKEN=gwh_...                        # locally also: export GWASHUB_URL=http://localhost:3000
python3 gwashub_upload.py template manifest.xlsx     # an empty manifest workbook: README sheet + Manifest sheet
python3 gwashub_upload.py check manifest.xlsx         # every problem, by row; uploads nothing
python3 gwashub_upload.py upload manifest.xlsx        # uploads (resumes if interrupted); datasets wait as Pending QC
```

Design and decisions: `docs/BULK_UPLOAD_PLAN.md`.

## Layout

```
web/                 frontend: Nuxt 4 SPA (PrimeVue 4); /api proxied to :8000 in dev
api/                 backend: FastAPI app (gwashub_api), Alembic migrations (api/migrations), tests
pipeline/            compute that runs as jobs
  runner/              runs a pipeline (one process per step); jobspec.py runs a job spec locally or as a
                       Batch job (aws/batch_entry.py)
  qc_steps/            QC steps by owner: gwashub/ (defaults), <consortium>/ (added by pull request);
                       one folder per step (script, step.yaml, test_<step>.py); shared _lib/, templates/
  images/              images steps and methods run in, by owner: gwashub/base (Python), gwashub/base-r (R),
                       gwashub/vcf (bcftools + gwas2vcf), gwashub/ldsc (bulik/ldsc), gwashub/gwama, gwashub/metal
                       (the programs, built from pinned, checksummed releases)
  analyses/            meta_analysis/<owner>/<method>/ (analysis.yaml + script): gwashub/gwama (default),
                       gwashub/metal, custom_example/custom_meta_analysis; shared _metalib/
  tests/               runner, image grouping, meta-analysis, S3 tests
resources/           reference files: catalog.yaml (where each comes from), build/*.sh (download + build), README.md
client/              gwashub_upload.py: the bulk upload script (standard library only) and its tests
practice/            synthetic files for the site's Practice project (files.json: what to enter for each)
images/app/          the site image (API and worker)
infra/               AWS CDK app: Network, Data and App stacks (docs/AWS_FULL.md)
devtools/            dev.sh, build_images.sh, synth.py (synthetic data), seed.py, e2e.py, run_demo.py; AWS: aws_cdk_deploy.sh,
                     aws_admin.sh, aws_upload_resources.py, aws_db_tunnel.sh, aws_bookmarks.py
docs/                PROGRESS (where it stands), AWS guides, validation plan, bulk upload design; images/ (posters)
local/               git-ignored: synthetic/ (fake data + resources), app/ (dev DB, uploads, runs), runs/
```

## Tests

```bash
./devtools/build_images.sh     # once (tests that need an image skip without it)
.venv/bin/python -m pytest      # QC steps, runner and grouping, meta-analysis, S3 (mocked), API incl. permissions
GWASHUB_TEST_DB_URL=postgresql+psycopg://user:pw@localhost:5432/db .venv/bin/python -m pytest api/tests/test_api.py   # same API tests on Postgres
```

## The step contract

Every step (default or consortium, any language) is called as

```
<command> --input working.tsv --resources DIR --params params.json --log step_log.json
```

and writes `{"verdict": "pass|warn|fail", "metrics": {}, "messages": [], "artifacts": []}` to `--log`
(each artifact `{type, path, label}`, optionally `rows` and `description`; removed rows go in `removed_rows.tsv.gz`
with a `reason` column).
It may rewrite `--input` in place, keeping the GWASHub format (`chr pos ea oa beta se p eaf` + optional
`n ncase ncontrol rsid info`); only the first step, `gwashub/parse`, reads the upload as received. Extra files go in `$GWASHUB_ARTIFACT_DIR`; other optional context:
`GWASHUB_STEP_LOGS`, `GWASHUB_RUN_DIR`. Exit 0 = it ran; non-zero = crashed. See `CONTRIBUTING.md`.
