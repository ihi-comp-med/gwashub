# QC step output: plan

Status: **implemented** (2026-10-07). Decisions are at the end.

Four changes:

1. The dataset QC view shows each step's log in the shape of the step contract (as the pipeline page does for
   `--input --resources --params --log`), with clickable, downloadable artifacts.
2. `parse` writes the rows it removes as an artifact, with the reason.
3. `filter` writes the rows it removes as an artifact, with the reason.
4. `harmonise_liftover` writes the rows it removes as an artifact, with the stage and reason.
5. `eaf_check` gets a parameter to remove variants with a large EAF difference (default: keep them).

## Where things stand

- **Step log** (`--log`): `{"step", "verdict", "metrics", "messages", "artifacts"}`; each artifact is
  `{"type": "image|table|text|report", "path", "label"}` relative to `$GWASHUB_ARTIFACT_DIR`
  (`pipeline/qc_steps/_lib/common.py` `write_log`; the R steps write the same JSON themselves).
- **Runner** (`pipeline/runner/executor.py`) copies `metrics`, `messages`, `artifacts` into `result.json` per step,
  with `rows_before`, `rows_after`, `duration`, `dir`.
- **Dataset QC view** (`web/app/components/DatasetPanel.vue`, "Processing steps"): per step a header (title, code,
  output, −removed, rows, duration, verdict tag), then every message as a paragraph, then the first 4 metrics inline
  (`+N more`). Artifacts only appear inside the "View output" dialog (`StepOutputViewer.vue`), mixed with the step
  log, console output and undeclared files.
- **Artifact files** are served by `GET /api/datasets/{id}/files/artifact?path=…` (preview up to 20 MB; `download=1`
  for a direct or presigned download). `.gz` files are shown as "binary" (download only).
- **Removed rows today:**

| Step | Removes rows? | What it writes now |
|---|---|---|
| `parse` | Yes: required value missing/invalid, identical alleles, EAF 0 or 1, other chromosomes | `parse_summary.tsv` (per-column counts), `chromosomes.tsv`. Not the rows |
| `filter` | Yes: rules applied in order (chromosome set, \|BETA\|, SE, INFO missing, INFO, MAF, N, indels, duplicates) | `filter_summary.tsv` (count per rule). Not the rows |
| `harmonise_liftover` | Yes, at four points (below) | `harmonise_liftover_summary.tsv` (count per stage), `gwas2vcf.log`, `bcftools.log`. Not the rows |
| `eaf_check` | No | `eaf_vs_reference.png` |

### What the harmonisation software exposes

| Stage | Tool | Does it say which variants it dropped? |
|---|---|---|
| Read / harmonise / normalise | gwas2vcf (our edited copy, `harmonise_liftover/gwas2vcf/`) | **No, not usably.** It counts them (`VariantsNotRead`, `VariantsNotHarmonised`) and logs each one only at `DEBUG` level (`gwas.py`: "Skipping …", "Could not harmonise {columns}: {reason}", "Could not normalise …"), and `DEBUG` also logs every input row, so turning it on would write a log the size of the upload several times over. Also one drop in `vcf.py` ("Skipping variant with null allele") at `WARNING` |
| Liftover GRCh37 → GRCh38 | bcftools +liftover | **Yes.** `--reject rejected.vcf.gz` is already written (to a temp folder, then thrown away) |
| Lifted to another chromosome, other contigs, non-ACGT alleles, duplicates | our Python in `harmonise_liftover.py` | Yes: these are pandas masks we already compute |

## 1. Dataset QC view: the step log as a contract table

Each step in "Processing steps" is **one clean line** when collapsed: the verdict dot (as now), the step's title,
its verdict tag and a chevron. Nothing else: the code and output buttons, −removed, row count, duration, batch
note and messages all move into the expanded view. **Every step starts collapsed**, whatever its verdict (the
verdict tag and dot already flag warn/fail).

Expanded, it shows a two-column table styled like the pipeline page's contract table (`.ct` in `pipeline.vue`;
move the shared styles to a small component or shared CSS):

```
 step       gwashub/filter   image gwashub/base-r   4.2 s                [view code]
 verdict    [warn]   on fail: flag
 rows       9,812,334 → 9,310,002   (−502,332, 5.12%)
 messages   • Removed 502,332 of 9,812,334 rows (5.12%).
            • |BETA| outside the limits (max 20): 12
            • duplicate variant (same chr:pos:alleles): 502,320
 metrics    rows in              9,812,334
            rows removed           502,332
            removed beta outside…       12
            …  (all of them, a compact key/value grid; no "+N more")
 artifacts  [table] Rows removed by each filter     filter_summary.tsv      1 KB   [open] [download]
            [table] Removed rows (with reason)       removed_rows.tsv.gz   4.1 MB   [open] [download]
 files      step log (step_log.json) · console output · params.json                [open]
```

- **Artifacts row:** one line per declared artifact: icon by type, label, file name, size, row count when the step
  gives one, an **Open** button and a **Download** button. Images also get a small thumbnail.
- **Open** opens the artifact in the existing `StepOutputViewer` dialog (already maximizable; it gets a `focus`
  prop so it opens on that artifact rather than the step log). Images full size, tables as a table, text/JSON as
  text, the HTML report in a frame. The dialog gets an "Open in new tab" button (the same artifact URL, inline) for
  a real pop-out window.
- **Compressed tables** (`.tsv.gz`, needed for removed rows): the preview endpoint returns the first 100 rows,
  decompressed, for `?preview=1`, so they open as tables like any other, under a line "First 100 of 502,332 rows:
  download for all" (the total from the artifact's `rows`); full file via Download. Plain `.tsv` tables in the viewer
  get the same 100-row limit (today it shows up to 400 lines). (Today `.gz` is
  download-only.) Reading only the start of the object on S3 needs a streamed read in `storage.py` (a ranged
  `get_object` body, decompressed until 100 lines); locally a `gzip.open`.
- **Metric labels:** keys humanised as now (`rows_removed` → "rows removed"); numbers formatted; fractions
  (`*_fraction`) shown as percentages. No per-step label tables: the contract stays "any metric names".
- **Error steps** (crashed, no valid log): the same table with verdict `error`, the exit code, the stop reason and
  the console tail.
- `data-tour="panel-steps"` stays on the section; `panel-metrics` is untouched.

The QC report HTML (`qc_report.py`) gets the same layout per step (verdict, messages, metrics table, artifacts
listed by label and file name; images embedded as now). Removed-row files are listed, not embedded.

No change to the step log contract itself is required. One **optional** addition (documented in
`CONTRIBUTING.md` and the README's contract section): an artifact may carry `"rows": <int>` (for tables) and
`"description": "<one sentence>"`; the UI shows them when present.

## 2-4. Removed rows as an artifact (`parse`, `filter`, `harmonise_liftover`)

One convention for every step that removes rows, so the UI, the report and a consortium step all treat them alike:

- File `removed_rows.tsv.gz` (gzipped, tab-separated, header, `NA` for missing), declared as
  `{"type": "table", "path": "removed_rows.tsv.gz", "label": "Removed rows", "rows": <n>}`.
- Columns: the step's input columns as they were, then **`reason`** (one per row; when a row fails several checks,
  all of them, `; `-separated, in the order the step checks them). Steps that see the upload also give
  **`line`** (line number in the uploaded file, 1 = first data row) so a cohort can find the row.
- Written even when nothing is removed? **No**: only when at least one row is removed (no empty artifacts).
- Documented in `CONTRIBUTING.md` as the recommended practice for consortium steps; the runner checks nothing
  about it (it already counts rows before and after).

### parse

- Keep the mapped columns **as received** (text, before recoding) for the removed rows, plus `line` and `reason`.
  Today `raw` is freed straight after building `gwas`; instead keep a row index and the original text columns until
  the end, then write `raw[!keep]`. Memory: the mapped columns as text are already held once; this holds them for
  longer (the step has 32 GB; a 10M-row upload with 13 text columns is well inside that).
- Reasons (from rules the step already applies, in its order):
  `chromosome not 1-22/X/Y (MT)`, `position missing or invalid`, `EA missing or not A/C/G/T`,
  `OA missing or not A/C/G/T`, `identical alleles`, `BETA missing or not a number`, `SE missing or ≤ 0`,
  `P missing and not computable` / `P outside 0-1`, `EAF missing or outside 0-1`, `EAF 0 or 1 (monomorphic)`.
  Implemented by recording, per required column, a logical "invalid" vector at the point each rule runs (instead of
  only counting), then pasting the reasons for dropped rows.
- The step fails early (column mapping, -log10 P detection) without writing rows: unchanged.

### filter

- Rules already run in order on what is left, so each removed row has exactly one reason: the rule's label
  (e.g. `|BETA| outside the limits (max 20)`, `duplicate variant (same chr:pos:alleles)`).
- Collect `gwas[drop][, reason := label]` per rule into a list, `rbindlist` and write once at the end. Columns: the
  GWASHub format (as filter received it) + `reason`.
- `filter_summary.tsv` stays (the counts).

### harmonise_liftover

Removed rows come from four places; one file, with a `stage` column as well as `reason`:

| Stage | Source of the rows | Columns |
|---|---|---|
| gwas2vcf: not read / not harmonised / not normalised / null allele | **Edit our gwas2vcf copy** (see below) to write each dropped input line and its reason | The step's input row (GWASHub format, uploaded build) |
| liftover | `rejected.vcf.gz` from bcftools +liftover (already produced), converted with `bcftools query` | GRCh37 position and harmonised alleles, BETA, SE, P, EAF (VCF fields mapped back to GWASHub names) |
| after liftover: other chromosome, other contig, non-ACGT alleles, duplicate | the existing pandas masks | GRCh38 values + `chr37 pos37` |

Common columns across stages: `stage, reason, chr, pos, ea, oa, beta, se, p, eaf, chr_input, pos_input` (missing
where a stage can't say). `chr_input/pos_input` always give the position as uploaded (gwas2vcf already carries
`C37/P37` through the liftover for GRCh37; we would set them for GRCh38 uploads too), so every removed row points
back to the upload.

**The gwas2vcf edit (recommended):** add an optional `--rejected <path>` argument to our copy. Where it does
`metadata["VariantsNotRead"] += 1; continue` (and the `VariantsNotHarmonised` and null-allele places), it also
writes the input line and a short reason (`not read: <field>`, `REF does not match the reference`,
`could not normalise`, `null allele`). About 15 lines; counting and output unchanged; off unless the flag is given.
This is our edited copy (it already carries "NS added" edits), not upstream.

**Alternative (no gwas2vcf edit): diff.** Every VCF record keeps the input `chr/pos` in `C37/P37` (set for both
builds), so input rows whose `chr:pos:{ea,oa}` has no record are the ones gwas2vcf dropped. Gives the rows but only
one reason ("dropped by gwas2vcf"), and is ambiguous where several input rows share a position and indels were
normalised. Works without touching gwas2vcf.

**Liftover reasons:** bcftools +liftover's reject file does not say why a record was rejected (to check against the
plugin version in `gwashub/vcf`); the reason will be `could not be lifted to GRCh38` unless it does.

## 5. eaf_check: optionally remove large differences

New parameter in `eaf_check/step.yaml`:

```yaml
- {key: remove_large_diff, label: Remove variants with a large difference, type: boolean, default: false,
   help: "Removes variants whose EAF differs from 1000 Genomes by more than the Large difference threshold. Variants
     not found in 1000 Genomes are kept."}
```

- Off (default): as now; nothing is removed, the input file is untouched.
- On: rows with `|ΔEAF| > diff_threshold` are removed from `--input`, written to `removed_rows.tsv.gz` (GWASHub
  format + `ref_eaf`, `eaf_diff`, `reason`), and counted (`rows_removed`). The warn threshold
  (`warn_diff_fraction`) is still judged on the share before removal, so a dataset with many differences still
  warns.
- Variants with no 1000 Genomes match, and runs where the check doesn't run (no ancestry match, no reference, fewer
  than 100 matches), are kept.
- Matching is on `chr, pos, ea, oa`; duplicates by position and allele order are already removed upstream, so the
  removal is by those keys.
- Update the step's `description`/`narrative` ("Removes nothing" → "Removes nothing unless asked") and the
  "Default QC pipeline" table in `docs/PROGRESS.md`.

## Tests

- `parse/test_parse.py`: removed_rows has the right rows, `line` numbers and reasons (incl. a row failing two
  checks); no file when nothing is removed.
- `filter/test_filter.py`: one reason per row, matching the rule counts in `filter_summary.tsv`.
- `harmonise_liftover`: a test (needs the `gwashub/vcf` image; skips without) with a REF mismatch, an unliftable
  GRCh37 position and a duplicate after liftover, each in the file with its stage.
- `eaf_check`: default keeps everything; `remove_large_diff: true` removes exactly the large differences.
- API: `?preview=1` on a `.tsv.gz` artifact returns the first 100 rows; permissions as the other artifact reads.
- `test_tours.py` still passes (markers kept).

## Order of work

1. Removed-rows convention + `filter` (simplest) + tests.
2. `parse` + tests.
3. `eaf_check` parameter + tests.
4. `harmonise_liftover` (liftover rejects and post-liftover masks first; then gwas2vcf, edit or diff, per your answer).
5. API gz preview; `StepOutputViewer` focus/new tab; DatasetPanel contract table; QC report layout.
6. Docs: `CONTRIBUTING.md` (removed rows, optional `rows`/`description`), README contract section, `PROGRESS.md`.

## Decisions

1. **gwas2vcf drops:** edit our gwas2vcf copy (optional `--rejected` flag), not a diff.
2. **Pop-out:** the existing maximizable viewer dialog, opened on the clicked artifact, with an "Open in new tab"
   button.
3. **Collapsed view:** as clean as possible: title, verdict, expand chevron. All steps start collapsed.
4. **parse removed rows:** values as received (text), with upload line number and reason(s); every removed row.
5. **Size:** no cap (follows from 4); removed-row files are gzipped and download-only past the 100-row preview.
6. **eaf_check removal** (not asked; default taken): the verdict is judged on the share before removal, so a
   dataset with many differences still warns when they are removed. Say if it should pass after removal instead.
