# Bulk upload: design and decisions

Agreed 2026-10-07 after three rounds of questions (answers at the end). **Built 2026-10-07**: `api/gwashub_api/routers/bulk.py`, `routers/tokens.py`, `client/gwashub_upload.py`, the Account page; tried end to end against the local site (check, upload, resume after an interruption, duplicates refused).

## In short

Any project member downloads one Python script from the site, fills in an Excel manifest (one row per file, every value
spelled out, cohort data repeated on each row, no guessing), and runs:

```bash
python3 gwashub_upload.py template manifest.xlsx  # an empty manifest workbook (README sheet, Manifest sheet)
python3 gwashub_upload.py check  manifest.xlsx     # validates everything, uploads nothing
python3 gwashub_upload.py upload manifest.xlsx     # all-or-nothing check again, then uploads; resumes if interrupted
python3 gwashub_upload.py status manifest.xlsx     # what happened to each row
```

Every upload, by script or through the website, lands as **pending**: QC starts only when someone clicks Start QC on
the website, so a mistake noticed after uploading costs nothing but a delete.

## Decisions

| Topic | Decision |
|---|---|
| Route | A Python script only; no browser bulk upload |
| Sign-in | Personal API tokens: one project, upload only, 30 days |
| Who | Any member of the project |
| Manifest | An Excel workbook (changed from TSV after the first live try, 2026-10-07: Excel quoted the TSV's comment lines): a README sheet, the Manifest sheet with drop-down lists, read by the server; cohort-level data repeated on every row of that cohort; no second table |
| Column mapping | No suggestions or guessing: the manifest names every column; any mismatch fails the whole check |
| New cohorts | Allowed: created from the manifest's cohort columns |
| Duplicates | Refused; the message says to delete the existing dataset first |
| Grouping | No batch record |
| QC | Never starts on upload (script or website): datasets are **pending** until someone clicks Start QC on the website. The script can't start QC |

## How it works

### 1. A token

On the site: **Account → API tokens → New token**, choose the project, name it ("UKB files, Oct 2026"). The token is
shown once. It:
- belongs to you and acts with your role in that project, never more;
- works only for that project and only for uploading (template, check, start an upload, send parts, complete, read the
  status of your own uploads); it can't start QC, delete, review, edit, change pipelines or members, or read other
  people's datasets;
- expires after 30 days; you can revoke it any time; the site stores only a hash;
- is recorded on each dataset it uploaded (which token, when), and its uses are logged.

### 2. The manifest

An Excel workbook, one row per file on its Manifest sheet. Column names are fixed keys, not labels. `template` writes it
for the project: a README sheet (steps, every column with an example, file paths, phenotypes with their trait types,
allowed values, the cohorts on the site), and the Manifest sheet with every column (optional ones included), help on
hover, colour by group (required, optional, cohort details, dataset details) and drop-down lists for the fixed choices.

| Column | Required | What it holds |
|---|---|---|
| `file` | yes | Path to the file, relative to the manifest |
| `cohort` | yes | The cohort's name: an existing cohort, or a new one |
| `phenotype`, `ancestry`, `sex` | yes | Exactly as the project lists them |
| `build` | yes | `GRCh37` or `GRCh38` |
| `effect_type` | yes | `beta` or `odds_ratio`: what `col_effect` holds (`beta` only for quantitative traits) |
| `n_cases`, `n_controls` | case/control traits | Whole numbers |
| `n_total` | quantitative traits | Whole number |
| `col_chr`, `col_pos`, `col_ea`, `col_oa`, `col_effect`, `col_se`, `col_eaf` | yes | The **exact header** of that column in the file |
| `col_p` or `col_log10p` | one of them | The exact header of the P or −log10 P column |
| `col_n`, `col_ncase`, `col_ncontrol`, `col_rsid`, `col_info` | no | Exact headers, if the file has them |
| `meta_<key>` (cohort-level fields) | as the project requires | The cohort's data (study, genotyping, imputation…), **repeated on every row of that cohort** |
| `meta_<key>` (dataset-level fields) | as the project requires | That dataset's own data |
| `sha256` | no | If given, the script checks the file against it before uploading |

Empty rows are skipped; empty cells are empty values; date cells are read as dates. Unknown columns are errors, so a
typo can't be silently ignored. For case/control phenotypes `n_total` is optional and, if given, must equal
`n_cases + n_controls`; for quantitative ones `n_total` is required and `n_cases` and `n_controls` must be empty.

**Cohort columns, the rules** (so the repeated values can't contradict each other or the site):
- **New cohort:** every row of that cohort must carry the same cohort-level values; the cohort is created with them.
  Rows that disagree fail the check ("row 7 and row 12 give different genotyping arrays for cohort UKB").
- **Existing cohort:** empty cells take the cohort record's values; a filled cell must equal the record, otherwise
  the check fails ("row 4: genotyping array differs from cohort UKB's record; leave it empty to use the record, or
  change the cohort on the site"). The manifest never changes a cohort.
- Required cohort-level fields must be filled for a new cohort, and may be left empty for an existing one that already
  has them.

### 3. `check`: fail hard, all at once

The script reads the manifest and each file's header line (gzipped or not), then sends the rows and headers to the
server's dry-run endpoint. Nothing is created. It reports **every** problem, each with its row number, and exits with
an error if there is even one:

- unknown or missing manifest columns; empty required values;
- phenotype, ancestry, sex, build or effect type not allowed in the project; counts missing or not whole numbers;
- a `col_*` header not found in the file, or the same header used twice; delimiter not tab, comma or space;
- required metadata missing, values of the wrong type or not among a field's choices;
- cohort-level values that disagree between rows of one cohort, or with an existing cohort's record;
- **duplicates, refused:** two rows with the same file or the same cohort × phenotype × ancestry × sex; or a row
  matching a dataset already in the project (same combination, or same size and sha256), with the message "matches
  dataset 123 (UKB · HF · EUR · all): delete it on the site first, or remove this row";
- a file missing, empty or unreadable.

The rules live on the server (the same `validate_upload` the upload wizard uses), so the script and the website can't
disagree.

### 4. `upload`

1. Runs `check` again; stops if anything fails. **Nothing is uploaded unless every row passes.**
2. Creates the new cohorts, then uploads the files a few at a time with the existing multipart flow (start → part URLs
   → PUT parts → complete), straight to S3, with a progress line per file.
3. Keeps a state file next to the manifest (`manifest.xlsx.state.json`: dataset id, parts done). If the connection
   drops or the laptop sleeps, running `upload` again sends only the missing parts of unfinished files and skips
   finished ones. The server already allows fresh part URLs for an unfinished upload.
4. Each completed upload becomes a dataset with status **pending**.

### 5. Pending datasets and starting QC (website)

- **Pending** is a new dataset status: uploaded, metadata recorded, QC not started. It shows in the datasets table
  (its own colour and filter) and in the project overview counts.
- **Every upload lands as pending**, including single uploads through the website's upload wizard. The wizard's last
  screen says so and offers **Start QC now**, so a single upload is still one click from running.
- **Start QC** (website only): the uploader for their own datasets, or a reviewer, selects pending datasets and clicks
  Start QC, or starts one from its panel. QC uses the dataset's pipeline at that moment.
- A pending dataset can be edited (metadata, counts, build: no automatic re-run while pending) and deleted; a bulk
  **Delete** for selected pending datasets makes it cheap to throw away a bad upload.

### 6. `status`

Lists each manifest row with its dataset id, status (uploading, pending, processing, passed, for review, failed) and a
link to it on the site.

## What gets built

| # | Piece | Where |
|---|---|---|
| 1 ✅ | **Pending status** (done 2026-10-07): upload completion without starting QC (script and wizard), Start QC (single and bulk; uploader or reviewer), wizard's "Start QC now", table filter and counts, bulk delete; build edits on pending datasets don't re-run | `models.py`, `routers/datasets.py`, upload, datasets and overview pages, dataset panel |
| 2 ✅ | **API tokens** (done 2026-10-07): table (hash, user, project, name, created, expires, revoked, last used), an `X-GWASHub-Token` header (not `Authorization`, which CloudFront drops from uncached GETs) in `auth.py` with the scope enforced, Account page to create and revoke, token recorded on datasets, migration | `auth.py`, new router, `web/app/pages/account.vue` |
| 3 ✅ | **Dry-run endpoint** `POST /projects/{pid}/bulk/check`: rows + headers → every problem by row, cohort consistency, duplicates | `routers/datasets.py` (reusing `validate_upload`) |
| 4 ✅ | **Template endpoint** `GET /projects/{pid}/bulk/template` and cohort creation for the upload | same, `routers/cohorts.py` |
| 5 ✅ | **The script** `gwashub_upload.py`: template, check, upload (parallel, resumable), status; standard library plus `requests`; downloadable from the site with the server address filled in | new `client/` folder |
| 6 ✅ | **Tests**: token scope (can't start QC, delete, review or reach another project), expiry and revocation; every check; cohort rules; duplicates; pending → Start QC; resuming an interrupted upload; existing tests updated for pending | `api/tests/` |
| 7 ✅ | **Docs**: a "Bulk upload" help page on the site, README, PROGRESS, CLAUDE.md | |

Rough size: about a week. Piece 1 also touches many existing tests, which assume QC starts on upload.

## Things to know

- **Tokens and the dev login:** tokens work the same locally and on AWS; the local pick-a-user login stays local-only.
- **Hours-long transfers:** S3 multipart uploads stay open until completed or aborted. The bucket's lifecycle rule
  removes parts of uploads abandoned for 7 days; `status` shows unfinished ones and `upload` resumes them.
- **QC load:** starting QC on 200 pending datasets queues 800 Batch jobs (four per run); they finish over hours. The
  cost is the same as uploading one by one.
- **Per-dataset overrides of cohort values** are still possible after upload, on the website, as now.

---

## Answers

**Third round**
- G1 A cohort-level value that differs from an existing cohort's record: (a) fail the check.

**Second round**
- F1 Duplicates: refuse and say they need to delete.
- F2 New cohorts: yes, allow new cohorts; but why a separate table? Copy all the cohort-level data down the rows (a wide
  spreadsheet).
- F3 Wizard uploads: also land as pending.
- F4 Starting QC: website only.
- F5 Cohort overrides: does the cohort data duplicated per row solve this? (Mostly; see G1.)

**First round**
1. Route: just a Python script, keep things simple (B).
2. Language: Python.
3. Tokens: 30 days, limited to project and uploading.
4. Who: anyone who is a project member.
5. Manifest: TSV only. (After the first live try: an Excel workbook with a README sheet; optional file columns as real
   columns; `col_effect` with `effect_type` beta or odds_ratio; the trait type shown separately from the phenotype.)
6. Mappings: no guessing; users must know exactly what they are uploading; fail hard on errors.
7. New cohorts: "no allow cohorts to be created" (clarified in F2: allowed).
8. Batch record: no; not sure how it would be useful.
9. Duplicates: "yes definitely" (clarified in F1: refuse).
10. Other: don't run QC immediately; list as pending, so lots of QC doesn't start if an error becomes apparent.
