# Progress log

Built 2026-10-02/03: first locally, then deployed to AWS (account gwashub-dev). No GitHub pushes and no
git commits yet (this folder isn't a git repository). The original `qc-pipeline` and Broad clones were
removed (their content is on GitHub); this repo is organised into `web/`, `api/`, `pipeline/` (runner,
qc_steps, images, analyses), `infra/` (AWS CDK) and a git-ignored `local/` for generated data.

## Status

| Area | State |
|---|---|
| `qc-pipeline` copied in (`pipeline/`) | ✅ |
| Synthetic genome + fake GWAS generator (`devtools/synth.py`) | ✅ 14 datasets, 2 traits, 9 injected fault types |
| Default QC steps (11, `pipeline/qc_steps/gwashub/`) | ✅ real implementations, per-step tests |
| Consortium steps by pull request (`pipeline/qc_steps/<owner>/`, `CONTRIBUTING.md`) | ✅ examples: `hcm/sarcomere_filter` (R), `custom_example/hello_world` |
| Runner | ✅ per-step processes, resources per step, block/flag/continue, crash handling, Python and R steps, image groups |
| Meta-analysis engine | ✅ fixed / random effects, Q, I², direction, GC, forest data, plots |
| API (FastAPI; SQLite locally, Postgres + Alembic on AWS) | ✅ server-side permissions, uploads in parts, QC jobs, reviews, versioned pipelines, resources, meta-analysis, dataset delete and genome-build edits, member management by reviewers, project creation by platform admins |
| Web wired to the API | ✅ every page; mock data removed |
| Meta-analysis tab | ✅ builder (datasets + settings + project defaults), results page (Manhattan, QQ, lead loci, forest plots) |
| End-to-end demo via HTTP (`devtools/e2e.py`) | ✅ |
| Interim AWS pilot (one EC2 server, password sign-in): `infra/minimal.yaml`, `devtools/aws_deploy.sh` | ✅ was live at an sslip.io address; superseded, still running until you delete it (`docs/AWS_MINIMAL.md`) |
| Full AWS deployment (CDK in `infra/`): S3 direct uploads, Postgres, Cognito (MFA), Fargate API + worker, Batch jobs per image group, CloudFront | ✅ **live in gwashub-dev**, checked end to end (uploads, QC on Batch incl. an R step in its own image, sign-in); see `docs/AWS_FULL.md` |

**Tests: 81 passing** (`.venv/bin/python -m pytest`): QC steps 26, runner and grouping 11, meta-analysis 5,
liftover chain 1, S3 helpers 4, API 31 (also run against Postgres 16), S3 storage and jobs (mocked) 3.

## How to run

```bash
cd gwashub
./devtools/dev.sh --fresh   # first time (≈1 min): data, DB, 14 uploads through the API, 3 meta-analyses
./devtools/dev.sh           # afterwards → http://localhost:3000
```

## End-to-end results (real HTTP uploads, auto-suggested column mappings)

Column-mapping suggestion was correct for **all 14 files** (5 software styles), including detecting
odds ratios from the values.

| Dataset | Injected fault | QC outcome |
|---|---|---|
| UK Biobank (GRCh37, PLINK), FinnGen (REGENIE), MVP AFR (GWAS-SSF), BBJ EAS (METAL, CSV, OR), EstBB males | – | passed |
| EstBB females | missing values, duplicates, `chr` prefix | for review → approved |
| HUNT | λ inflation 1.18, lower-case alleles | for review (λGC 1.197) → approved |
| BioVU | P given as −log10(P) | failed (blocked at Value ranges) |
| Partners | EA/OA columns swapped | failed (Reference check: EAF r = −1.00) |
| deCODE | GRCh37 file declared GRCh38 | failed (Reference check: 100% not in panel) |
| Genes & Health | no SE column | failed (blocked at Row check) |
| SHaRe, HCMR (GRCh37), Pakistan HCM | – | passed (HCM project; the consortium **R** step `hcm/sarcomere_filter` ran) |

Meta-analyses vs the simulated truth:
- **All-cause HF, 7 datasets, fixed effects: 20/20 true loci, 0 false positives**, λGC 1.04, effect
  sizes within a few % of the truth (re-checked on 2026-10-03 after the AWS changes).
- **HCM, 3 datasets: 4/8 loci, 0 false positives.** The 4 misses are explained: 2 loci sit in the
  regions the project's sarcomere filter deliberately removes; the other 2 have the smallest effects
  (β 0.030–0.035) and are sub-threshold in these small cohorts (P 3×10⁻⁶ and 7×10⁻⁴).
- EUR-only random effects: 19 loci; λGC 0.83 (DL random effects deflates null statistics; the UI notes this).

## Decisions made (please review)

1. **Copied, not imported with history.** `scripts/default/{row_check,qc_report}.py` → `pipeline/steps/`
   (row_check now parses any delimiter/gzip; its range checks moved to `value_ranges`).
   `orchestrator/` → `pipeline/runner/` (rewritten). `s3io.py` kept with its tests in `pipeline/runner/aws/`
   (`repo_fetch.py` was later removed with the git-repo custom steps). Dropped: `aws_wrapper.py`, `resources.py`, `manifest.py` (`extends` format),
   `report.py`, the `nick_test` example.
2. **Contract unchanged** (`--input --resources --params --log`); extra context via optional env vars;
   per-step params arrive as `params["step"]`.
3. **Steps declare resources** in `step.yaml`; only those are staged. `always_run: true` lets the QC
   report run after a block.
4. **Value ranges and Reference check default to `block`** (unusable data otherwise produces noise).
5. **Reference check aligns all variants to effect allele = panel ALT**, so QC output is directly
   meta-analysable.
6. **Synthetic resources use production file names** (`hg19ToHg38.over.chain.gz`,
   `ref_panel_grch38.tsv.gz`) but start with a `# SYNTHETIC` header. Real UCSC/1000G files were not
   downloaded (needs your OK).
7. **Dataset status:** QC pass → passed, warn → for review, fail/blocked → failed; a reviewer's
   decision (approve/reject/request changes) overrides and is shown in the panel.
8. **Permissions:** uploaders see all rows/counts but open only their own datasets; reviewers do
   everything else. **Meta-analysis:** all members can view results and plots; only reviewers run
   them or download full summary statistics. Non-members get 404 (projects aren't revealed).
9. **Pipeline versions pin resource versions**; each run records the image digests it used; datasets keep
   the version they ran with; re-running uses the current version.
10. **Upload options** (your request): per project, reviewers set the phenotypes, allowed ancestries
    and expected sex strata (Pipeline settings tab); the upload form only offers those and the API
    rejects anything else. Removing a phenotype keeps existing datasets.
11. **Sign-in:** local is passwordless (pick a user); the interim server used passwords; AWS uses Cognito
    (invite-only, MFA for everyone). In every mode the session is an HMAC-signed cookie with a 12-hour expiry.
12. The HERMES logo is in `web/public/brand/`, shown on the front page ("A HERMES Consortium
    resource") with a footer credit. **Check the link**: I used https://hermesconsortium.org.

## Changes requested on 2026-10-03 (done)

- HERMES logo in `web/public/brand/`, front-page hero ("A HERMES Consortium resource") and footer credit.
- "Pipeline" tab renamed **Pipeline settings**; it now starts with **Upload options** (phenotypes,
  allowed ancestries, expected sex strata), enforced by the API.
- Theme: primary palette built on the HERMES logo blue **#303880** (also used in the PNG plots).
- Declutter pass: removed the synthetic example plot (front page), duplicate status tiles and the
  permissions note (overview), "Open →" on project cards, the always-on review box (now only for
  "for review"; otherwise an "Override QC outcome" link), default-step provenance lines and
  `rows_out` metrics (panel; metrics capped at 4 with "+N more"), the empty custom-steps card and
  the sidebar contract help (moved into the custom-step dialog).
- Copy: em dashes removed from all UI text, messages and seed names; marketing phrasing replaced
  with plain descriptions.
- Dataset summary defaults to **phenotype × ancestry × sex** (other groupings in a dropdown).
- **Study metadata** (the HERMES data-registry fields: study, participants, genotyping, imputation,
  genotyping QC, age/sex distribution) defined once in `api/gwashub_api/metadata_schema.py`; captured
  at upload (new wizard step, with "copy from a previous upload") and **editable in the dataset
  viewer** by the uploader or a reviewer. Descriptive fields (cohort, phenotype, ancestry, sex, N) are
  editable too; every edit is logged (who, when, old → new) and shown as an edit history.
- Dataset panel split into **QC** and **Details** tabs.
- Per-step outputs: an **Output** button opens a viewer with the step log, console output and every
  file the step wrote (images inline, tables as tables). Per-step links removed; one "Download report".
- **Pipeline settings** tab moved to the right of the tab bar.
- "QC methods" replaced by an **About** page (what GWASHub is, how it works, roles, the default QC
  steps, data handling). `/pipelines` redirects there.
- Checked in the browser after the usage-limit pause: Details tab view and edit mode, edit history
  (now uses field labels and collapses long edits), the step output viewer (step log, console,
  images), and the About page. Fixed the header showing "Sign in" on non-project pages.
- **AWS deployment guide:** `docs/AWS_DEPLOYMENT.md` (accounts, security, S3 direct uploads for
  multi-GB files, Batch sizing from measured memory use, Cognito, CloudFront, CI/CD, costs, checklist,
  the code changes needed, and a 6-week schedule).

## Changes on 2026-10-03, afternoon (done)

- **Datasets:** delete (uploader or reviewer; removes the upload and all QC outputs), change the genome
  build (re-runs QC), 80%-wide dataset panel.
- **AWS, full deployment** (`docs/AWS_FULL.md`): files in S3 with browser uploads in 64 MB parts and
  presigned downloads; Postgres with Alembic; Cognito hosted sign-in with MFA; API and worker on Fargate
  behind an internal load balancer reached through CloudFront; QC and meta-analyses on AWS Batch (Spot
  Graviton, scales to zero); a jump host and read-only database user for PyCharm (`devtools/aws_db_tunnel.sh`).
- **Pipeline layout:** steps in `pipeline/qc_steps/<owner>/`, images in `pipeline/images/<owner>/`.
  Custom steps from arbitrary git repositories were removed; consortia add steps by pull request
  (`CONTRIBUTING.md`, PR template, CODEOWNERS; "Contribute a custom step" dialog on the pipeline page).
- **Per-step images with grouping:** consecutive steps sharing an image run as one Batch job; jobs are
  chained with Batch dependencies. New `gwashub/base-r` image (R 4.5).
- **Step outputs:** every file a step writes is listed and downloadable; empty console files aren't kept.
- **Members and projects:** reviewers invite members (new people get a Cognito invitation), change roles
  and remove members; platform admins create projects from the Projects page.
- Environment banner per deployment ("Development build" on AWS dev, none in prod).

## Found and fixed during testing

- Generator: indels in inverted segments complemented but not reversed (12 spurious mismatches).
- `stats_summary`: numpy `clip(lower=)` crash; "λGC = nan is within range" for −log10 P files.
- Blocked runs had no report → `always_run`.
- Matplotlib font cache rebuilt every run (16 s) → shared cache.
- API not importable under uvicorn (repo root path).
- QQ plot unreadable with strong signals → independent axes. Forest-plot text too small.
- Project pages blank for non-members → explicit message.
- Logo served as HTML by the dev server (public file added after start) → bound src + restart.
- AWS: the dataset panel was narrow only in the built site (PrimeVue's CSS loaded after ours).
- AWS: first-boot race on the interim server (no internet before the Elastic IP was attached).
- AWS: containers couldn't write `/tmp` with a read-only root filesystem (rolled back; deferred).
- AWS: the worker crashed on SQS errors (now retries); the "latest run" check used a stale list.

## Not done / known limitations

- **AWS hardening and operations:** see "Not done yet" in `docs/AWS_FULL.md` (WAF, custom domain,
  locked-down environment for consortium steps, alarms, malware scanning of uploads, CI/CD, real
  reference data, read-only root filesystem, Cognito email through SES for more than ~50 invitations a day).
- **Production** (gwashub-prod) is configured in `infra/gwashub_infra/config.py` but not deployed.
- **Not a git repository yet:** `.github/` (CODEOWNERS with placeholder team names, PR template) is ready
  for when it goes on GitHub.
- No real reference data (chain file, 1000G panel); indel left-normalisation not handled.
- Locally, a step's `image` is recorded but ignored (this machine's Python/R are used).
- Resource import-from-URL is not implemented (upload only).
- Custom-step test runs from the browser were removed with the git-repo custom steps; step tests run in CI.
- `qc-pipeline` on GitHub not archived (your call).
