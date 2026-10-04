# GWASHub: workspace guide

GWASHub is a web portal where HERMES Consortium cohorts upload GWAS summary statistics, run them
through a versioned QC pipeline, get a cleaned file and report back, and reviewers meta-analyse the
datasets that pass. This directory is the whole product (one monorepo).

Read before substantial work:
- `README.md`: how to run and test (`./devtools/dev.sh`, `.venv/bin/python -m pytest`)
- `docs/PROGRESS.md`: what is built, decisions taken, known gaps
- `docs/AWS_FULL.md`: the AWS deployment as built (gwashub-dev), deploying, admin commands
- `CONTRIBUTING.md`: how consortia add QC steps and images
- `docs/AWS_DEPLOYMENT.md` (design and production checklist), `docs/AWS_ACCOUNT_SETUP.md` (accounts),
  `docs/PLAN.md` (original plan)

## Layout

| Folder | Role |
|---|---|
| `web/` | Frontend: Nuxt 4 + PrimeVue 4 SPA; `/api` proxied to :8000 in dev |
| `api/` | Backend web service: FastAPI (`gwashub_api`): projects, roles, datasets, metadata, pipelines, resources, meta-analysis. Tests in `api/tests/` |
| `pipeline/` | Compute that runs as batch jobs (Python package `pipeline`) |
| `pipeline/runner/` | Runs a pipeline (one process per step); `jobspec.py` runs a job spec (locally or as a Batch job, `aws/batch_entry.py`). CLI: `python -m pipeline.runner run …` |
| `pipeline/qc_steps/` | QC steps by owner: `gwashub/` (defaults), `<consortium>/` (added by pull request, see `CONTRIBUTING.md`); one folder per step (script, `step.yaml`, `test_<name>.py`); shared `_lib/`, `templates/`, `fixtures/` |
| `pipeline/images/` | Images steps run in, by owner: `gwashub/base` (Python), `gwashub/base-r` (R); consortium images build `FROM` these |
| `pipeline/analyses/` | `meta_analysis/` (inverse-variance fixed / DerSimonian-Laird random effects) |
| `pipeline/tests/` | Runner, grouping, liftover-chain, meta-analysis, S3 tests |
| `images/app/` | The site image (API and worker) |
| `infra/` | AWS CDK app (Network, Data, App stacks); see `docs/AWS_FULL.md` |
| `devtools/` | `dev.sh`, `synth.py` (synthetic data), `seed.py`, `e2e.py`, `run_demo.py`; AWS: `aws_cdk_deploy.sh` (deploy), `aws_admin.sh` (admin commands), `aws_db_tunnel.sh`, `aws_bookmarks.py` |
| `docs/` | Plan, progress, AWS guides, original qc-pipeline README |
| `local/` | Git-ignored local state: `synthetic/` (fake data + resources), `app/` (dev DB, uploads, run outputs), `runs/` (CLI runs) |

The original sources were git clones that have been removed; they remain on GitHub if anything else
needs porting: `nicksunderland/qc-pipeline` (copied into `pipeline/`),
`broadinstitute/data-registry-api` and `broadinstitute/data-registry` (Broad, BSD-3: keep the
copyright notice for any code ported from them). Useful there: liftover worker
(`liftover_docker/`), SGC meta-analysis (`sgc_ma/`), LDSC (`sgc_ldsc/`), HERMES upload form
(`pages/hermes/new.vue`).

## Key architecture

- **Pipelines are lists of steps; each step = script + image + on_fail + params.** Steps live in
  `pipeline/qc_steps/<owner>/<step>/` (key `<owner>/<step>`); consortia add theirs by pull request.
  The runner stages the input and only the resources the step declares. On AWS, consecutive steps
  sharing an image run as one Batch job; a new job starts where the image changes.
- **Step contract:** `--input --resources --params --log`; write a JSON step log with `verdict`
  pass/warn/fail; exit 0 = it ran. Extra context via optional env vars (`GWASHUB_ARTIFACT_DIR`,
  `GWASHUB_STEP_LOGS`, `GWASHUB_RUN_DIR`). Steps never touch the database or the network.
- **Permissions are enforced server-side** (`api/gwashub_api/auth.py`): uploaders open only their own
  datasets; reviewers do everything else in their project, including managing members; platform admins
  (`is_admin`) can also create projects; non-members get 404.
- **Local vs AWS** is configuration only (`config.py`): storage local/S3, database SQLite/Postgres
  (Alembic migrations in `api/migrations`), jobs threads/Batch, sign-in dev (pick a user)/Cognito.
- **Study metadata** fields are defined once in `api/gwashub_api/metadata_schema.py` and drive the
  upload form, the dataset viewer and validation.
- **Reproducibility:** pipeline versions pin resource versions; each run records the pipeline version
  and the image digests it used.

## Working rules

- Run `.venv/bin/python -m pytest` before and after changes; add tests with new behaviour.
- QC logic belongs in `pipeline/qc_steps/` as scripts; the API and runner stay generic.
- Nothing deployment-specific as literals: settings come from env vars (`api/gwashub_api/config.py`).
- No credentials in code or git. Locally: env vars. In AWS: IAM roles, Secrets Manager, GitHub OIDC.
- The local dev login (`/api/auth/login`, `/api/auth/users`) must never be reachable in production.
- UI copy: plain and specific; no em dashes.
