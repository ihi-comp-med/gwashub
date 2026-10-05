# Progress log

Built 2026-10-02/03: first locally, then deployed to AWS (account gwashub-dev). It is a git repository
(branch `main`, three commits), not yet pushed to GitHub. The original `qc-pipeline` and Broad clones were
removed (their content is on GitHub); this repo is organised into `web/`, `api/`, `pipeline/` (runner,
qc_steps, images, analyses), `infra/` (AWS CDK) and a git-ignored `local/` for generated data.

## Status

| Area | State |
|---|---|
| `qc-pipeline` copied in (`pipeline/`) | ✅ |
| Synthetic genome + fake GWAS generator (`devtools/synth.py`) | ✅ 14 datasets, 2 traits, 9 injected fault types |
| Default QC pipeline (`pipeline/qc_steps/gwashub/`) | ✅ the HERMES 3 DCM pipeline since 2026-10-05: parse, filter, harmonise_liftover (gwas2vcf + bcftools), eaf_check, ldsc (bulik/ldsc), stats_summary, plots, qc_report; tested on synthetic data, not yet on real cohorts |
| Consortium steps by pull request (`pipeline/qc_steps/<owner>/`, `CONTRIBUTING.md`) | ✅ example: `custom_example/hello_world` |
| Runner | ✅ per-step processes, resources per step, block/flag/continue, crash handling, Python and R steps, image groups |
| Meta-analysis methods | ✅ `gwashub/gwama` (default), `gwashub/metal`, consortium methods by PR (example: weighted Z); shared post-processing: lead loci, forest data, plots, `datasets.tsv`, `README.txt` |
| API (FastAPI; SQLite locally, Postgres + Alembic on AWS) | ✅ server-side permissions, uploads in parts, QC jobs, reviews, versioned pipelines, resources, meta-analysis, dataset delete and genome-build edits, member management by reviewers, project creation by platform admins |
| Web wired to the API | ✅ every page; mock data removed |
| Meta-analysis tab | ✅ builder (method picker, typed parameter forms, project defaults per method), results page (settings box, Manhattan, QQ, lead loci, forest plots, per-dataset filter counts, downloads) |
| End-to-end demo via HTTP (`devtools/e2e.py`) | ✅ |
| Interim AWS pilot (one EC2 server, password sign-in) | ✅ ran on 2026-10-03; deleted once the full deployment was live, together with its template, script and password sign-in |
| Full AWS deployment (CDK in `infra/`): S3 direct uploads, Postgres, Cognito (MFA), Fargate API + worker, Batch jobs per image group, CloudFront | ✅ **live in gwashub-dev**, checked end to end (uploads, QC on Batch incl. an R step in its own image, sign-in); see `docs/AWS_FULL.md` |

**Tests: 77** (76 pass locally after `./devtools/build_images.sh`; the GWAMA comparison runs only inside the `gwashub/gwama` image) (`.venv/bin/python -m pytest`), including the API tests on Postgres 16 and S3 storage and Batch job
hand-over against mocked S3.

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

1. **Copied, not imported with history.** `scripts/default/{row_check,qc_report}.py` → `pipeline/steps/` (now `pipeline/qc_steps/gwashub/`)
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
11. **Sign-in:** local is passwordless (pick a user); AWS uses Cognito (invite-only, MFA for everyone). In
    both the session is an HMAC-signed cookie with a 12-hour expiry. (The interim server's password mode was
    removed with it.)
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

## Changes on 2026-10-04 (done)

- **Datasets table:** Overview / Details toggle (Details: cases, controls, λGC, λ1000, λ MAF≥1%, variants,
  hits at 5e-8 and 1e-5, liftover counts, LDSC intercept, in smaller type); pipeline version per dataset
  (older ones highlighted); cohort filter; reviewers select datasets and **re-run QC** in bulk.
- **Dataset panel:** a QC metrics table above the plots; metadata shows what comes from the cohort and what
  a dataset overrides.
- **Cohorts** are records (per project; Cohorts tab): cohort-level metadata (study, genotyping, imputation,
  QC) entered once and inherited by its datasets, which may override a value. Existing datasets were linked
  to cohorts by name on first start. Uploads pick an existing cohort or add one, warned about similar names.
- **Metadata field catalogue** (`metadata_schema.py`, HERMES and SGC fields, each per cohort or per dataset);
  per project, the fields shown and those required at upload are stored in the database (Pipeline settings →
  Metadata fields; platform admins pick a starting set when creating a project). Missing required fields show
  as "metadata incomplete".
- **Phenotype codes and descriptions**, with CSV import.
- **QC:** `stats_summary` adds λ1000, λ at MAF ≥ 1% and hits at 1e-5; new step `gwashub/ldsc` (LD score
  regression intercept, in the default pipeline; it passes and says so when the reference isn't uploaded yet;
  synthetic LD scores locally, Pan-UK Biobank intended for production). Steps can declare `optional_resources`.
- **Resources:** platform admins only, for one project or shared with all, uploaded in parts (multi-GB).
- **Sign-in page** in HERMES blue (Cognito branding in `infra/gwashub_infra/login_branding.json`).

## Meta-analysis methods and typed parameters (2026-10-04, done)

- Methods are folders like QC steps: `pipeline/analyses/meta_analysis/<owner>/<method>/analysis.yaml` + script,
  added by pull request (CONTRIBUTING.md section 6). Every project can use every method.
  - `gwashub/gwama`: wraps GWAMA 2.2.2 (image `gwashub/gwama`), incl. genomic control and the sex-differentiated test.
  - `gwashub/metal`: wraps METAL 2020-05-05 (image `gwashub/metal`), standard-error or sample-size scheme.
  - `custom_example/custom_meta_analysis`: weighted Z-score example (no pooled effect size).
- A method writes `meta_results.tsv.gz`; `_metalib/publish.py` does lead loci, forest data, plots and summary for all.
- Settings are `{method, params, common, include_statuses}`; project defaults keep parameters per method.
  `meta_analysis.method` column (migration `9b3e1c7d2a40`).
- **Typed parameters** (`pipeline/runner/params.py`) for QC steps and methods: forms in the pipeline editor and
  the meta-analysis builder are drawn from them; the API rejects wrong types, out-of-range values and unknown keys.

## Before/after QC numbers and meta-analysis outputs (2026-10-04, done)

- The runner profiles every QC run at both ends, whatever the pipeline (`qc_steps/_lib/profile.py`): the upload
  as received (through the column mapping) and the cleaned file. Per column (BETA, SE, P, EAF, N, INFO): count,
  missing, mean, median, SD, min, max. Stored in `result.json` (`profile`); shown in the dataset panel and the QC
  report. Chosen over enforced first/last steps because pipelines are editable lists and steps have no fixed places.
- Rows in and out of every step (`rows_before`/`rows_after`); the panel shows rows removed per step and the QC
  report has a rows-by-step table.
- Meta-analysis: each dataset's filter counts (missing BETA/SE, below MAF, palindromic), variants used and variants
  in the results; `datasets.tsv` (input order = direction-string order) and `README.txt` (every column, effect
  allele = ALT) downloadable with the results.
- Runs from before this change have no profile; re-run QC to get one.

## Default QC = the HERMES 3 DCM pipeline (2026-10-05)

The first default steps (row_check, harmonise_columns, value_ranges, alleles, liftover, reference_check, maf/info
filters, GWASHub's own LDSC) and the example `hcm/sarcomere_filter` were deleted; the validated Snakemake pipeline in
`hermes3_dcm` (git-ignored clone, commit ff35109) was split into steps. Default template:

| Step | Image | From hermes3_dcm | Notes |
|---|---|---|---|
| `gwashub/parse` (R) | `gwashub/base-r` | `gwas_qc_parse.R` | The only step that reads the upload. Recoding and validity rules as there; P = 0 → smallest double; fails when the P column is −log10 P |
| `gwashub/filter` (R) | `gwashub/base-r` | thresholds from `gwas_qc_parse.R` | Parameters; defaults reproduce it (chr 1-22/X/Y, \|β\| < 20, 0 < EAF < 1, EAF/N/INFO required) plus duplicate removal |
| `gwashub/harmonise_liftover` | `gwashub/vcf` (x86-64) | rules `gwas2vcf`, `liftover`, `vcf_to_tsv`; chr check in `gwas_qc_report.R` | The **edited** gwas2vcf (copied into the step), bcftools 1.22 +liftover (freeseek/score db4da22, as bioconda's plugin 1.22), dbSNP 157 rsIDs. Warns below 95% harmonised or lifted; never fails unless nothing is left |
| `gwashub/eaf_check` | `gwashub/vcf` in the template (its own image: `gwashub/base`) | EAF section of `gwas_qc_report.R` | 1000G frequencies; warns above 5% with \|ΔEAF\| > 0.2; removes nothing |
| `gwashub/ldsc` | `gwashub/ldsc` (x86-64) | LDSC in `gwas_qc_report.R` | The authors' bulik/ldsc (Python 2.7, commit 2fdeeb3), not ldscr; Pan-UKB LD scores in LDSC format + w_hm3.snplist; passes with a message until they're uploaded |
| `stats_summary`, `plots`, `qc_report` | `gwashub/base` | – | unchanged |

- **GWASHub format** (`_lib/common.py` `STANDARD`): `chr pos ea oa beta se p` + optional `eaf n ncase ncontrol rsid info`.
  Every step after `parse` reads and writes it; VCFs stay inside `harmonise_liftover`. Upload mapping gained
  `ncase`, `ncontrol` and `log10p` (P or −log10 P required).
- **Images:** `gwashub/vcf` and `gwashub/ldsc` are x86-64 (`platform: linux/amd64` in `image.yaml`): gwas2vcf's pinned
  Python 3.7 / pysam 0.15.2 / numpy 1.15.4 and LDSC's Python 2.7 have no ARM64 builds. All sources pinned by checksum.
  A QC run is four Batch jobs (base-r, vcf, ldsc, base).
- **AWS (not deployed yet):** steps declare `compute:` (vCPUs, memory) and each Batch job gets the largest of its
  steps; a second compute environment and queue for x86-64 images (M7i/R7i); 200 GB job volumes (500 GB in prod);
  12-hour job timeout. The dev database's pipelines still name the deleted steps: reset them (reseed) when deploying.
- **Locally:** steps run in their images (`GWASHUB_STEP_RUNNER=docker`, no network, current code mounted);
  `devtools/build_images.sh`; `dev.sh` builds missing images.
- **Synthetic references** under production names: bgzipped all-N FASTAs for both builds with the variants' alleles and
  30 bp random flanks (+ `.fai`, `.gzi`), a dbSNP-like VCF + index, 1000G-like frequencies, LDSC-format LD scores.
- **Known behaviour of gwas2vcf:** where an indel's shorter allele is the first base of the longer one, both
  orientations pass its REF check and it keeps the upload's; for those indels EA = ALT may not hold (β matches the EA).

## Meta-analysis: GWAMA as the default (2026-10-05)

- GWASHub's own inverse-variance method (`gwashub/ivw`) was deleted; **GWAMA is the default** (the HERMES 3 analyses'
  method: `-qt --indel_alleles`, as `meta_analysis_gwama.R`).
- **GWAMA parameters** (from `GWAMA --help`): model (`--random`), minimum datasets, a per-dataset MAF filter (default
  off, so a different cut doesn't need QC re-run), `--genomic_control`, `--genomic_control_output`, `--threshold`
  (direction shown below P) and `--sex`. Not exposed because the wrapper fixes them: `-qt`, `--indel_alleles`,
  `--no_alleles`, `--map`, the `--name_*` columns and `--filter` (the MAF option covers it).
- **METAL parameters** (from its HELP): scheme, minimum datasets, MAF filter, `GENOMICCONTROL`, `OVERLAP` + `ZCUTOFF`,
  `MINWEIGHT`, `MINMAXFREQ` (`eaf_min`, `eaf_max`), and the effect/SE print precision (default 8; METAL's own 4 rounds the
  results). Fixed by the wrapper: separator and column labels, `TRACKPOSITIONS`, `USESTRAND`, `LOGPVALUE`, `ANALYZE HETEROGENEITY`.
- **The programs' own files are downloadable** with the results ("GWAMA files" / "METAL files" in the settings box):
  GWAMA's console output, `.log.out`, `.err.out` and `.gc.out`; METAL's script, console output and `.info` file. A method
  lists them in its log (`files`), so consortium methods can do the same.
- Locally, methods run in their images too (`GWASHUB_STEP_RUNNER=docker`).
- The per-dataset genomic control and palindromic options of `_metalib.load_study` went with `ivw`.

## Lead loci by LD clumping; reference resources (2026-10-05)

- **Lead loci** of every meta-analysis come from PLINK 2 clumping (`_metalib/loci.py`), ported from `define_loci_plink.R`:
  candidates (autosomes, MAF ≥ 0.01, ≥ 2 datasets) → P < 5e-8 clumped at r² < 0.6 (independent significant SNPs) → clumped
  at r² < 0.1 (lead SNPs) → each lead's LD cloud (P < 0.05, r² ≥ 0.1, ±500 kb) bounds its locus, trying the other
  super-populations' panels for leads with no partner in the chosen one → loci merged within 250 kb; loci with no LD cloud
  get ±250 kb. All of these are settings (common to every method; EUR panel by default). Variants match the panels as
  `chr:pos:ref:alt` in either allele order. Significant variants not in the panel can't be clumped and are counted.
  Until the panels are uploaded, lead loci fall back to a 500 kb distance window and the results page says so.
  `lead_loci.tsv` now has one row per locus with `locus_start`, `locus_end`, `n_leads`, `n_signals`, `leads`.
- **PLINK 2** (v2.0.0-a.7.11, built from its tagged source, so it runs on ARM64) is in `gwashub/base` and every image
  built from it. Locally, the post-processing runs in the method's image too.
- **`resources/`**: where every reference file comes from (`catalog.yaml`), download/build scripts (`build/*.sh`) and a
  README; built files go to `local/resources/` and are uploaded as shared resources. A test fails if a step declares a
  resource the catalogue doesn't cover. The 1000G panels and frequencies come from the 2022 high-coverage GRCh38 callset
  (PLINK 2 files), with IDs set to `chr:pos:ref:alt`.
- **Signals:** `signals.tsv` keeps every independent significant SNP with its locus (`locus_id`), the lead SNP it
  clumped under, and whether it is a lead SNP or the locus's strongest; `lead_loci.tsv` has the `locus_id` to join on.
- **Locus plots** (`_metalib/regions.py`, `RegionPlot.vue`): for the selected locus, every variant in it (± a flank)
  by position and −log10 P, coloured by r² with the lead in the chosen 1000 Genomes panel (LocusZoom colours), the lead
  as a diamond, other signals outlined, the locus shaded, and a GENCODE 50 gene track (canonical exons, strand). One
  `region_<locus_id>.json` per locus, loaded when the locus is picked.
- **Genes:** `genes_b38.tsv.gz` from GENCODE 50 basic (`resources/build/genes_gencode.sh`); lead loci get
  `nearest_gene`, `nearest_pc_gene` and `genes_in_locus` (protein-coding genes overlapping the locus).
- Synthetic LD panels (founder-haplotype blocks) and genes are built by `devtools/synth.py`, the panels with the same
  PLINK 2 commands.
- The per-dataset QC step `stats_summary` still finds its lead loci by distance (a QC indicator).

## Real references on dev, dev reset, storage paths (2026-10-05, evening)

- **Real references on AWS dev:** all 37 files from `local/resources/BUILT.tsv` (~26 GB incl. dbSNP 157) uploaded with
  `devtools/aws_upload_resources.py` (straight to S3 from the laptop, content-addressed paths
  `resources/shared/<key>/<sha256 prefix>/<key>`, then a manifest) and registered with the new admin command
  `add-resources MANIFEST` (titles, builds, sources and licences from `resources/catalog.yaml`). The catalogue's
  `build` is now only `GRCh37`, `GRCh38` or `Any` (tested).
- **dbSNP 157** built (`dbsnp157_b38.sh`, native ARM64 bcftools 1.22): 1.17 bn records after splitting multi-allelics.
- **Dev database reset** (old pipeline versions named deleted steps). Only the `testing` project was kept (settings,
  members, cohorts; Cognito logins survived and re-link by email); no demo data on dev. `testing` pipeline v2 pins
  the 21 QC references.
- **Storage paths** now `uploads/<project>/<dataset>-<UTC time>/<file>`, `qc/<project>/<dataset>/<run>-<UTC time>/`,
  `meta/<project>/<id>-<UTC time>/`: findable by project and dataset, unique across database resets. The dataset
  panel and meta-analysis page end with a small "Reference for help requests" line (ids + folder) and a copy button,
  for everyone, so users can send it when something goes wrong.
- **Costs:** ECR keeps the 5 newest images per repository (lifecycle policy set by the deploy script). Batch instance
  sizes limited to large..4xlarge (Spot had given a 2 vCPU job an r7g.8xlarge; deploy pending); `MAX_COMPUTE`
  (16 vCPU, 120 GB) refuses step/method requests no instance could run. Usage so far is covered by credits
  (≈ $8.50 for 5 days of dev in October). Cheaper options discussed, not decided: research credits; a serverless
  redesign (Lambda API and worker, Aurora Serverless v2 scaling to zero, no NAT); fck-nat; dev sleep/teardown;
  single-AZ prod database.
- **Synthetic datasets don't fit the real references:** they were simulated on the synthetic genome, so on dev only
  ~50% of their SNPs match the real GRCh37 base (chance for random allele pairs) and harmonise_liftover warns
  (fake UKB: 45.5% harmonised, switches split 50/50). Smoke-test dev with a real file (e.g. the public HERMES 2019
  HF summary statistics). Idea, not done: when few variants match, try the other build's reference too and say
  which build fits (or neither).
- **Cleanup:** removed `pipeline/runner/aws/s3io.py`, `pipeline/qc_steps/fixtures/`, unused helpers, the old
  `docs/PLAN.md`, `docs/NEXT_STEPS.md` and `docs/qc-pipeline-README.md`. New: `docs/ARCHITECTURE_IMAGE_PROMPT.md`;
  posters in `docs/images/` shown in the README (the stack poster needs regenerating: no vcf/ldsc images, says IVW).

## Review of the previous site's issue list (2026-10-04)

| Issue | Where it stands |
|---|---|
| QC metrics per file (counts, mean, median, SD, range of BETA/SE/P/EAF, λGC) | Done: column profile before and after QC, λ metrics |
| Filters applied before meta-analysis, and the numbers after filtering | Done: rows removed per step; per-dataset meta-analysis filter counts |
| Downloadable QC'd files | Done (dataset panel) |
| Alleles coded A/T for every variant | Done: real alleles throughout; effect allele = ALT |
| EAF, heterogeneity P, direction, effect/other allele naming | Done; N_EFF deliberately not added (2026-10-04) |
| Order of datasets for the direction column | Done: `datasets.tsv`, numbered table, README |
| Forest plots of lead loci | Done |

Small fixes the same day: QC status column restored in both dataset table views (with a missing-metadata icon);
Overview table uses the smaller Details text; meta-analysis settings moved into one info box under the title;
download buttons appear only for files a meta-analysis has (older ones lack `datasets.tsv`/`README.txt`).

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
- AWS: containers couldn't write `/tmp` with a read-only root filesystem (rolled back; deferred).
- AWS: the worker crashed on SQS errors (now retries); the "latest run" check used a stale list.

## Not done / known limitations

- **AWS hardening and operations:** see "Not done yet" in `docs/AWS_FULL.md` (WAF, custom domain,
  locked-down environment for consortium steps, alarms, malware scanning of uploads, CI/CD,
  read-only root filesystem, Cognito email through SES for more than ~50 invitations a day).
- **Production** (gwashub-prod) is configured in `infra/gwashub_infra/config.py` but not deployed.
- **Not pushed to GitHub, and the work since the 2026-10-03 commits is uncommitted.** `.github/` (CODEOWNERS with
  placeholder team names, PR template) is ready for when it goes on GitHub.
- The HERMES 3 pipeline hasn't been compared with the HPC (Snakemake) results on real cohorts yet.
- Resource import-from-URL is not implemented (upload only).
- Custom-step test runs from the browser were removed with the git-repo custom steps; step tests run in CI.
- `qc-pipeline` on GitHub not archived (your call).
