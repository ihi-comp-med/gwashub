# GWASHub — build plan

Status: draft, 2026-10-02. Owner: Nick Sunderland.

> **Update 2026-10-03 (evening):** Phases 1–2 and the meta-analysis from Phase 3 are built, tested and
> deployed to AWS (gwashub-dev); see `docs/PROGRESS.md` and **`docs/AWS_FULL.md`** (the deployment as
> built). This plan is kept for its reasoning; where it differs from what was built: steps live in
> `pipeline/qc_steps/<owner>/` and ship in the images (no runtime checkout, no git-repo custom steps;
> consortia contribute by pull request, see `CONTRIBUTING.md`); images are in `pipeline/images/<owner>/`;
> CDK is in Python; per-step images run as image groups chained with Batch job dependencies rather than
> Step Functions.

Goal: ship the GWASHub **default offering** on infrastructure we own, extracting only
what we need from the Broad `data-registry` / `data-registry-api` repos, and running on our
own AWS account instead of the Broad's.

---

## 1. What exists today

### 1.1 The four folders

```
~/git/gwashub/
├── gwashub/             ← empty; the new product lives here
├── qc-pipeline/         ← ours. QC scripts/manifests + orchestrator (558 LOC, clean, tested, CI)
├── data-registry-api/   ← Broad. FastAPI monolith, ~14k LOC in dataregistry/api + ~12 Batch job dirs
└── data-registry/       ← Broad. Nuxt 3 SPA (ssr:false), PrimeVue, one page tree per project
```

### 1.2 `data-registry-api` — what's in the monolith

It serves ~8 unrelated Broad projects from one process. Roughly:

| Area | Files | Relevance to GWASHub |
|---|---|---|
| **QC runs (drives `qc-pipeline`)** | `api/qc.py`, `api/qc_runner.py`, `QCRun`/`QCStepResult` models, 2 migrations, `qc_batch/qc_pipeline_batch.yml`, `tests/test_qc_*` | **Core.** Port first. |
| GWAS upload / column mapping | HERMES routes in `api/api.py`; SGC `/sgc/gwas-upload-url`, `/sgc/confirm-gwas-upload`, `/hermes/suggest-column-map` | Core. Port the pattern (presigned upload + column map), not the project-specific code. |
| GWAS validation | `hcm_batch/validate_gwas.py`, `sgc_batch/validate_gwas.py`, `hermes_docker/hermes_qc.py` (wraps an R package) | Mine for QC checks → reimplement as `qc-pipeline` scripts. |
| Liftover | `liftover_docker/gwas_liftover.py` (748 LOC, UCSC liftOver, strand flips) | Default-offering step. Port into `qc-pipeline` as a script + chain files as resources. |
| QC plots (Manhattan/QQ) | `sgc_qc_plots/` | Default-offering step (report). Port into `qc-pipeline`. |
| Meta-analysis | `sgc_ma/` (Python), `hcm_ma/`, HERMES `aggregator_docker` (Scala, `dig-aggregator-methods`) | Phase 3. Use `sgc_ma` (Python); skip the Scala aggregator. |
| LDSC | `sgc_ldsc/` | Phase 3. |
| Auth | `get_current_user` (JWT cookie/bearer), `jwt_utils.py`, Google OAuth, external user service `users.kpndataregistry.org` | Replace (see §4). |
| PEG, CALR, MSKKP, KPN CMS, bioindex, PubMed | many | **Ignore.** |

How a QC run works today (`qc_runner.py`):
`POST /api/qc/run` → insert `qc_run` row → FastAPI `BackgroundTask` calls `batch.submit_job`
→ **polls `describe_jobs` every 10 s in the API process** → reads `result.json` from S3 →
writes `qc_step_result` rows. Weakness: an API restart orphans in-flight runs.

### 1.3 `data-registry` — frontend

Nuxt 3, client-only SPA, PrimeVue + Tailwind/PrimeFlex, Pinia. Page trees per project
(`pages/hermes`, `pages/sgc`, `pages/hcm`, …). **There is no UI for `qc-pipeline` runs.**
Useful donors: the HERMES/SGC upload + column-mapping forms, the `hermes/qc/[id].vue` result
page, SGC QC-plot pages, `useAxios` composables.

### 1.4 `qc-pipeline` — the manifest

Its README is the design manifest for GWASHub's processing model: shared environment image,
scripts as pure functions with a fixed CLI contract, YAML pipeline manifests with `extends`
and `on_fail: block|flag|continue`, resource manifest with `required_by` staging, runs pinned
to a commit, contributor workflow via PR + CI with no AWS. Today the `default` pipeline is
only `row_check.py` → `qc_report.py`. **This is where most of the default-offering work goes.**

---

## 2. Define the default offering (MVP)

A logged-in user can:

1. **Create a study** (name, trait, ancestry, build, N cases/controls or N, description).
2. **Upload** a summary-stats file (`.tsv/.txt/.csv`, optionally `.gz`) directly to S3 via
   presigned (multipart) upload.
3. **Map columns** (CHR, POS, EA, OA, BETA/OR, SE, P, EAF, N, rsID) with auto-suggestion.
4. **Run the `default` QC pipeline** — one click.
5. **See results**: per-step verdicts + metrics, overall pass/warn/fail, HTML report with
   Manhattan + QQ, λGC; **download** the harmonised file.
6. **See history** of their runs, each pinned to a pipeline commit.

Proposed `default` pipeline steps (each a folder under `steps/` with script, `step.yaml` + test):

| id | step | on_fail | source to mine |
|---|---|---|---|
| 10 | `row_check` — parse, required columns present, numeric types | block | exists |
| 20 | `harmonise_columns` — rename to standard schema, OR→BETA, normalise CHR (chr1/23/X) | block | HERMES column map, `sgc_ma/harmonize.py` |
| 30 | `value_ranges` — P∈(0,1], SE>0, EAF∈[0,1], drop NA/inf, dedupe variants | flag | `sgc_batch/validate_gwas.py`, `hcm_batch/validate_gwas.py` |
| 40 | `alleles` — valid ACGT, flag palindromic/indels, uppercase | flag | same |
| 50 | `liftover` — to GRCh38 if needed (chain files as resources) | flag | `liftover_docker/gwas_liftover.py` |
| 60 | `reference_check` — allele match/flip vs reference (e.g. 1000G/gnomAD freq) | flag | new / `hermes_qc` logic |
| 70 | `stats_summary` — λGC, N variants, Z vs P consistency | flag | `sgc_qc_plots/computations.py` |
| 80 | `plots` — Manhattan + QQ PNGs | continue | `sgc_qc_plots/plots.py` |
| 90 | `qc_report` | flag | exists |

Prerequisite in the runner: **upload per-step file artifacts** (listed as a current
limitation) so plots reach S3 and the report. Also make the report embed them.

Out of scope for MVP: meta-analysis, LDSC, public dataset browsing, consortium/group
permissions, custom pipelines from the UI.

### 2.1 Projects, roles and per-project pipelines (requirements, 2026-10-02)

Everything is scoped to a **project**. A user can belong to several projects, with one role in each.

- **After login** → the projects list (or straight into the project if they only have one).
- **Project overview:** description, members (grouped by role), and a dataset summary of the raw
  files submitted, tabulated by **ancestry × sex** against **passed QC / for review / failed**
  (plus processing). Each count links into the dataset viewer, pre-filtered.
- **Dataset viewer:** a filterable table. Clicking a dataset opens a **right-hand panel (~75% width)**
  listing every processing step (verdict, messages, metrics, duration), plus provenance
  (scripts commit, image digest), plots and downloads.
- **Pipeline settings (per project):** the GitHub repo + ref (branch/tag/commit, resolved to a commit),
  the pipeline manifest name, and the **Docker Hub image + tag** (resolved to a digest). Change history.
- **Roles:**
  - *Uploader:* can upload, and can see project overviews and summary counts. Can open or download
    **only their own** datasets. Settings are read-only.
  - *Reviewer:* can see and download everything, edit pipeline settings, manage members, and
    approve or reject datasets that are "for review".
- **Dataset status** (`passed | review | failed | processing`) is separate from the QC verdict.
  A QC `warn` lands a dataset in `review`, and a reviewer's decision moves it to `passed` or `failed`.

Backend implications:
- New tables: `project`, `project_member(user_id, project_id, role)`, `project_pipeline_settings`
  (+ history), `dataset` (project_id, uploader, cohort, phenotype, ancestry, sex, build, N, status),
  `qc_run` → FK to dataset, `review_decision`.
- **All permission checks are server-side** in one dependency (`require_project_role`, plus a
  `can_access_dataset` check on every dataset/file/run endpoint and presigned URL). The UI only hides buttons.
- **Per-project images:** AWS Batch can't override a job definition's image at submit time, so we
  register one job definition per (project, image digest), created or reused when settings are saved.
  Always run by **digest**, never a mutable tag.
- **Docker Hub:** anonymous pulls are rate-limited. Use authenticated pulls (Hub token in Secrets
  Manager), or mirror approved images into our own ECR on save (recommended: also allows an
  image-scanning gate).
- **Arbitrary repos and images are arbitrary code in our AWS account.** Only reviewers may set
  them. The job role stays minimal: read the input and resources, write only `runs/<run_id>/`, no DB,
  no secrets. Consider an allowlist of GitHub orgs and Docker Hub namespaces.
- Private GitHub repos need a per-project read token (or a GitHub App installation).

### 2.2 Pipeline builder: design for v1 (decided 2026-10-02)

A project's pipeline is an ordered list of **entries** `(step, image, on_fail)`, built with
dropdowns in the UI and stored as versioned JSON. Saving creates a new version that is never edited
afterwards, and each dataset records the version it ran with.

- **Step** = a script at a pinned commit of a GitHub repo + a command (`Rscript {script}`,
  inferred from the file extension) + a default image + a default `on_fail`.
  - **Default steps** live in the `gwashub` monorepo under `steps/<name>/` (see §3). They're available in every project.
  - **Custom steps** belong to a project. A reviewer enters repo + branch/tag/commit + script path
    + image. The ref is resolved to a commit and the image to a digest when saved, and nothing
    changes until someone clicks "update".
- **Fixed contract** (unchanged): `--input --resources --params --log` + a JSON step log. The
  whole repo is cloned, so helper files are available to the script.
- **Images:** a curated list (`gwashub/base`, `gwashub/base-r`, `gwashub/liftover`, …) plus any
  Docker Hub image typed in. The image doesn't need anything GWASHub-specific inside it; it just
  needs the interpreter and packages the script uses.
- **Execution:** one Batch job per step, chained with `dependsOn`. Each job has three containers
  sharing a scratch volume: **stage** (our image: clone the repo, fetch the input from S3) →
  **step** (the user's image) → **publish** (our image: upload the result and step log). No Step
  Functions or EFS in v1. ⚠ Check that Batch multi-container jobs run on Fargate; if not, use an
  EC2 compute environment.
- **"Test step"** runs the step on a 2,000-row sample file in the chosen image and shows the
  console output and step log before saving.
- **Builder checks** (warnings only): no step chosen, row_check not first, qc_report not last,
  duplicates, steps that need harmonised columns placed before harmonise, an R script in a Python
  image (or the reverse).
- **Resources (reference files)** are staged read-only into a step's `--resources` directory. Each
  step declares the resource keys it needs, and only those are downloaded for it.
  - *Shared* resources (chain files, GRCh38 FASTA, 1000G/gnomAD frequencies, dbSNP map) are
    curated by GWASHub admins, are available to every project, and are preferably imported from
    the official URL.
  - *Project* resources are added by that project's **reviewers only**, via a presigned upload
    (cap ~50 GB) or a server-side import from a URL (preferred, because the source is recorded).
    The reviewer must declare that the file contains no individual-level data and may be used by
    the project.
  - **Uploaders can't add resources.** They can send a request to the reviewers. Open upload would
    mean unreviewed files of unknown origin, possible individual-level data, uncontrolled storage
    costs, and a route for one project member to change what another's pipeline reads.
  - Resources are checksummed (sha256) and immutable. Re-using a name creates a new version, and a
    pipeline version pins the resource versions it was saved with. Builder warns if a step needs a
    resource that doesn't exist.
- **Later, without changing steps:** merge adjacent steps that use the same image into one job,
  use EFS between steps, move S3 access out of the step container, per-step parameter forms,
  `requires`/`produces` ordering checks, allowlists, image scanning.

The clickable mock of all of this is in `web/` (mock data in
`app/composables/useMockData.ts`, permission logic in `app/composables/useMockAuth.ts`).

---

## 3. Target code structure

**Decision (2026-10-02): one monorepo.** The whole default offering lives together in `gwashub`,
including the default QC steps, in a plain `steps/` folder (not a git submodule). `qc-pipeline`'s
contents move in: scripts → `steps/`, `orchestrator/` → `runner/`, `docker/` → `images/`. After
that, the `qc-pipeline` repo is archived.

```
gwashub/
├── api/              FastAPI, SQLAlchemy, Alembic   (ported/trimmed from data-registry-api)
│   ├── gwashub/      app package: routers/{auth,projects,datasets,pipelines,resources}, models, db, s3, config
│   ├── migrations/
│   └── tests/
├── web/              Nuxt SPA, PrimeVue 4            (currently the clickable mock)
├── steps/            the default step library — one folder per step
│   ├── row_check/        row_check.py · step.yaml · test_row_check.py
│   ├── harmonise_columns/
│   ├── …                 (liftover, reference_check, stats_summary, plots, qc_report, …)
│   ├── templates/        default.yaml — the "Default QC" pipeline template
│   └── fixtures/         shared small test inputs (sample_gwas.tsv, params.json)
├── runner/           stage / publish containers + `gwashub run` local CLI (from qc-pipeline/orchestrator)
├── images/           base/, base-r/, liftover/ Dockerfiles for the curated step images
├── infra/            AWS CDK (TypeScript or Python) — all resources as code
├── docs/             this plan, architecture notes, runbooks
├── docker-compose.yml  local dev: MySQL/Postgres + MinIO/LocalStack + api + web
├── LICENSE / NOTICE  (keep Broad BSD-3 notice for ported code)
└── .github/workflows/  test, build, deploy (GitHub OIDC → AWS)
```

How the `steps/` folder works:
- **Default steps are still a "step source".** It's the same code path as custom steps from
  external repos: the source is `github.com/<owner>/gwashub`, the path is `steps/<name>/…`, and it's
  pinned to a commit. The runner does a sparse checkout of just `steps/` at that commit.
- **Releases:** tag step-library releases separately from app releases (e.g. `steps/v2.0.0` vs
  `v1.4.0`). The API config pins which `steps/` tag new pipelines use, and existing pipeline
  versions keep their commit.
- **CI with path filters:** changes to `steps/**` run the step tests (each against `fixtures/`, no
  AWS) and rebuild `images/**` only if those changed. `web/**` and `api/**` run their own jobs.
- **The repo (or at least `steps/`) must be public** for run-time clones, or the runner needs a
  read-only token.
- **Trade-off accepted:** outside contributors to default steps open PRs against the app repo.
  CODEOWNERS on `steps/` keeps step review separate from app review.

Porting rules:
- Port a feature *with its tests*; add a test before changing behaviour.
- Replace all literals (region, buckets, queue names, secret ids) with a single
  `config.py` reading env vars.
- Drop the per-project router duplication (`sgc`, `hcm`, `hermes` each reimplemented
  upload/MA); GWASHub has one generic `studies` + `files` + `runs` model.
- Keep MySQL to minimise porting friction, *or* move to Postgres now (better JSON support,
  RDS/Aurora Serverless v2 options). **Recommendation: Postgres** — the ported surface is small
  (QC tables + new study/file tables), so the switching cost is low now and only grows later.

---

## 4. AWS plan (own account)

### 4.1 Account setup (day 1, ~half a day)

1. Create AWS account with a **dedicated email** (e.g. `aws-root+gwashub@…`), enable root MFA,
   then never use root again.
2. Enable **IAM Identity Center**; create your admin user with MFA. Use `aws sso login` locally.
3. **AWS Budgets**: monthly budget with alerts at 50/80/100% (start at e.g. $100/mo).
   Enable Cost Anomaly Detection.
4. Enable **CloudTrail** (org/all-region trail) and **S3 Block Public Access** at account level.
5. Pick the region (see decisions §6). Optional later: AWS Organizations with separate
   `dev` and `prod` accounts — for MVP, one account with `dev`/`prod` CDK stages is fine.
6. GitHub → AWS via **OIDC role** (no long-lived access keys in GitHub secrets).

### 4.2 Resources (all in `infra/`, CDK)

| Concern | Service | Notes |
|---|---|---|
| Networking | VPC, 2 AZs, public + private subnets, **S3 gateway endpoint** | Avoid NAT Gateway cost (~$35/mo+) for MVP: run Batch Fargate tasks in public subnets with public IP, or add NAT later. |
| Uploads / results | S3 `gwashub-<env>-data` (prefixes `uploads/`, `runs/<run_id>/`) | SSE-S3/KMS, versioning, block public access, CORS for browser multipart upload, lifecycle to IA/Glacier. |
| Reference files | S3 `gwashub-<env>-resources` | Replaces `qc-resource-files`; chain files, reference panels. Bucket name read from `resources/*.yaml` → make overridable by env. |
| QC jobs | **AWS Batch on Fargate** + ECR repos for `images/*` (base, base-r, liftover) | Direct port of `qc_pipeline_batch.yml` with parameterised VPC/bucket. Job role: read input/resources, write `runs/` only. For large files, add an EC2 compute env later (Fargate caps at 16 vCPU / 120 GB RAM / 200 GB ephemeral). |
| Job completion | **EventBridge rule** on Batch job state change → SQS → API worker (or Lambda) ingests `result.json` | Replaces in-process polling; survives restarts. |
| API | **ECS Fargate service behind an ALB** (HTTPS via ACM) | Containerised FastAPI. Cheapest-possible alternative: one small EC2 + Caddy, but ECS avoids snowflake servers. |
| Database | **RDS Postgres** `db.t4g.micro`/`small`, private subnet, automated backups | Credentials in Secrets Manager, injected into ECS task. |
| Frontend | `nuxt generate` → S3 + **CloudFront** | Same parent domain as API so the auth cookie works. |
| Auth | **Amazon Cognito** user pool (email + optional Google/ORCID federation) | Removes dependency on Broad's user service. API validates Cognito JWTs. |
| DNS / TLS | Route 53 hosted zone + ACM certs | `gwashub.<domain>`, `api.gwashub.<domain>`. |
| Secrets | Secrets Manager / SSM Parameter Store | No secrets in `.env` on servers. |
| Observability | CloudWatch Logs (API + Batch), alarms on failed jobs/5xx | |

Step code access at run time: keep `gwashub` (or at least `steps/`) **public** (simplest), or
store a read-only GitHub token in Secrets Manager for the stage container.

### 4.3 Rough running cost (MVP, low traffic, approximate)

ALB ~$18 · ECS Fargate API (0.5 vCPU/1 GB) ~$18 · RDS t4g.micro ~$15 · S3/CloudFront/Route 53
a few $ · Batch Fargate pay-per-run (cents per typical GWAS) · Cognito free tier.
**≈ $55–80/month** before NAT Gateway; verify with the AWS Pricing Calculator for your region.

### 4.4 Data governance

GWAS summary statistics are usually low-risk but often under consortium data-access
agreements. Decide region accordingly, encrypt at rest, private buckets only, presigned URLs
with short expiry, per-user access checks on every file/run, audit via CloudTrail, and a
documented retention/deletion policy.

---

## 5. Phased roadmap

**Phase 0 — Foundations (week 1)**
- [ ] `git init` this directory, create the GitHub repo, add LICENSE/NOTICE.
- [ ] AWS account setup (§4.1).
- [ ] Decide open questions (§6).
- [ ] Local dev stack: docker-compose with Postgres + LocalStack/MinIO.

**Phase 1 — End-to-end skeleton on our AWS (weeks 2–3)**
- [ ] `infra/`: VPC, buckets, ECR, Batch (port `qc_pipeline_batch.yml`), RDS, ECS API, CloudFront.
- [ ] `api/`: config, db, port `QCRun`/`QCStepResult` + migrations, `qc.py`/`qc_runner.py` + tests;
      replace polling with EventBridge → SQS consumer.
- [ ] Move `qc-pipeline` into the monorepo (`steps/`, `runner/`, `images/`), keeping its history; archive the old repo.
- [ ] Build & push `images/*` to our ECR from `gwashub` CI.
- [ ] Prove: upload a fixture file → run `default` → results in DB → presigned download. (No UI yet; via `/docs`.)

**Phase 2 — Default offering (weeks 3–6)**
- [ ] Auth (Cognito) + `studies`, `files` tables and endpoints; presigned multipart upload.
- [ ] Column mapping + suggestion (port from HERMES).
- [ ] `runner/`: artifact upload support. `steps/`: steps 20–80 from §2, each with fixtures/tests.
- [ ] `web/`: login, study list, new study + upload + column map, run status, results/report page.
- [ ] Reference resources uploaded to the resources bucket.
- [ ] Prod stage deployed; alarms, backups, budget alerts verified.

**Phase 3 — Beyond default**
- [ ] Meta-analysis (port `sgc_ma`), LDSC (port `sgc_ldsc`), group/consortium permissions,
      public study pages, user-contributed steps via PRs to `steps/` or as custom steps.

---

## 6. Decisions needed from you

1. **AWS region** — `eu-west-2` (London) if you/your data are UK/EU-based (GDPR, latency);
   `us-east-1` only if collaborators/data are US-centric. Current code hard-codes `us-east-1`.
2. **Database** — Postgres (recommended) vs keep MySQL.
3. **Auth** — Cognito (recommended) vs self-managed users in the API DB vs ORCID-only.
4. **IaC tool** — CDK (recommended: Python matches the codebase) vs Terraform.
5. **Domain name** for the portal.
6. **Is the `gwashub` repo public?** (affects run-time clone of `steps/`; otherwise a read-only token).
7. **Who are the first users** and what file sizes to expect (drives Fargate vs EC2 Batch).
8. **Funding/billing** for the AWS account (personal card vs institutional account/credits —
   check for AWS research credits via your institution).
