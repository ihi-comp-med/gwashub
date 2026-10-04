# Next steps: SGC features and sign-in polish

Written 2026-10-04. Each item has a **Response** line: write yes / no / later, or notes, and I'll read
this file and action it.

How to answer, for example:
> **Response:** yes, but only male/female, not "both"

---

## Agreed (2026-10-04): done, deployed to dev on 2026-10-04

1. **Datasets table: Overview / Details toggle** (1.1, 1.4). *Overview*: cohort, dataset, phenotype, ancestry, sex,
   uploaded by, date, QC status. *Details* (smaller font): cohort, dataset, phenotype, ancestry, sex, cases,
   controls, QC metrics (λGC, N variants, hits at 5e-8, liftover lifted / unmapped / strand flips, and the 2.3 metrics),
   QC status. Plus the pipeline version each dataset last ran with (1.3).
2. **QC metrics table in each dataset's panel**, above the Manhattan / QQ plots (1.1).
3. **Bulk re-run QC** on selected datasets (reviewers) (1.3).
4. **Phenotype codes + descriptions**, with CSV import; existing names become codes (1.5).
5. **More QC metrics** (λ1000, λ at MAF ≥ 1%, hits at 1e-5) if easy (2.3).
6. **Cohorts as records** (2.1): strictly per project (projects are isolated; only resources are shared).
   Anyone in the project can create a cohort at upload, warned about similar existing names. The datasets table
   shows the cohort name (filterable). Study, genotyping, imputation and genotyping-QC metadata live on the
   cohort and pre-fill each dataset, which can override them; participants and age/sex distribution are per dataset.
7. **Metadata field catalogue** (3.1): every field (HERMES and SGC) defined in the repo; per project, which fields are
   shown and which are mandatory are stored in the database and can be changed later. Platform admins do the
   initial set-up; the project's **reviewers** can then change it. Datasets missing a mandatory field show
   "metadata incomplete" (nothing is blocked).
8. **LDSC intercept step** `gwashub/ldsc` (3.3): Pan-UKB per-ancestry LD scores; default `on_fail: flag`; **in the
   default pipeline**, and until the LD-score files are uploaded it passes with a **warning** ("reference files
   missing") instead of failing the run (to revisit later).
9. **Resources are added by platform admins only** (3.3): shared and project resources, through an admin upload
   dialog that sends large files in parts straight to S3. Reviewers no longer upload resources; they still see
   them and the pipeline pins their versions.
10. **Sign-in page in HERMES blue** (Cognito branding colours only) (4.2).

Estimated effort for all ten: one working session (about 4–8 hours of build time, plus ~6-minute deploys,
your checks in the browser, and downloading the LDSC reference files).

Not now: 1.2 exclude from meta-analysis, 1.6 recent activity, 2.2 meta-analysis targets, 3.2 cohort data files,
4.1 authenticator name. Principle (3.4): every project type lives in GWASHub and is configured per project.

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

## Phase 1: general and quick

### 1.1 QC overview page
One table of every dataset in a project, built from the step logs already stored (no new computation):
λGC, N variants, hits at 5e-8, liftover lifted / unmapped / strand flips, status. Filter, sort, export
CSV; Manhattan/QQ thumbnails; click through to the dataset. (Covers SGC #5 and #6.)

> **Response:** yes, but should go at the bottom of the dataset view, above the plots / lead loci

> **Clarification needed:** 1.1 was planned as a *project-wide* table comparing all datasets side by side.
> "Bottom of the dataset view, above the plots / lead loci" sounds like the panel for *one* dataset. Which do you mean?
> (a) a QC-metrics block inside each dataset's panel, above its plots and lead loci; or
> (b) the all-datasets comparison table, placed at the bottom of the **Datasets** page under the list; or both?
>
> **Answer:** I misunderstood - maybe there should be a toggle switch at the top of the datasets table [Overview / Details]; overview just has Cohort/Dataset/Phenotype/Ancestry/Sex/Uploaded by/Date/QC status; Details has Cohort/Dataset/Phenotype/Ancestry/Sex/[QC stats]/QC status (and is smaller font to fit onto the page???). Still would be good to add a per dataset QC metrics table at the bottom of the individual dataset view (above the manhattan and QQ plot).

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

> **Clarification needed:** I read this as: add **cases** and **controls** columns (totals) to the existing
> "Dataset summary" table on the project overview, which already groups by phenotype × ancestry × sex (with the
> grouping dropdown), and use a smaller font so it fits. Right? And should the totals count every dataset, or
> only those that passed QC (or both, e.g. "all / passed")?
>
> **Answer:** see above, cases and control counts go to the Details table shown on toggle. 

### 1.5 Phenotype codes and descriptions
Upload options become *code + description* (e.g. `HF` / "All-cause heart failure"), with CSV import;
shown in the upload form. Existing names become codes. (SGC #3.)

> **Response:** yes. good idea.

### 1.6 Recent activity card
Latest uploads, QC results and reviews on the project overview. (SGC #11.)

> **Response:** no leave this. 

---

## Phase 2: general, larger

### 2.1 Cohorts as records
A Cohort table per project: cohort metadata entered once and shared by its datasets; a Cohorts page
listing each cohort's files and status; "download all files" as a list of short-lived links (zipping
multi-GB files on the fly isn't practical). Existing datasets are grouped by their cohort name. (SGC #1.)

> **Response:** yes, add cohort information to the dataset table so this can be filtered on. 

> **Clarification needed:**
> 1. Is a cohort **per project** (UK Biobank in HERMES and UK Biobank in HCM are separate records) or **shared across
>    projects** (one UK Biobank record, metadata entered once)? Shared saves re-entry but means deciding who may edit it.
> 2. At upload, the uploader picks an existing cohort or creates a new one. Should creating a cohort be open to
>    uploaders, or reviewers only?
> 3. "Cohort information" in the datasets table: which cohort fields do you want as filterable columns
>    (e.g. country, genotyping array, imputation panel)? Or just the cohort name plus a cohort filter?
> 4. Which metadata belongs to the **cohort** (same for all its datasets, e.g. country, ethics, genotyping, imputation)
>    and which to each **dataset** (e.g. case definition, N, sex)? I'd propose: study, genotyping, imputation and
>    genotyping-QC sections at cohort level; participants, age/sex distribution at dataset level.
>
> **Answer:** A cohort is strictly per project; projects are completely isolated (other than being able to share resources). Creating a cohort should be open to all, but warn if similar name exists / tell them to make sure its not in the list. just the cohort name in the table. I'd propose: study, genotyping, imputation and genotyping-QC sections at cohort level; participants, age/sex distribution at dataset level - I agree, this should auto populate for a dataset, but be able to be over-ridden within a dataset, if for some reason a different imputations was used for a phenotype (this should be rarely needed in practice).

### 2.2 Meta-analysis targets
Presets (all / sex-stratified / ancestry-stratified) that pick eligible, non-excluded, QC-passed datasets;
INFO minimum; run label; per-input table ("variants in / used, used or skipped and why"); direction
concordance in the summary; several runs per target. (SGC #7.)

> **Response:** leave for now

### 2.3 More QC metrics
In the `stats_summary` step: λ1000 (λ scaled to 1,000 cases and 1,000 controls), λ at MAF ≥ 1%, hits at
1e-5. They show on the QC overview (1.1). (SGC #6.)

> **Response:** ok add if easy, drop if not. 

---

## Phase 3: per-project switches, mostly for SGC

### 3.1 Metadata templates per project
`metadata_schema.py` becomes named templates ("HERMES", "SGC GWAS cohort"); each project picks one or
both. The upload form and dataset viewer already draw from the schema, so this is mostly data. (SGC #10.)

> **Response:** I think a better way would be to specify all possible meta-data options in the repo, but then when a project is set up the admin can choose which ones will be shown - I guess these choices will be stored somewhere in the database? we should be able to say if they are mandatory when uploading a dataset. they should be able to be updated later. 

> **Clarification needed:** agreed on the approach (a catalogue of all fields in the repo; per project, the shown
> fields and which are mandatory stored in the database; editable later). Three details:
> 1. Who configures this per project: **platform admins only**, or the project's **reviewers** too?
> 2. When a field becomes mandatory after datasets were uploaded: should existing datasets just show as
>    "metadata incomplete" (my suggestion), or be blocked from something (e.g. meta-analysis) until filled in?
> 3. Should the catalogue include the SGC-only fields now (country, data freeze date, lead collaborator, authors,
>    publication requirements, public summary stats, ethics, ascertainment, case coding system, ancestry method,
>    association-testing methods), so any project can switch them on?
>
> **Answer:** Only admins configure a project; "metadata incomplete" (my suggestion) is good; Should the catalogue include the SGC-only fields now - yes it should

### 3.2 Cohort data files (SGC "Phase 0")
A per-project "cohort files" feature with configurable file types; for SGC: case/control counts and
co-occurrence (male and female) plus a description file, each with column mapping like GWAS uploads.
Validation runs as a Batch job with cross-file checks; the cohort page shows progress. (SGC #2.)

> **Response:** leave this. 

### 3.3 LDSC intercept step
A new optional QC step `gwashub/ldsc`: needs LD-score reference files (a new shared resource) and
probably its own image. Useful for every project. (SGC #6.)

> **Response:** yes add this, I think a bunch of resources will need uploading to enable. further to this, I need a resource upload dialog available to admins to upload and define the global resources. 

> **Clarification needed:**
> 1. LD-score reference: I'd use the **Pan-UK Biobank LD scores** (per ancestry: AFR, AMR, CSA (South Asian), EAS,
>    EUR, MID), so non-European datasets get a matching reference; the classic alternative is the European-only
>    1000 Genomes set from the LDSC authors. OK to use Pan-UKB (and accept that some ancestry labels, e.g. "Multi",
>    won't have a match and will skip the step with a message)?
> 2. Default `on_fail` for the new step: **flag** (warn on a high intercept, never block)? And add it to the default
>    pipeline for new projects, or leave it as an optional step projects add themselves?
> 3. Global resources dialog: for **platform admins** only (project reviewers keep adding project resources as now)?
>    Reference files can be several GB, so it will upload in parts straight to S3 like datasets; the current
>    project-resource upload goes through the API and will switch to the same method.
>
> **Answer:** 1. yes thats fine; flag is the correct response; Global resources dialog: for **platform admins** only please; reviewers shouldnt be able to upload, this will form part of the custom script submission / PR process. 

### 3.4 Questions to settle before Phase 3
- Would SGC (skin) be a project inside GWASHub, using these switches?
- Do their counts / co-occurrence data have disclosure rules (e.g. minimum cell counts to display)?

> **Response:** the idea is that all projects, or any type, are within GWAShub, and that the site is sufficiently customisable to meet different requirements. 

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

> **Response:** ignore this, its fine as it is. 

### 4.2 Style the sign-in page like the website
Managed-login branding, defined in CDK so it's versioned and applies to prod: HERMES blue `#303880`
buttons and links, our page background, GWASHub/HERMES logo and favicon, matching corner rounding and
spacing, light/dark mode. It can't use our own CSS or the Inter font, so it won't be pixel-identical.
About 15 minutes for the colours. (Fully custom sign-in pages are possible but
take several days and move sign-in security onto us; not recommended.)

> **Response:** add the blue if easy, but no need to create a fully customised page. 

---

## Anything else

> **Response:** none.
