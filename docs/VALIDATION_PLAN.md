# Validation plan

Four tests that show, with numbers, that GWASHub gives the same answers as the tools people already trust. Each test
runs the same inputs two ways: through the website, and through the original program run outside GWASHub (GWAMA or
METAL by hand), or against a file whose correct QC outcome is known in advance. Then it compares the outputs
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

**Inputs:** test 1's three QC'd files, plus UK Biobank **female** and **male** heart failure files (Neale lab round 2,
ICD-10 I50; test 1's UKB variants; uploaded with sex Female and Male) for the sex-differentiated test.

**Site settings:** two runs, since the site (like GWAMA's --sex) needs every dataset in a sex-differentiated run to be
male-only or female-only. *options*: random effects; genomic control per dataset and on the results; minimum MAF 0.01;
minimum datasets 3; direction shown below P 0.05, on test 1's three datasets. *sex*: the sex-differentiated test on the
female and male files.

**Reference:** GWAMA by hand with `--random --genomic_control --genomic_control_output --threshold 0.05` (options) and
`--sex` (sex), the
MAF filter applied to each input first (MAF < 0.01 removed, as the site does before GWAMA) and `n_studies ≥ 3` applied
after.

**Pass if:** identical to GWAMA by hand as in test 1 (β, se, p, n_studies, direction, heterogeneity, N, EAF, and the
two sex-differentiated p-values); the genomic control factors in the site's GWAMA output equal those of the run by
hand; the number of variants removed by the MAF filter equals the count in R.

Set up in `validation/02_gwama_options/`. The Neale lab files are linear regressions on the 0/1 trait, so QC warns that
their effects are on the linear scale (a real-world case of that check); it doesn't affect the comparison.

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

Set up in `validation/03_metal/`, with a third run, *samplesize_plain* (sample-size scheme, defaults), for the
cross-scheme check: the overlap correction changes the weights, so the run with it on isn't comparable to stderr.

**Found by the dry run (run locally with the site's code and images):** site METAL matched METAL by hand exactly in all
three runs once one bug was fixed: a BETA of exactly 0 negated by an allele switch was written as −0.0, which METAL's
sample-size scheme read as a negative effect (135 HERMES variants), shifting its direction string and overlap
weights; harmonise_liftover and the meta-analysis loader now write it as 0. GWAMA's own p-values drift from the exact
normal tail as Z grows (0.046 in −log10 p at p ≈ 1e-50, METAL within 0.0002), so the cross-program p check covers
p > 1e-5 and the drift is a note. Three lead loci at p ≈ 4e-8 were significant in one scheme only, so the lead-locus
check covers loci with p < 5e-9 and lists the rest. With the overlap correction on, METAL bins variants by total weight,
and with per-variant N most bins hold one variant: the correction is erratic on such data (a caution for users).

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
| Non-palindromic SNP given on the minus strand | harmonise | removed: gwas2vcf (as HERMES 3) switches REF/ALT but doesn't complement (found by the dry run) |
| A few variants deliberately at wrong positions (alleles can't match the reference) | harmonise | not harmonised, removed |
| A header line repeated mid-file | (always) | removed by parse, reason "repeated header line" |
| Chromosome coded 25 / XY | (always) | kept as X |
| A/T and C/G SNPs with MAF > 0.4 | Strand-ambiguous SNPs on, max 0.4 | removed |

Whole-file gotchas can't be planted in rows, so each is its own small upload of the same clean file, changed once,
with the expected verdict and message (these check the named diagnosis, not row counts). The full list, with an
unchanged control that must name no gotcha, is `validation/04_qc_planted/test.yaml` `cases`:

| Whole-file problem | Expected |
|---|---|
| gzip cut in half | parse fails: "cut short" |
| BETA replaced by exp(BETA) (odds ratios given as BETA) | parse fails: "look like odds ratios" |
| Uploaded as GRCh38 when it is GRCh37 | harmonise warns, naming GRCh37 |
| EAF replaced by MAF, alleles unchanged | eaf_check names "minor allele frequency" |
| A/T, C/G SNPs strand-flipped (alleles complemented, EAF → 1 − EAF) | eaf_check warns: strand-ambiguous SNPs |
| BETA and SE multiplied by φ(1 − φ) (linear 0/1 scale) | stats_summary warns: "linear 0/1 scale" |
| SE rounded to 3 decimal places | parse warns: rounded |
| Chromosome 22 deleted | parse warns: chromosome 22 |

Set up in `validation/04_qc_planted/` (part A: the planted rows below; part B: the whole-file mistakes after them).

**Reference:** `reference.R` knows the row ids of every planted set, so it writes the expected output: which rows
survive and, for swapped/flipped rows, the expected β, EAF and alleles after harmonisation (and their GRCh38 positions
from the UCSC chain, via R `liftOver` or the step's own counts).

**Pass if:** every planted row is removed or fixed exactly as expected; every unplanted row survives (apart from the
small, explained number lost in liftover, which must equal the step's own `unmapped` and `chromosome_changed` counts);
the per-step row counts on the site equal the expected counts; the step logs name each rule's removals.

**Found by the dry run (run locally with the real references, 41 of 41 checks passing):** minus-strand SNPs are
removed, not complemented: gwas2vcf (as HERMES 3) switches REF and ALT but never complements, so the expectation was
corrected. dbSNP 157 names 3 kept variants differently from Pan-UKB (matched on β and se, and reported). A/T and C/G SNPs
in regions inverted between builds are complemented by the liftover, which from alleles alone looks like a switch:
EAF decides their orientation in the comparison.

**Why it matters:** QC is where most mistakes in consortium meta-analyses come from. Showing each rule with a known
answer makes the QC report trustworthy.

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

## Status

Tests 1 to 4 run on gwashub-dev and passed (7/7, 18/18, 19/19, 41/41), **with GWAMA run on the whole genome in one go.
The GWAMA wrapper has changed since (October 2026, below), so tests 1 and 2 and test 3's GWAMA comparison must be run
again before their results stand for the current version.** In test 4 the upload wizard itself suggests
"Odds ratios" for the odds-ratios-as-BETA file; it is uploaded as BETA on purpose, so parse's check (which guards
uploads by script) is what's tested.

### Revalidation needed: GWAMA per chromosome (October 2026)

**What changed:** a 15-dataset meta-analysis ran out of memory (60 GB) because GWAMA holds every variant of every
dataset at once. The GWAMA wrapper (`pipeline/analyses/meta_analysis/gwashub/gwama/run_gwama.py`) now runs GWAMA one
chromosome at a time and joins the results. Two consequences need checking against GWAMA by hand:

1. **Per chromosome.** Each variant is meta-analysed on its own, so splitting should change nothing. Every dataset is
   listed on every chromosome in the same order (an empty file where it has none), so the direction string keeps its
   meaning.
2. **Genomic control per dataset** can't be left to GWAMA any more (it would compute λ per chromosome). The wrapper
   applies it before GWAMA, as GWAMA 2.2.2 does: λ = median((β/se)²) / 0.4549364 over the dataset's variants, raised
   to 1 if below, every se multiplied by √λ; it writes `genomic_control.tsv`. **Genomic control on the results** is
   still GWAMA's own, so with that option GWAMA runs once on the whole genome (and needs the memory for it).

**Checked so far (synthetic data only):** `pipeline/tests/test_meta_analysis.py`
`test_gwama_per_chromosome_matches_one_whole_genome_run` gives byte-identical results files per chromosome and in one
run (fixed; random with a direction threshold; datasets missing a whole chromosome), the same per-dataset counts, and
with genomic control the wrapper's λ equals GWAMA's own (`gwama.gc.out`) and β, se, z, p agree to GWAMA's printed
precision. That is not a validation: it is 4 small simulated datasets.

**To do, on gwashub-dev with the new version:**

| Run | Covers | Pass if |
|---|---|---|
| Test 1 again | per chromosome, defaults | as test 1 |
| Test 2 again, both runs | the options run still uses genomic control on the results, so GWAMA runs whole genome there, with the wrapper's genomic control per dataset; the sex run is per chromosome | as test 2; the λ in `genomic_control.tsv` equal those of GWAMA by hand (`gwama.gc.out`) to 4 decimals |
| Test 2, *options* without genomic control on the results (new) | per chromosome with the wrapper's genomic control, random effects, threshold, MAF filter | identical to GWAMA by hand with `--random --genomic_control --threshold 0.05` |
| Test 3's GWAMA comparison again | per chromosome vs METAL | as test 3 |
| Scale: the 15 HERMES 3 DCM datasets (meta-analysis 17) | memory and time at full size | completes on the 60 GB machine; record peak memory and time per chromosome |

