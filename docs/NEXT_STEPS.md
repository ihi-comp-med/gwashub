# Next steps: SGC features and sign-in polish

Written 2026-10-04. Each item has a **Response** line: write yes / no / later, or notes, and I'll read
this file and action it. Effort estimates are rough.

How to answer, for example:
> **Response:** yes, but only male/female, not "both"

---

## Background: what SGC has that GWASHub doesn't

From a review of the Skin Genetics Consortium (SGC) pages in
[broadinstitute/data-registry](https://github.com/broadinstitute/data-registry) (`pages/sgc/*`,
`components/SGC*`, `utils/sgcMa.js`).

| # | SGC feature | What it does | GWASHub today | Useful generally? |
|---|---|---|---|---|
| 1 | Cohorts as records (Cohorts page, cohort view/edit, "Download all files") | A cohort has its own metadata and groups all its files | "Cohort" is a text field on each dataset; metadata is per dataset ("copy from a previous upload") | Yes |
| 2 | Phase 0 cohort files | Male/female case and control counts per phenotype, male/female phenotype co-occurrence files, a cohort description file; each with column mapping, a progress checklist and "inter-file validations" | Only GWAS files | SGC-specific |
| 3 | Phenotype dictionary | Phenotype code + description, managed list, "View valid phenotypes" during upload, downloadable | Phenotype names only (Upload options) | Yes |
| 4 | Phenotype case totals | Total cases, controls and cohorts per phenotype, male / female / both | Dataset counts by QC status; no N totals | Yes |
| 5 | GWAS files summary | Every file with liftover status, per-chromosome liftover breakdown, "run liftover on all needing it", download unmapped variants, headline totals | Liftover is a QC step; results per dataset only | Yes |
| 6 | GWAS QC plots | Every file with λGC, λ1000, λ (MAF ≥ 1%), N variants, hits at 5e-8 and 1e-5, LDSC intercept, Manhattan/QQ on expand | Metrics and plots per dataset only; no cross-dataset comparison | Yes |
| 7 | Meta-analysis targets | Presets (all individuals / sex-stratified / ancestry-stratified) that pick eligible QC-passed files; MAF and INFO minimums; run labels; several runs per group; per-cohort "variants in / used, used or skipped and why"; caveats; direction concordance | You pick datasets by hand; settings, summary, lead loci, forest plots | Yes |
| 8 | Meta-analysis ignore list | "Exclude from meta-analysis" with reason, who and when; bulk CSV; re-include; runs report "Ignored" vs "Skipped" | Reviewers can reject a dataset or filter by status | Yes |
| 9 | Bulk file actions | "Validate all unvalidated", queued status, QC errors, full error log download | One dataset at a time | Yes |
| 10 | SGC cohort metadata fields | Country of ascertainment, data freeze date, lead collaborator, contributors as authors, publication requirements, summary stats public?, ethics approval, ascertainment, case coding system, ancestry-assignment method, association-testing methods (genotyping/imputation QC mostly overlaps HERMES) | HERMES field set, the same for every project | Per project |
| 11 | Recent uploads dashboard (HERMES) | "Recently added" datasets | Overview summary only | Yes (small) |
| 12 | User management | Create users with roles | Done (invite, roles, remove) | n/a |

Proposal: most items go in for **every project**. Items 2 and 10 get a per-project switch: a small
project "features" setting (reviewers / platform admins, next to Upload options) and a choice of
metadata template.

---

## Phase 1: general and quick (≈ 3–4 days in total)

### 1.1 QC overview page
One table of every dataset in a project, built from the step logs already stored (no new computation):
λGC, N variants, hits at 5e-8, liftover lifted / unmapped / strand flips, status. Filter, sort, export
CSV; Manhattan/QQ thumbnails; click through to the dataset. (Covers SGC #5 and #6.)

> **Response:** yes, but should go at the bottom of the dataset view, above the plots / lead loci

### 1.2 Exclude from meta-analysis
A reviewer action per dataset with a reason (records who and when); a project list page; bulk CSV;
re-include. Meta-analysis runs report "Ignored" vs "Skipped". (SGC #8.)

> **Response:**  ignore this for now. 

### 1.3 Bulk actions on the Datasets table
Re-run QC on selected datasets, or on all that ran an older pipeline version; download QC error logs.
(SGC #9.)

> **Response:** yes, be able to select datasets and rerun the QC for this. also document in the datasets table which QC pipeline version was last used for the dataset.

### 1.4 Sample-size totals
Cases, controls and number of datasets per phenotype × sex (and × ancestry), on the project overview.
(SGC #4.)

> **Response:** yes add the controls too, might need to make the table font smaller to accommodate changes. 

### 1.5 Phenotype codes and descriptions
Upload options become *code + description* (e.g. `HF` / "All-cause heart failure"), with CSV import;
shown in the upload form. Existing names become codes. (SGC #3.)

> **Response:** yes. good idea.

### 1.6 Recent activity card
Latest uploads, QC results and reviews on the project overview. (SGC #11.)

> **Response:** no leave this. 

---

## Phase 2: general, larger (≈ 1–1.5 weeks)

### 2.1 Cohorts as records
A Cohort table per project: cohort metadata entered once and shared by its datasets; a Cohorts page
listing each cohort's files and status; "download all files" as a list of short-lived links (zipping
multi-GB files on the fly isn't practical). Existing datasets are grouped by their cohort name. (SGC #1.)

> **Response:** yes, add cohort information to the dataset table so this can be filtered on. 

### 2.2 Meta-analysis targets
Presets (all / sex-stratified / ancestry-stratified) that pick eligible, non-excluded, QC-passed datasets;
INFO minimum; run label; per-input table ("variants in / used, used or skipped and why"); direction
concordance in the summary; several runs per target. (SGC #7.)

> **Response:**

### 2.3 More QC metrics
In the `stats_summary` step: λ1000 (λ scaled to 1,000 cases and 1,000 controls), λ at MAF ≥ 1%, hits at
1e-5. They show on the QC overview (1.1). (SGC #6.)

> **Response:**

---

## Phase 3: per-project switches, mostly for SGC (≈ 1–2 weeks)

### 3.1 Metadata templates per project
`metadata_schema.py` becomes named templates ("HERMES", "SGC GWAS cohort"); each project picks one or
both. The upload form and dataset viewer already draw from the schema, so this is mostly data. (SGC #10.)

> **Response:**

### 3.2 Cohort data files (SGC "Phase 0")
A per-project "cohort files" feature with configurable file types; for SGC: case/control counts and
co-occurrence (male and female) plus a description file, each with column mapping like GWAS uploads.
Validation runs as a Batch job with cross-file checks; the cohort page shows progress. (SGC #2.)

> **Response:**

### 3.3 LDSC intercept step
A new optional QC step `gwashub/ldsc`: needs LD-score reference files (a new shared resource) and
probably its own image. Useful for every project. (SGC #6.)

> **Response:**

### 3.4 Questions to settle before Phase 3
- Would SGC (skin) be a project inside GWASHub, using these switches?
- Do their counts / co-occurrence data have disclosure rules (e.g. minimum cell counts to display)?

> **Response:**

---

## Sign-in polish (Cognito)

### 4.1 Authenticator app name
The entry in your authenticator app is labelled with the sign-in domain
(`gwashub-dev-203248996074.auth.…`); Cognito has no separate setting for that name.
- **Now, yourself:** rename the entry in your authenticator app (only a local label; codes unaffected).
- **Option A (small CDK change):** a shorter domain prefix such as `gwashub-dev` → `gwashub-dev.auth.…`
  (prefixes must be lower case). People already registered keep the old label until they rename it
  (today only you).
- **Option B (later):** a custom sign-in domain such as `login.gwashub.org`, added with the site's own domain.

> **Response:**

### 4.2 Style the sign-in page like the website
Managed-login branding, defined in CDK so it's versioned and applies to prod: HERMES blue `#303880`
buttons and links, our page background, GWASHub/HERMES logo and favicon, matching corner rounding and
spacing, light/dark mode. It can't use our own CSS or the Inter font, so it won't be pixel-identical.
About half a day; I'd do it together with 4.1 Option A. (Fully custom sign-in pages are possible but
take several days and move sign-in security onto us; not recommended.)

> **Response:**

---

## Anything else

> **Response:**
