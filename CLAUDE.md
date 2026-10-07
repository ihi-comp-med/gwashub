# GWASHub: workspace guide

GWASHub is a web portal where HERMES Consortium cohorts upload GWAS summary statistics, run them
through a versioned QC pipeline, get a cleaned file and report back, and reviewers meta-analyse the
datasets that pass. This directory is the whole product (one monorepo).

Read before substantial work:
- `README.md`: how to run and test (`./devtools/dev.sh`, `.venv/bin/python -m pytest`)
- `docs/PROGRESS.md`: where it stands: what is built, decisions, known gaps
- `docs/AWS_FULL.md`: the AWS deployment (gwashub-dev): design reasons, deploying, admin commands, production checklist, costs
- `CONTRIBUTING.md`: how consortia add QC steps, meta-analysis methods and images
- `docs/AWS_ACCOUNT_SETUP.md` (accounts), `docs/VALIDATION_PLAN.md` (validation tests)

## Layout

| Folder | Role |
|---|---|
| `web/` | Frontend: Nuxt 4 + PrimeVue 4 SPA; `/api` proxied to :8000 in dev |
| `api/` | Backend web service: FastAPI (`gwashub_api`): projects, roles, datasets, metadata, pipelines, resources, meta-analysis. Tests in `api/tests/` |
| `pipeline/` | Compute that runs as batch jobs (Python package `pipeline`) |
| `pipeline/runner/` | Runs a pipeline (one process per step); `jobspec.py` runs a job spec (locally or as a Batch job, `aws/batch_entry.py`). CLI: `python -m pipeline.runner run …` |
| `pipeline/qc_steps/` | QC steps by owner: `gwashub/` (defaults), `<consortium>/` (added by pull request, see `CONTRIBUTING.md`); one folder per step (script, `step.yaml`, `test_<name>.py`); shared `_lib/`, `templates/` |
| `pipeline/images/` | Images steps and methods run in, by owner: `gwashub/base` (Python), `gwashub/base-r` (R), `gwashub/vcf` (bcftools + liftover, gwas2vcf; x86-64), `gwashub/ldsc` (bulik/ldsc; x86-64), `gwashub/gwama`, `gwashub/metal` (pinned, checksummed releases); consortium images build `FROM` these |
| `pipeline/analyses/` | `meta_analysis/<owner>/<method>/` (`analysis.yaml` + script): `gwashub/gwama` (default), `gwashub/metal`, consortium methods by PR; shared `_metalib/` (inputs, `publish.py` post-processing) |
| `pipeline/tests/` | Runner, grouping, meta-analysis, resource catalogue tests |
| `resources/` | Reference files: `catalog.yaml` (source, version, licence, which steps use each), `build/*.sh` download/build scripts, `README.md`; built files go to `local/resources/` and are uploaded as shared resources |
| `practice/` | Synthetic files for the Practice project (`api/gwashub_api/practice.py`), with `files.json` (what to enter for each) |
| `client/` | `gwashub_upload.py`: the bulk upload script (standard library only; served by the site with its address filled in) and its tests |
| `images/app/` | The site image (API and worker) |
| `infra/` | AWS CDK app (Network, Data, App stacks); see `docs/AWS_FULL.md` |
| `devtools/` | `dev.sh`, `build_images.sh`, `synth.py` (synthetic data and references), `seed.py`, `e2e.py`, `run_demo.py`; AWS: `aws_cdk_deploy.sh` (deploy), `aws_admin.sh` (admin commands), `aws_upload_resources.py` (reference files to S3), `aws_db_tunnel.sh`, `aws_bookmarks.py` |
| `docs/` | `PROGRESS.md`, AWS guides, `VALIDATION_PLAN.md`; `images/` (posters shown in the README) |
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
  sharing an image run as one Batch job (sized by the steps' `compute:`, x86-64 images on their own queue);
  a new job starts where the image changes. Locally, steps run in their images too (`GWASHUB_STEP_RUNNER=docker`).
- **Default QC = the HERMES 3 DCM pipeline** (from `nicksunderland/hermes3_dcm` on GitHub, commit ff35109): `parse` (the only step that
  reads the upload) → `filter` → `harmonise_liftover` (the edited gwas2vcf + bcftools liftover; all VCF work inside it)
  → `eaf_check` → `ldsc` (bulik/ldsc) → `stats_summary` → `plots` → `qc_report`. Every step after `parse` takes the
  GWASHub format in and writes it out (`_lib/common.py` `STANDARD`); GRCh38 with EA = ALT after `harmonise_liftover`.
- **Before/after numbers:** the runner itself profiles every run's upload (via the column mapping) and cleaned
  file (`_lib/profile.py`, `result.json` `profile`) and counts rows before/after each step; no bookend steps.
- **Step contract:** `--input --resources --params --log`; write a JSON step log with `verdict`
  pass/warn/fail; exit 0 = it ran. Extra context via optional env vars (`GWASHUB_ARTIFACT_DIR`,
  `GWASHUB_STEP_LOGS`, `GWASHUB_RUN_DIR`). Steps never touch the database or the network.
- **Permissions are enforced server-side** (`api/gwashub_api/auth.py`): uploaders open only their own
  datasets; reviewers do everything else in their project, including managing members; platform admins
  (`is_admin`) can also create projects and upload resources, but need a reviewer role for
  reviewer actions (the creator of a project is its first reviewer); non-members get 404. Personal API tokens (for the
  upload script) are accepted only by endpoints that use `auth.upload_caller`; everything else is cookie-only.
- **Local vs AWS** is configuration only (`config.py`): storage local/S3, database SQLite/Postgres
  (Alembic migrations in `api/migrations`), jobs threads/Batch, sign-in dev (pick a user)/Cognito.
- **Meta-analysis methods:** `<command> --inputs --params --out --log`; write `meta_results.tsv.gz`
  (`chr pos ea oa p n_studies` + `beta se` or `z`); GWASHub's `_metalib/publish.py` adds lead loci (PLINK 2 clumping against 1000G panels, `_metalib/loci.py`, as `define_loci_plink.R`), forest data,
  plots and summary. Each runs as one job in its own image. Any project can use any method.
- **Typed params** (`pipeline/runner/params.py`): `params:` in step.yaml and analysis.yaml is a list of
  `{key, label, type, default, min, max, options, help}`; the website draws forms from it and the API validates.
- **Trait types**: each project phenotype is case/control (default) or quantitative (`Project.phenotype_types`,
  `services.trait_type`). Case/control datasets give cases and controls (`n_total` = their sum); quantitative ones
  give `n_total` only and BETA effects. QC (parse N fill, λ1000) and meta-analysis (fallback N) use these counts.
- **Study metadata**: every field (HERMES and SGC) is defined once in `api/gwashub_api/metadata_schema.py`,
  each section per cohort or per dataset; projects choose which are shown and required (Project.metadata_config).
  Datasets inherit their **cohort's** values and may override them (`metadata_schema.effective`).
- **Named pipelines:** a project has several QC pipelines, each versioned, one the default; a dataset follows the
  default unless a reviewer pins it (`Dataset.pipeline_id`). Changing pipeline only advises a re-run (out of date =
  latest run on another version). A step always runs in its step.yaml image.
- **Custom metadata fields** (`Project.metadata_custom`) sit beside the built-in ones; hidden, never deleted.
- **Uploads wait as pending:** every upload (wizard or script) lands as "Pending QC"; the uploader or a reviewer starts QC.
- **Bulk upload:** `client/gwashub_upload.py` (standard library only) + an Excel manifest (`manifest_xlsx.py`: template
  and reader) + `routers/bulk.py` (parse, check every row with the wizard's own rules in `datasets.check_upload`,
  status). Nothing guessed; duplicates refused; the script sends nothing unless the whole manifest passes.
- **Practice project** (`practice.py`): created at start-up, every user a reviewer, for the guided tours in the Help menu.
- **Reproducibility:** pipeline versions pin resource versions; each run records the pipeline version
  and the image digests it used.

## Working rules

- Run `.venv/bin/python -m pytest` before and after changes; add tests with new behaviour.
- QC logic belongs in `pipeline/qc_steps/` as scripts; the API and runner stay generic.
- Nothing deployment-specific as literals: settings come from env vars (`api/gwashub_api/config.py`).
- No credentials in code or git. Locally: env vars. In AWS: IAM roles, Secrets Manager, GitHub OIDC.
- The local dev login (`/api/auth/login`, `/api/auth/users`) must never be reachable in production.
- UI copy: plain and specific; no em dashes.
- Guided tours (`web/app/utils/tours.ts`) point at `data-tour="…"` markers: keep a marker when changing its element
  (or update the tour); `api/tests/test_tours.py` checks they match.
