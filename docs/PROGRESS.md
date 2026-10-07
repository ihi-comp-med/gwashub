# Where GWASHub stands

Current state, decisions and known gaps (2026-10-07). The site is feature-complete for now; what's left is validation
(tests 2 to 5) and the production items. How things changed day by day is in the git history; how to run
it is in `README.md`, the AWS deployment in `docs/AWS_FULL.md`.

## Built

| Area | State |
|---|---|
| Website (Nuxt 4 + PrimeVue 4) | Every page on the API: projects, overview, datasets, cohorts, upload wizard, dataset panel (QC and Details), pipeline settings, meta-analysis builder and results, Account (bulk upload, API tokens), Validation, About; six guided tours in the Help menu |
| API (FastAPI) | Server-side permissions; uploads in parts; pending QC; QC jobs; reviews; named, versioned pipelines; resources; cohorts and study metadata; meta-analysis; dataset and project deletion; bulk upload (Excel manifest, check, API tokens); the Practice project |
| Default QC | The HERMES 3 DCM pipeline as eight steps (below). Tested on synthetic data and dry runs on real files; not yet compared with the HPC results on real cohorts |
| Runner | One process per step, in the step's image; resources per step; block / flag / continue; crash handling; Python and R steps; image groups as Batch jobs |
| Meta-analysis | `gwashub/gwama` (default), `gwashub/metal`, consortium methods by PR; shared post-processing: lead loci by PLINK 2 clumping, signals, locus plots with genes, forest plots, `datasets.tsv`, `README.txt` |
| Reference files | `resources/catalog.yaml` and build scripts; all 37 real files (~26 GB, incl. dbSNP 157) on AWS dev |
| AWS | Live in gwashub-dev (`docs/AWS_FULL.md`); prod configured, not deployed |
| Validation | Test 1 (site GWAMA vs GWAMA by hand) passes 7/7; tests 2 to 5 planned (`docs/VALIDATION_PLAN.md`, `validation/`) |
| Tests | 99, all passing (`.venv/bin/python -m pytest`; step tests need the images from `./devtools/build_images.sh`) |

## Default QC pipeline

From `nicksunderland/hermes3_dcm` (commit ff35109), split into steps. Every step after `parse` reads and writes the
GWASHub format (`_lib/common.py` `STANDARD`: `chr pos ea oa beta se p eaf` + optional `n ncase ncontrol rsid info`).

| Step | Image | What it does |
|---|---|---|
| `parse` (R) | `gwashub/base-r` | The only step that reads the upload. Recodes and validates; removes rows it can't recover (missing or invalid essentials, EAF missing, 0 or 1); P from BETA/SE only where missing; N from cases + controls. Reports missingness and out-of-bounds counts per column, chromosomes present, indels |
| `filter` (R) | `gwashub/base-r` | Chromosome set; range filters (\|BETA\|, SE, INFO, MAF, N), each with its own switch and min/max switches; missing INFO filled with 1 or removed; indels; duplicates. Warns above 5% removed |
| `harmonise_liftover` | `gwashub/vcf` (x86-64) | The edited gwas2vcf (copied into the step) and bcftools +liftover; dbSNP 157 rsIDs. GRCh38 with EA = ALT afterwards |
| `eaf_check` | `gwashub/vcf` | EAF vs 1000 Genomes; warns above 5% with \|ΔEAF\| > 0.2; removes nothing |
| `ldsc` | `gwashub/ldsc` (x86-64) | The authors' bulik/ldsc (Python 2.7), Pan-UKB LD scores; intercept and ratio |
| `stats_summary`, `plots`, `qc_report` | `gwashub/base` | λ metrics, hits, lead loci by distance; Manhattan and QQ; the HTML report |

The runner profiles every run's upload and cleaned file (`_lib/profile.py`) and counts rows before and after each step,
whatever the pipeline. A QC run is four Batch jobs (one per change of image).

## Decisions

**Data and QC**
- Steps follow one contract (`--input --resources --params --log`, JSON step log with a verdict) and never touch the
  database or the network. They declare the resources they need; only those are staged.
- A step always runs in the image its `step.yaml` names. Pipelines choose steps, `on_fail` and parameters only.
- Typed parameters (`pipeline/runner/params.py`, including `range`) drive the forms and the API's validation.
- `step.yaml`: `description` is one sentence; `narrative` holds the longer explanation shown on the pipeline page.
- **Bulk upload** (`client/gwashub_upload.py`, plan and decisions in `docs/BULK_UPLOAD_PLAN.md`): a standard-library
  Python script downloaded from the Account page; an Excel manifest (README sheet, Manifest sheet with drop-downs; read
  on the server, `manifest_xlsx.py`) names every column exactly (nothing guessed) and repeats
  a cohort's details on its rows; `check` reports every problem by row (server-side, `routers/bulk.py`, the wizard's own
  rules) and `upload` sends nothing unless all pass, then resumes after interruptions. Duplicates are refused; a manifest
  never changes an existing cohort. Authenticated by **personal API tokens** (one project, upload only, 30 days, stored
  hashed, revocable; accepted only by the upload endpoints, `auth.upload_caller`).
- **Guided tours** (Help menu, top right; driver.js, MIT): six tours (upload a dataset, check QC and start it, review a
  dataset, run a meta-analysis, bulk upload, set up a project) defined as data in `web/app/utils/tours.ts`, pointing at
  elements marked `data-tour="…"`; they move between pages and wait when the next element only appears after the user
  acts. `api/tests/test_tours.py` fails if a tour points at an element or page that no longer exists.
- **Practice project** (`practice.py`, files in `practice/`): created at start-up (`GWASHUB_PRACTICE=0` turns it off),
  every user a reviewer so every tour works there, three synthetic files to download with the details to enter. Each
  person sees only themselves in its members, which can't be changed; its data is shared and may be deleted.
- Every upload (website or script) lands as **pending**: QC starts when the uploader (their own datasets) or a reviewer
  clicks Start QC, in the dataset panel or for a selection in the datasets table. Selected datasets can be deleted in bulk.
- Dataset status: QC pass → passed, warn → for review, fail → failed; a reviewer's decision overrides.
- Reproducibility: pipeline versions pin resource versions; runs record the pipeline version and image digests.

**Pipelines**
- A project has several named pipelines, each versioned, one the default. A dataset follows the default unless a
  reviewer pins it to another. Changing a dataset's pipeline, or saving a new version, only advises a re-run: the
  dataset shows as out of date (latest run on another version), with counts and "Re-run all out of date".
- Changing a dataset's genome build re-runs QC automatically.

**People and permissions**
- Enforced server-side (`auth.py`). Uploaders see all rows and counts but open only their own datasets; reviewers do
  everything else in their project, including members, pipelines and metadata settings. Non-members get 404.
- Platform admins create projects (becoming the first reviewer), upload resources and delete projects. For anything
  else they need a reviewer role in the project.
- Meta-analysis: every member sees results and plots; only reviewers run them or download full summary statistics.
- Sign-in: local is pick-a-user; AWS is Cognito, invite-only, MFA for everyone. Sessions are HMAC-signed cookies
  (12 hours).

**Metadata**
- Fields are defined once (`metadata_schema.py`, HERMES and SGC), each per cohort or per dataset; projects choose
  which are shown and required, and can add their own (text, number, whole number, yes/no, choice, date). Project
  fields can be renamed and hidden, never deleted, so saved values and edit history stay valid.
- Datasets inherit their cohort's values and may override them; every edit is logged.
- Phenotypes are case/control or quantitative; quantitative datasets give a total N. A meta-analysis can't mix them.

**Deleting**
- Datasets: uploader or reviewer; removes the upload and all QC outputs; past meta-analyses keep their results.
- Projects: platform admins; the name must be typed (pasting blocked) and confirmed again; refused while jobs run.
  Removes every record and the project's files in all storage areas. On AWS the buckets are versioned, so files stay
  recoverable for 30 days (dev) or 90 days (prod).

**Storage paths** `uploads/<project>/<dataset>-<UTC time>/<file>`, `qc/<project>/<dataset>/<run>-<UTC time>/`,
`meta/<project>/<id>-<UTC time>/`, `resources/<shared|project>/<key>/…`: findable, and unique across database resets.

## Known behaviour

- Where an indel's shorter allele is the first base of the longer one, gwas2vcf keeps the upload's orientation, so
  EA = ALT may not hold for those indels (BETA still matches the EA).
- Synthetic datasets don't fit the real references (only ~50% of SNPs match the real GRCh37 base), so on AWS dev
  `harmonise_liftover` warns for them. Smoke-test dev with a real file.
- Job sizes come from each step's `compute:`; they don't yet scale with the uploaded file's size.
- `stats_summary` finds per-dataset lead loci by distance (a QC indicator); meta-analysis lead loci use LD clumping.

## Not done

- Production deployment and the AWS items in `docs/AWS_FULL.md` → "Before production" (WAF, custom domain, isolated
  consortium steps, malware scanning, alarms, CI/CD, SES for invitations).
- Validation tests 2 to 5, including the comparison with the HERMES 3 HPC (Snakemake) results. Deliberate differences
  from HERMES 3 to list there: warning at 5% removed, missing INFO filled with 1 by default.
- Resource import from a URL (upload only).
- Idea: when few variants match the declared build's reference, try the other build and say which fits.
- The stack poster in `docs/images/` predates the vcf and ldsc images and GWAMA; regenerate it when convenient.
- `.github/CODEOWNERS` has placeholder team names; the `qc-pipeline` repository on GitHub isn't archived.
