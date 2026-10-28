# Poster prompts

The two posters in `docs/images/` are drawn from the prompts below. They are shown in the README and at the bottom of
the website's About page (served from `docs/images/` as `/posters/…`, so replacing a file updates the site at the next
deploy). **Keep these prompts up to date:** when a change alters something a poster shows (a step, an image, a queue,
a role, a service), edit the prompt in the same pull request, then regenerate the poster and replace the file under the
same name.

How to regenerate: paste a prompt into an image or design tool (e.g. Claude Design), export a PNG at 3840 × 2160 (or
at least 1920 × 1080), check every label against the prompt (tools misspell and invent), and save it as the file
named. Each prompt is self-contained.

Last checked against the code: 13 October 2026.

---

## 1. How it works → `docs/images/GWASHub-how-it-works.png`

```text
Create a landscape technical poster, 3840 x 2160 px (16:9), titled "GWASHub: how it works", with the subtitle "Upload
GWAS summary statistics, run them through versioned QC, meta-analyse what passes". Style: clean flat infographic on a
warm off-white background (#F7F5F0), dark navy text (#1E2A3A), Inter or a similar sans-serif, thin rounded panels with
light grey borders, simple line icons, small coloured tag pills. Colour code (with a legend in the bottom-right
corner): navy = website and API; teal (#1F9A8A) = compute; amber (#E8A33D) = data and files; grey = logging and audit;
red-orange (#D9533B) = security; purple (#7A5BA8) = people. Arrows: solid = data flow; dashed = control or events;
dotted = "is recorded in". Numbered black circles 1-7 mark the main QC flow. Every label must be spelled exactly as
written here; do not invent services, numbers or features. Footer left: "GWASHub · in collaboration with HERMES and the Cardiovascular Disease Knowledge Portal"; footer right:
"Architecture as of October 2026".

LAYOUT: four panels in the top two thirds (left: People and roles; centre-top: The building blocks; right-top:
Meta-analysis; centre-bottom, wide: On AWS) and a strip along the bottom: Security, logging and reproducibility.

PANEL "People and roles" (left column, purple person icons):
- Uploader (a cohort analyst): uploads their cohort's summary statistics, maps the columns, fills in study metadata;
  opens only their own datasets (uploaded by them, or naming them as contact); starts QC on them.
- Reviewer: sees every dataset in the project, starts, stops and re-runs QC, approves datasets that passed with
  warnings or sends them back, manages members and the project's pipelines, runs and stops meta-analyses.
- Platform admin: creates projects; uploads shared reference resources.
- Consortium developer: adds QC steps, meta-analysis methods and images by pull request; code owners review before
  anything runs.
- A small red-bordered note: "Permissions are checked by the server on every request. Non-members get 'not found'."
- A dashed purple line from the people to "invite only: members sign in through Amazon Cognito".

PANEL "The building blocks" (centre-top): a large teal block labelled "A QC step" built from four smaller cards:
- Script: any language (Python, R, ...); reads one table, writes one table.
- Image: the software environment it runs in, pinned by digest.
- Resources it declares: reference files such as the genome FASTA, dbSNP, 1000 Genomes panels, LD scores; only the
  files a step declares are given to it.
- Parameters: typed settings (number, choice, yes/no) with defaults and limits; the website draws the form from them,
  the server checks them, and every run records the values it used.
- On fail: block (stop the pipeline; the QC report still runs), flag (warn and carry on), continue.
- Step log: every step writes a log with a verdict: pass, warn or fail.
- A code strip "The step contract": "--input --resources --params --log", "exit 0 = it ran", "never touches the
  database or the network".
To the right: "Pipeline version 3" with a row of eight small teal step tiles grouped under brackets by image, the
caption "a pipeline = an ordered list of steps", and notes: "Each project has named pipelines, each versioned, one the
default; a version pins its steps, images and reference-file versions." and "Consecutive steps that share an image run
together as one job; a new job starts where the image changes." and "Before/after profile and row counts per step,
recorded automatically." A highlighted amber note: "Every step after the first speaks one table format (chromosome,
position, alleles, effect, SE, P ...). Output: GRCh38, a cleaned file, plots and a QC report." Under the steps, the
default pipeline in order (small labels): Parse → Filter → Harmonise and liftover → EAF check → LD score regression →
Summary statistics → Manhattan & QQ plots → QC report. Small boxes: "GWASHub defaults", "Consortium additions (by pull
request)", and "Images: base (Python), base-r (R), vcf (bcftools liftover, gwas2vcf), ldsc (LD score regression), gwama,
metal; consortium images build on these".

PANEL "Meta-analysis" (right-top): four document icons each with a green tick "QC passed" → a "Method" card with pills
"GWAMA" (default) and "METAL" and a dashed pill "consortium methods by pull request", the caption "script · image ·
parameters", and the note "GWAMA runs one chromosome at a time" → a "Post-processing (GWASHub)" card listing: lead loci
by LD clumping against 1000 Genomes; nearest genes; forest plots; locus plots; per-dataset checks (direction,
outliers, λGC, LDSC intercept). Below, four small thumbnails: Manhattan plot, Forest plot, Locus plot, Results table.
Footnote: "Each meta-analysis runs as one Batch job in its method's image, on an On-Demand machine."

PANEL "On AWS" (wide, centre-bottom), a large rounded box "AWS account: gwashub-prod (and an identical gwashub-dev),
London eu-west-2" with tags "dev" and "prod" and the caption "dev is the lean copy: one NAT gateway, smaller database,
Development build banner on every page". Inside, left to right:
- User's browser → Amazon CloudFront (HTTPS only) → Amazon S3: website (static site files) and "/api/*" → an internal
  load balancer (not reachable from the internet).
- Amazon Cognito user pool: invite-only sign-in, password plus authenticator app (MFA) for everyone; "verified identity
  token" to the API.
- A VPC with public subnets (NAT gateway, outbound only), private subnets and database subnets across Availability
  zones A and B, and an S3 gateway endpoint.
- API service (ECS Fargate, Graviton, FastAPI) with an "API role" tag.
- AWS Batch (teal) with "job role: read uploads and resources, write results; nothing else" and three queues: "Graviton
  (arm64) Spot queue", "Intel (x86-64) Spot queue: for tools without ARM builds", and "On-Demand queue: meta-analyses".
  Caption: "one job per image group, sized from the steps' CPU and memory; up to 500 GB encrypted disk; Spot machines
  can be taken back, and the job then starts again".
- Worker service (ECS Fargate) with a "worker role" tag: records finished and failed jobs; also checks unfinished runs
  every 5 minutes.
- Amazon RDS PostgreSQL (encrypted, automated backups, Multi-AZ in prod): projects, datasets, pipeline versions,
  metadata.
- Jump host (Systems Manager): read-only database browsing through Session Manager; no open ports.
- On the right: three S3 buckets "uploads", "results", "resources" (pills: KMS encrypted, versioned, private, TLS
  only); Amazon ECR (container registry); Amazon EventBridge; Amazon SQS; AWS Secrets Manager (database password,
  session key, Cognito client secret).
Numbered flow: 1 upload in 64 MB parts with short-lived signed links (the file never passes through the API) → 2 job
spec → 3 submit jobs, each waiting on the one before → 4 images pinned by digest → 5 read the upload and only the
declared reference files; write outputs and live progress (each running step's last output lines) → 6 job finished or
failed events (EventBridge → SQS → worker) → 7 downloads by signed link, after a permission check.

BOTTOM STRIP, three columns:
- "Security" (red-orange icons): invite-only sign-in with MFA for everyone (Cognito); permissions enforced on the
  server; no public servers (API and database in private subnets; only CloudFront faces the internet); everything
  encrypted at rest (KMS) and in transit (TLS only); short-lived signed links for every upload and download;
  least-privilege IAM roles per service, no passwords or access keys in code, secrets in Secrets Manager; separate AWS
  accounts for production, development, audit and management (AWS Organizations, Control Tower region guardrail); code
  for new steps reviewed by pull request (code owners) before it can run.
- "Logging and monitoring" (grey): Amazon CloudWatch Logs (one log group per environment, streams for the API, worker
  and every Batch job); AWS CloudTrail (organisation-wide: who did what, when, in every account); Amazon GuardDuty
  (threat detection); AWS Security Hub and IAM Access Analyzer (best-practice checks, delegated to the audit account);
  inside the app: each step's log with its verdict, live progress and output per step, and the run's messages shown to
  users.
- "Reproducibility" (with ticks): every run records the pipeline version, the exact steps and the parameter values
  they ran with, image digests, reference-file versions (with checksums), and before/after profiles of the data. Every
  reference file has a recorded source, version and licence. The same code runs locally (folder, SQLite, Docker) and on
  AWS (S3, PostgreSQL, Batch): only configuration changes.
```

---

## 2. The stack → `docs/images/GWASHub-stack.png`

```text
Create a landscape technical poster, 3840 x 2160 px (16:9), titled "GWASHub: the stack", with the subtitle "One
monorepo: a Nuxt website, a FastAPI service, pipeline images, and AWS CDK for the deployment". Same style as a clean
engineering infographic: warm off-white background (#F7F5F0), dark navy text (#1E2A3A), Inter or a similar
sans-serif, rounded white cards with thin borders, version numbers as small amber superscripts, code and paths in a
monospace font. Every label must be spelled exactly as written here; do not invent tools, versions or numbers. Footer
left: "GWASHub · in collaboration with HERMES and the Cardiovascular Disease Knowledge Portal"; footer right: "Stack as of October 2026, from the main branch".

LAYOUT: a large left area "Layers, from the browser down" (about two thirds of the width) made of five rows; each row
starts with a coloured label card, followed by four or five white cards. A right column with three panels: "Local or
AWS", "dev and prod", "Deploying". A full-width bottom strip: "Building, testing and contributing".

ROW 1, navy label card "Website" (path web/; note "Built to static files; served by S3 + CloudFront"):
- Nuxt 4: single-page app in Vue and TypeScript, no server rendering.
- PrimeVue 4: components on the Aura theme in HERMES blue (with a strip of four blue swatches); pinned to v4.
- PrimeIcons, Inter: icon set and typeface.
- Own plot components: Manhattan, QQ, forest and locus plots drawn in the browser.
- Uploads in parts: large files go straight to storage in parts by signed link; progress carries on while you use the
  rest of the site.

ROW 2, navy label card "API and worker" (path api/; note "One image, gwashub/app: same code, different command"):
- Python 3.12: slim base image, built for arm64 (Graviton).
- FastAPI, Uvicorn: routes for projects, cohorts, datasets, pipelines, meta-analysis and sign-in; permissions
  enforced here.
- SQLAlchemy, Alembic: database models and versioned schema migrations.
- psycopg, PyJWT: PostgreSQL driver; signed tokens for sign-in.
- boto3, httpx: talks to S3, Batch and SQS; HTTP calls to Cognito.

ROW 3, teal label card "Pipeline jobs" (path pipeline/; note "Steps and methods run inside these images; one Batch job
per image group"):
- Runner: one process per step; runs a job spec locally or as a Batch job; publishes each running step's last output
  lines; code line "python -m pipeline.runner".
- Step and method files: each step is a script, a step.yaml with typed parameters and its CPU and memory, and its
  test; each method an analysis.yaml and a script.
- Tools: PLINK 2.0, bcftools 1.22 with the liftover plugin, gwas2vcf (edited), LDSC (bulik/ldsc), GWAMA 2.2.2,
  METAL 2020-05-05.
- A wide teal-tinted card "Images" with the caption "consortium images build FROM these", six image tiles in two
  columns, each with a small container icon and a one-line note:
  gwashub/base (Python 3.12, the runner and every step, PLINK 2, bcftools; arm64) ·
  gwashub/base-r (+ R, data.table, jsonlite; arm64) ·
  gwashub/vcf (bcftools liftover, gwas2vcf; x86-64) ·
  gwashub/ldsc (LDSC in Python 2.7; x86-64) ·
  gwashub/gwama (+ GWAMA 2.2.2; arm64) ·
  gwashub/metal (+ METAL 2020-05-05; arm64).
  Under it: "Meta-analysis methods: GWAMA (default), METAL, and consortium methods by pull request".

ROW 4, amber label card "Data" (note "Database for records, object storage for files"):
- PostgreSQL 16 on Amazon RDS: SSL enforced; SQLite when running locally; the same API tests run on both.
- Amazon S3: uploads, results, resources: KMS encrypted and versioned; unfinished uploads cleared after 7 days; older
  uploads moved to Glacier Instant Retrieval; local folders when running locally.
- Reference resources: genome FASTA, dbSNP, 1000 Genomes panels, LD scores; each with source, version, licence and
  checksum (resources/catalog.yaml).
- Synthetic data: fake datasets and reference files for local work and tests.

ROW 5, black label card "Infrastructure as code" (path infra/; note "London, eu-west-2"):
- AWS CDK in Python v2: three stacks per environment, drawn as three small boxes: Network (VPC), Data (KMS, S3,
  Postgres), App (sign-in, API, worker, Batch, website).
- Amazon ECR: immutable tags, scanned on push; pipeline images pinned by digest.
- ECS Fargate: API and worker on ARM64.
- AWS Batch: QC on Spot (Graviton m7g/r7g for arm64 images, Intel m7i/r7i for x86-64 images); meta-analyses on
  On-Demand machines; scales to zero.

RIGHT PANEL "Local or AWS" (caption "Same code in both places; only configuration (environment variables) changes"), a
three-column table:
Website | Nuxt dev server, proxies /api | Static files on S3 + CloudFront
Sign-in | Pick a user, no password | Cognito with MFA; the image refuses to start without it
Database | SQLite | PostgreSQL on RDS
Files | Local folder | Three S3 buckets
Jobs | Threads in the API process, steps in Docker | AWS Batch; results arrive through SQS and the worker

RIGHT PANEL "dev and prod", a table with columns gwashub-dev | gwashub-prod:
NAT gateways 1 | 2; Database instance t4g.small | t4g.medium; Multi-AZ database no | yes; Backups kept 7 days |
30 days; API tasks 1 | 2; Batch maximum vCPUs 32 | 128; Batch disk 200 GB | 500 GB; Logs kept 90 days | 365 days;
Old file versions kept 30 days | 90 days; Page banner "Development build" | none.

RIGHT PANEL "Deploying": code line "./devtools/aws_cdk_deploy.sh [dev|prod]" and four numbered steps: 1 Build the
website to static files (nuxi generate) and refuse to deploy an empty build; 2 Build the app image for arm64 and push
it to ECR; 3 Build every pipeline image in dependency order, push, record digests; 4 CDK deploys Network, Data and App,
with the digests pinned in Batch.

BOTTOM STRIP "Building, testing and contributing", four cards:
- pytest, moto: QC steps, runner and image grouping, meta-analysis, S3 (mocked by moto), API including permissions;
  about 150 tests, run in full before every change.
- Local stack: dev.sh --fresh makes synthetic data, seeds the database, uploads demo datasets and runs meta-analyses;
  API on :8000, website on :3000.
- AWS helpers: aws_admin.sh for admin commands, aws_db_tunnel.sh for the database, aws_bookmarks.py for console links.
- GitHub pull requests: CODEOWNERS and a pull-request template; CONTRIBUTING.md explains how a consortium adds a step,
  method or image; validation tests compare GWASHub with the original programs (docs/VALIDATION_PLAN.md).
```
