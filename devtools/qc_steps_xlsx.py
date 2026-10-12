"""Write docs/QC_STEPS.xlsx: the default QC steps, their parameters (from each step.yaml) and notes.

    .venv/bin/python devtools/qc_steps_xlsx.py

Descriptions and parameters come from the step.yaml files; the notes (NOTES below) summarise the documentation at the
top of each step's script, so update them when a step's behaviour changes.
"""
import os

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "QC_STEPS.xlsx")

NOTES = {
    "parse": [
        "The only step that reads the upload as received; every later step reads and writes the GWASHub format (chr pos ea oa beta se p eaf [n ncase ncontrol rsid info]).",
        "Recoding as the HERMES 3 DCM pipeline: no 'chr' prefix, 23 = X, 24 = Y, 25/XY read as X; alleles upper-case A/C/G/T; counts such as '1,234' read as numbers, censored counts such as '<=40' (as All of Us hides small counts) read as missing and filled from the study details; odds ratios converted to log-odds.",
        "For odds-ratio uploads the SE is assumed to be the SE of log(OR). A delta-method SE of the OR itself is not caught here (stats_summary catches it).",
        "Missing P is computed from BETA/SE with the normal approximation; an uploaded P always wins (it may come from a t test, SPA or Firth correction).",
        "P = 0 becomes the smallest positive double (about 2.2e-308), so -log10 P caps near 307 for those rows.",
        "Stops on: truncated gzip, odds ratios uploaded as BETA (all positive, median near 1), log-odds uploaded as OR (negative values), a P column holding -log10 P.",
        "Removes rows with a required value missing or invalid, identical alleles, EAF of exactly 0 or 1 (monomorphic) or repeated header lines; every removed row is listed with its line and reason.",
        "Warns on rounded BETA/SE/P (few distinct values in 50,000+ rows), N more than 5% above the declared total, or a missing autosome in a genome-wide file.",
    ],
    "filter": [
        "Default: only duplicates are removed; every value filter is off until switched on (|BETA| has the HERMES 3 limit of 20 ready).",
        "Each rule runs on what the earlier ones left, so a row is counted once, under the first rule that removes it.",
        "MAF = min(EAF, 1 - EAF) of the dataset's own EAF. MAC = 2 x MAF x N with the per-variant N (cases and controls together); in very unbalanced case/control designs a stricter limit may be wanted.",
        "Strand-ambiguous (A/T, C/G) SNPs can't be checked for strand from their alleles, and near MAF 0.5 not from their frequency either: removing them above MAF 0.4 is the usual safeguard (off by default).",
        "Duplicates: same chr:pos and the same pair of alleles in either order; the first row in the file is kept.",
        "Missing values pass the range rules; only INFO has its own rule for missing values (fill with 1 by default; HERMES 3 removed them).",
        "Fails if a filter is on for a column the dataset doesn't have, rather than removing everything.",
    ],
    "harmonise_liftover": [
        "gwas2vcf (MRCIEU, the HERMES 3 edited copy) keeps a variant if OA is the reference base; if EA is, it swaps the alleles and flips BETA and EAF; otherwise it drops the variant.",
        "The reverse strand is never tried: a SNP reported on the minus strand is dropped, and an A/T or C/G SNP on the minus strand passes with its effect on the wrong allele (see filter and eaf_check).",
        "GRCh37 uploads are lifted to GRCh38 with bcftools +liftover and the UCSC chain; it handles chain strand flips and REF/ALT swaps itself. Variants moved to another chromosome, off the primary contigs, made multi-allelic or duplicated by the liftover are removed.",
        "Output is GRCh38 with ea = ALT and oa = REF, so every dataset is aligned to the same strand and effect allele before meta-analysis.",
        "rsIDs come from dbSNP 157 where it has the same position and alleles; they are labels only (variants are matched by position and alleles).",
        "Build check: up to 20,000 autosomal SNPs are looked up in both reference genomes. In the right build nearly all match, in the wrong one about half; the step warns and names the other build when it fits clearly better.",
        "N, cases and controls are carried through the VCF unchanged (controls in a GWASHub-added field).",
    ],
    "eaf_check": [
        "Compares EAF with 1000 Genomes (2022 high-coverage, GRCh38) for the declared ancestry, matching on position and both alleles.",
        "1000 Genomes has about 500 to 900 people per super-population: reference frequencies carry noise of about 0.015 at f = 0.5. Sub-populations (e.g. Finns) and admixed cohorts differ more without anything being wrong.",
        "Negative correlation with the reference suggests swapped EA/OA columns.",
        "Names three mistakes: wrong ancestry (another population fits at least 40% better), a MAF column uploaded as EAF (agrees only once both are folded to MAF), and strand problems in A/T and C/G SNPs (they disagree with the reference far more often than the other SNPs).",
        "Never fails; removes nothing unless 'Remove variants with a large difference' is on. Doesn't run for ancestries without a 1000 Genomes population.",
    ],
    "ldsc": [
        "The authors' LDSC software, unchanged (Python 2.7): munge_sumstats.py against the HapMap 3 SNP list, then ldsc.py --h2 with Pan-UK Biobank LD scores for the dataset's ancestry (SAS uses CSA).",
        "Intercept is about 1 + N a (a = confounding: stratification, cryptic relatedness); near 1 means the inflation is polygenic signal. With mean chi-square near 1 (small cohorts) it is poorly estimated: read intercept_se.",
        "An intercept well below 1 usually means the cohort applied genomic control already.",
        "h2 is on the observed scale; for case/control traits it depends on the case fraction and is not comparable between cohorts (no liability-scale conversion).",
        "munge_sumstats.py also removes SNPs with N below the 90th percentile / 1.5, so n_snps can be well below the HapMap 3 count when N varies.",
        "Never fails; passes and says why when it can't run (no reference, no rsIDs, no N).",
    ],
    "stats_summary": [
        "Lambda GC = median chi-square / 0.4549. It grows with N under polygenicity, so lambda 1000 (rescaled to 1,000 cases and 1,000 controls, or 1,000 people) is reported for comparison; the LDSC intercept is the better confounding measure in large cohorts.",
        "P vs BETA/SE: warns when more than 1% of rows are over 10-fold off, or the median |BETA/SE| / |Z from P| is outside 0.8 to 1.25 (e.g. SE of the OR rather than of log OR).",
        "SE vs sample size (EasyQC SE-N check): expected SE = sqrt(2 / (p q Neff)) for log-odds, Neff = 4 / (1/cases + 1/controls), over common, well-imputed variants (MAF >= 5%, INFO >= 0.9). A ratio near phi(1 - phi) means effects on the linear 0/1 scale (BOLT-LMM, linear regression).",
        "Assumes additive coding and Hardy-Weinberg proportions; covariates push the observed SE up a little, so ratios of 1.0 to 1.2 are normal.",
        "For quantitative traits the same ratio is the trait SD in the units of BETA (about 1 for an inverse-normal transformed trait): reported, not judged.",
        "Lead loci here are by distance only (500 kb windows by default); meta-analysis loci use LD clumping.",
    ],
    "plots": [
        "Manhattan (autosomes and X; Y not drawn) and QQ plots with lambda GC, plus downsampled data for the website's interactive plots (every P < 1e-3 and a sample of the rest).",
        "Shares its drawing code with the meta-analysis, so QC and meta-analysis plots look the same. Always passes.",
    ],
    "qc_report": [
        "One self-contained HTML file: every step's verdict, messages, metrics and artifacts, rows in and out of each step, and each numeric column before and after QC.",
        "Always runs, even after a blocking failure. A step that crashed (no valid log) is in the run's result but not in the report.",
    ],
}


def fmt_default(p):
    d = p.get("default")
    if p.get("type") == "range":
        if not d.get("on"):
            parts = []
            if d.get("min_on"):
                parts.append(f"min {d.get('min')}")
            if d.get("max_on"):
                parts.append(f"max {d.get('max')}")
            return "off" + (f" (when on: {', '.join(parts)})" if parts else "")
        parts = []
        if d.get("min_on"):
            parts.append(f"min {d.get('min')}")
        if d.get("max_on"):
            parts.append(f"max {d.get('max')}")
        return "on, " + ", ".join(parts)
    if isinstance(d, bool):
        return "yes" if d else "no"
    return str(d)


def params_text(params):
    if not params:
        return "None"
    return "\n".join(f"{p['label']} ({p['key']}): {fmt_default(p)}" for p in params)


pipeline = yaml.safe_load(open(f"{ROOT}/pipeline/qc_steps/templates/default.yaml"))
wb = Workbook()
ws = wb.active
ws.title = "QC steps"
head = ["#", "Name", "Description (brief)", "Parameters (defaults)", "Image", "On fail (default)", "Notes on key steps and assumptions"]
ws.append(head)
for i, entry in enumerate(pipeline["steps"], 1):
    owner, name = entry["step"].split("/")
    y = yaml.safe_load(open(f"{ROOT}/pipeline/qc_steps/{owner}/{name}/step.yaml"))
    notes = "\n".join(f"- {n}" for n in NOTES[name])
    ws.append([i, f"{y['title']}\n({entry['step']})", y["description"], params_text(y.get("params")), y["image"],
               y.get("default_on_fail", ""), notes])

widths = [4, 22, 45, 55, 16, 12, 110]
for c, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(c)].width = w
fill = PatternFill("solid", fgColor="303880")
for cell in ws[1]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = fill
    cell.alignment = Alignment(vertical="center", wrap_text=True)
for row in ws.iter_rows(min_row=2):
    for cell in row:
        cell.alignment = Alignment(vertical="top", wrap_text=True)
ws.freeze_panes = "B2"

info = wb.create_sheet("About")
for line in [
    ["GWASHub default QC pipeline (pipeline/qc_steps/templates/default.yaml), in run order."],
    ["Source of descriptions and parameters: each step's step.yaml (pipeline/qc_steps/gwashub/<step>/step.yaml)."],
    ["Notes summarise the documentation at the top of each step's script."],
    ["On fail: block = stop the pipeline (the report still runs); flag = record the failure and continue; continue = ignore it for the overall verdict."],
    ["Regenerate after changing a step.yaml or a step's behaviour: .venv/bin/python devtools/qc_steps_xlsx.py"],
]:
    info.append(line)
info.column_dimensions["A"].width = 130
wb.save(OUT)
print("wrote", os.path.relpath(OUT, ROOT))
