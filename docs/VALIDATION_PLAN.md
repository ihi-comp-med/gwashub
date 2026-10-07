# Validation plan

Five tests that show, with numbers, that GWASHub gives the same answers as the tools and pipelines people already
trust. Each test runs the same inputs two ways: through the website, and through the original program or pipeline run
outside GWASHub (GWAMA or METAL by hand, the HERMES 3 HPC pipeline, or a file whose correct QC outcome is known in
advance). Then it compares the outputs
against thresholds fixed in advance. The results become a public **Validation** page on the site (sketch at the end).

## Principles

- **Same inputs, two routes.** Never compare against something derived from the site's own output, except where a test
  says so on purpose (the site's QC'd files are the agreed input to the meta-analysis tests).
- **The reference is the original tool's output, never a reimplementation.** GWASHub's claim is that it reproduces
  GWAMA, METAL and the HERMES 3 pipeline; whether those tools are themselves right is not what these tests measure.
  Running the same release by hand, with inputs prepared independently in R, checks everything GWASHub adds around it:
  input formatting, flags, allele matching across datasets, output parsing and writing.
- **Thresholds set before running**, written in each test below. A test passes or fails; there is no "looks close".
- **Everything recorded:** dataset sources and versions, the R script, the site's pipeline version and image digests
  (shown on each run), GWAMA/METAL versions, and the comparison table. Anyone should be able to rerun it.
- **Re-run check:** run each site analysis twice; the outputs must be byte-identical (compare `sha256sum` of the
  downloaded files). This is the cheapest strong evidence of reproducibility and applies to every test.

## Shared inputs

Three or more public heart-failure GWAS, so every test uses real data. Candidates (check each source's terms, and
record the exact file and version used):

| Short name | Source | Build | Notes |
|---|---|---|---|
| FINNGEN | FinnGen public release, endpoint I9_HEARTFAIL_NS | GRCh38 | EUR; has EAF, N via cases/controls |
| UKB | UK Biobank: Pan-UKB or Neale lab heart failure (ICD-10 I50 / phecode 428) | GRCh37 | EUR; Neale lab also has female and male files (needed for test 2) |
| BBJ | BioBank Japan heart failure (Ishigaki et al. 2020, JENGER) | GRCh37 | EAS; useful for heterogeneity (random effects) |
| HERMES2019 | HERMES 2019 HF meta-analysis (Shah et al. 2020) | GRCh37 | Only for test 5 or as a sanity reference; it is itself a meta-analysis |

**Size:** keep uploads small and the tests quick. A fixed recipe so it is repeatable: autosomes only; variants present
in all files; then **all** variants in 2 Mb windows around a few known HF loci (for example 9p21 CDKN2B-AS1, 4q25
PITX2, 6q25 LPA) so the meta-analysis has real signals, plus a **random 50,000** of the rest (`set.seed(2026)`).
About 60,000 to 80,000 variants per file.

All scripts live in `validation/<nn>_<name>/` (layout in `validation/README.md`; test 1 is set up): `reference.R`, the exact inputs, the reference outputs and a
`result.json` the Validation page reads.

---

## Test 1: GWAMA meta-analysis, default settings

**Claim:** a fixed-effect meta-analysis on GWASHub gives the same results as GWAMA run by hand on the same files.

**Inputs:** FINNGEN, UKB, BBJ, prepared with the size recipe above.

**Steps**
1. `reference.R` downloads and subsets the three files and writes `finngen.tsv`, `ukb.tsv`, `bbj.tsv`.
2. Upload the three files to a validation project on the site; let QC run (all should pass or warn, not fail).
3. Download each dataset's **QC'd file** (dataset panel → *QC'd file*). These are the agreed inputs for the meta step,
   so the test isolates the meta-analysis from QC.
4. Run a GWAMA meta-analysis on the site with the defaults (fixed effects, min 2 datasets, no MAF filter, no genomic
   control). Download `meta_results.tsv.gz`.
5. GWAMA by hand on the three QC'd files: write GWAMA inputs (`MARKERNAME EA NEA BETA SE N EAF`, marker =
   `chr:pos:<alleles sorted>`), run the same GWAMA release (2.2.2) with
   `GWAMA -i gwama.in -o ref -qt --indel_alleles --threshold 1`, keep variants in ≥ 2 datasets.
6. Join site and GWAMA on `chr:pos:ea:oa` (flip sign where the effect allele differs) and compare.

Set up in `validation/01_gwama_default/` (`1_prepare.R`, `2_reference.R`, `3_compare.R`, thresholds in `test.yaml`).

**Pass if**
| Check | Threshold |
|---|---|
| Same variant set | identical |
| β and se | max abs difference ≤ 1e-6 (GWAMA's printed precision) |
| p | max relative difference ≤ 1e-4 (p > 1e-300) |
| n_studies and direction string | identical |
| Q, its p and I² | max abs difference ≤ 1e-6 |
| Total N and EAF | max abs difference ≤ 1e-6 |
| Re-run | second site run has the same sha256 |
| Lead loci | the known loci in the windows are reported as lead loci |

**Show on the page:** scatter of site vs GWAMA-by-hand −log10 p (should sit on the line), the comparison table, the
GWAMA command line used.

**Found by the dry run (fixed):** GWASHub wrote its results with 6 significant digits, so values ≥ 1 (z, Q) lost digits
GWAMA prints, and the gzip header carried a timestamp, so re-runs weren't byte-identical. Results are now written with
15 significant digits and no timestamp; a dry run on a local meta-analysis then matched GWAMA by hand exactly (every
difference 0) and two runs were byte-identical.

---

## Test 2: GWAMA with every option on

**Claim:** each GWAMA setting on the site does what GWAMA does with that option, including in combination.

**Inputs:** test 1's three QC'd files, plus UKB **female** and **male** files (same recipe, uploaded with sex set to
Female and Male) for the sex-differentiated test.

**Site settings:** random effects; genomic control per dataset; genomic control on the results; minimum MAF 0.01;
minimum datasets 3; direction shown below P 0.05; sex-differentiated test (on the female + male + the other datasets).

**Reference:** GWAMA by hand with `--random --genomic_control --genomic_control_output --sex --threshold 0.05`, the
MAF filter applied to each input first (MAF < 0.01 removed, as the site does before GWAMA) and `n_studies ≥ 3` applied
after.

**Pass if:** identical to GWAMA by hand as in test 1 (β, se, p, n_studies, direction, heterogeneity, N, EAF, and the
two sex-differentiated p-values); the genomic control factors in the site's GWAMA output equal those of the run by
hand; the number of variants removed by the MAF filter equals the count in R.

**Why it matters:** the defaults are what most people run, but the options are where wrappers usually go wrong (a flag
not passed, a filter applied after instead of before). One test with everything on shows they are all wired up.

---

## Test 3: METAL, both schemes, and agreement with GWAMA

**Claim:** METAL on the site matches METAL run by hand, in both schemes and with its options; and two different
programs agree with each other where they should.

**Inputs:** test 1's three QC'd files.

**Site runs (three meta-analyses):**
1. METAL, scheme `stderr`, defaults.
2. METAL, scheme `samplesize`, with sample-overlap correction on (Z cut-off 1), minimum weight 10,000, min/max EAF on,
   precision 8.
3. (Already from test 1) GWAMA fixed effects.

**Reference:** METAL by hand (release 2020-05-05) with a script mirroring the site's settings:
`SCHEME STDERR|SAMPLESIZE`, `AVERAGEFREQ ON`, `MINMAXFREQ ON|OFF`, `GENOMICCONTROL OFF`, `OVERLAP ON|OFF`,
`ZCUTOFF 1`, `MINWEIGHT 10000`, `EFFECT_PRINT_PRECISION 8`, `STDERR_PRINT_PRECISION 8`, and the same `MARKER`,
`ALLELE`, `EFFECT`, `STDERR`, `PVALUE`, `WEIGHT`, `FREQ` columns.

**Pass if**
| Check | Threshold |
|---|---|
| Site vs METAL by hand (both runs): β/se or Z, p, direction, weight, freq, min/max freq | identical at printed precision |
| Variant set, including MINWEIGHT removals | identical |
| METAL stderr vs GWAMA fixed (cross-program) | β, se abs difference ≤ 1e-5; −log10 p difference ≤ 0.01 |
| METAL samplesize vs stderr (cross-scheme) | −log10 p correlation ≥ 0.99; same lead loci |

**Why it matters:** the cross-program check is the strongest statement on the page: two independent implementations
of the same model, run through GWASHub, give the same answer.

---

## Test 4: QC filters remove exactly what they should ("planted problems")

**Claim:** each QC rule removes or fixes exactly the variants it describes, and nothing else; allele harmonisation flips
effects correctly.

**Input:** one clean real file (UKB subset, about 60,000 variants), with known problems planted by `reference.R`, each in
a separate, recorded set of rows (for example 100 rows each):

| Planted problem | Site setting | Expected |
|---|---|---|
| EAF missing | Require EAF on | removed |
| EAF exactly 0 or 1 | Remove EAF of 0 or 1 on | removed |
| \|β\| ≥ 20 | Maximum \|BETA\| 20 | removed |
| SE above a cut-off | Maximum SE (set, e.g. 10) | removed |
| INFO below threshold / INFO missing | Minimum INFO 0.8; Remove variants without INFO on | removed |
| MAF below threshold | Minimum MAF 0.01 | removed |
| N missing / N below threshold | Require N on; Minimum N (set) | removed |
| Indels | Remove indels on | removed |
| Exact duplicate rows / duplicate chr:pos:alleles | Remove duplicate variants on | one copy kept |
| Chromosome Y, MT | Chromosomes kept "1-22, X" | removed |
| P outside 0 to 1, non-numeric β | (always) | removed by parse |
| Alleles swapped relative to the reference (EA = reference base) | harmonise | kept, β sign flipped, EAF → 1 − EAF |
| Non-palindromic SNP given on the minus strand | harmonise | kept, alleles complemented |
| A few variants deliberately at wrong positions (alleles can't match the reference) | harmonise | not harmonised, removed |

**Reference:** `reference.R` knows the row ids of every planted set, so it writes the expected output: which rows
survive and, for swapped/flipped rows, the expected β, EAF and alleles after harmonisation (and their GRCh38 positions
from the UCSC chain, via R `liftOver` or the step's own counts).

**Pass if:** every planted row is removed or fixed exactly as expected; every unplanted row survives (apart from the
small, explained number lost in liftover, which must equal the step's own `unmapped` and `chromosome_changed` counts);
the per-step row counts on the site equal the expected counts; the step logs name each rule's removals.

**Why it matters:** QC is where most mistakes in consortium meta-analyses come from. Showing each rule with a known
answer makes the QC report trustworthy.

---

## Test 5: replicates the HERMES 3 HPC pipeline on real cohort files

**Claim:** the GWASHub default QC gives the same cleaned file and QC metrics as the HERMES 3 DCM pipeline on the HPC
(the Snakemake pipeline it was ported from).

**Inputs:** 2 or 3 real HERMES 3 cohort files that already went through the HPC pipeline (their `*-hg38_clean.tsv.gz`
outputs and logs are on Myriad), ideally one GRCh37 and one GRCh38 upload, one EUR and one non-EUR. If cohort data
can't go on the page, use the public HERMES 2019 file through both pipelines instead, and report cohort results
privately.

**Steps:** upload each file with the same column mapping and study details as on the HPC; run the default pipeline;
download the QC'd file and the step logs; compare with the HPC outputs.

**Pass if**
| Check | Threshold |
|---|---|
| Variants out | Jaccard overlap ≥ 0.999; every difference explained (listed by reason) |
| Effect allele and position (GRCh38) for shared variants | identical |
| β, se, EAF, N | max abs difference ≤ 1e-8 (or exact text match) |
| p | max relative difference ≤ 1e-6 |
| rsID | identical where both have one |
| Per-step counts (harmonised, lifted, removed by each filter) | identical to the HPC logs |
| LDSC h² and intercept; λGC | equal to the HPC values within 1e-4 (same ldsc release, same references) |
| EAF check flags | identical list |

**Known causes of difference to check first:** reference file versions (dbSNP build, FASTA contigs, 1000G release:
GWASHub uses the 2022 high-coverage panel for the EAF check, HERMES 3 used phase 3 lifted), and R/Python version
differences in number formatting. Any difference caused by a deliberate change must be listed on the page as such.

**Why it matters:** this is the claim consortium members care about most: "uploading here is the same as sending it to
the HPC pipeline".

---

## The Validation page (sketch)

A page at `/validation`, public to anyone signed in, one card per test:

- **Header:** test name and claim in one sentence; a badge *Passed* / *Failed* / *Not run*; date run; GWASHub commit,
  pipeline version and image digests; program versions (GWAMA 2.2.2, METAL 2020-05-05, ldsc commit).
- **Result table:** each check, its threshold and the observed value, with a tick or cross.
- **One plot:** site vs reference (−log10 p or β), or for test 4 a table of planted problems with expected vs observed.
- **Downloads:** the R script, the inputs (or how to get them), the reference outputs, the site outputs.
- **Re-run check:** the two runs' checksums, identical.

Data for the page: each `validation/<nn>_<name>/result.json` (checks, thresholds, observed, versions, plot data),
committed with the scripts, so the page is static and versioned with the code. Re-running a test updates the JSON in a
pull request, which shows exactly what changed.

## Order for tomorrow

1. Pick and download the public files; write the size recipe once (shared by tests 1 to 4).
2. Test 1 (it also produces the QC'd files tests 2 and 3 use).
3. Test 3, then test 2.
4. Test 4 (needs the most scripting: the planted problems and expected output).
5. Test 5 (needs HPC access and a decision on what can be shown publicly).
