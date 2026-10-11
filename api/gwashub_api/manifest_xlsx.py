"""The bulk upload manifest as an Excel workbook: the template (a README sheet, the Manifest sheet with drop-down lists,
and a hidden sheet holding the lists) and reading a filled-in workbook back into rows.

Manifest columns are fixed keys in the header row (`file`, `cohort`, …, `col_effect`, …, `meta_<field>`); the README
explains each. Rows are read from the Manifest sheet; dates in date cells come back as YYYY-MM-DD."""
import io
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from gwashub_api import metadata_schema

ROWS = 1000  # data rows given drop-downs and formats
EFFECT_TYPES = ["beta", "odds_ratio"]
FILL = {"required": "D6E4F5", "optional": "EDEDED", "cohort": "DDF0DD", "dataset": "FFF2CC"}
GROUP_TEXT = {"required": "Required (sample sizes: n_cases and n_controls, or n_total, by the phenotype's trait type)", "optional": "Optional (leave empty if not applicable)",
              "cohort": "Cohort details: the same on every row of a cohort", "dataset": "This dataset's details"}

# The file columns: key -> (what it is, required?)
FILE_COLUMNS = [
    ("chr", "Chromosome", True), ("pos", "Base-pair position", True), ("ea", "Effect allele", True),
    ("oa", "Other (non-effect) allele", True), ("effect", "Effect size: BETA, or odds ratio (see effect_type)", True),
    ("se", "Standard error of the effect", True), ("eaf", "Effect allele frequency", True),
    ("p", "P value (give col_p or col_log10p)", False), ("log10p", "-log10 P (give col_p or col_log10p)", False),
    ("n", "Sample size per variant", False), ("ncase", "Cases per variant", False), ("ncontrol", "Controls per variant", False),
    ("rsid", "rsID", False), ("info", "Imputation INFO score", False),
]


def columns(project, custom, cfg, cohorts) -> list[dict]:
    """The Manifest sheet's columns: {key, group, help, example, choices (list name or None), kind}."""
    cols = [
        {"key": "file", "group": "required", "kind": "text", "example": "results/ukb_hf_eur.tsv.gz",
         "help": "The summary-statistics file: an absolute path (/data/gwas/ukb_hf.tsv.gz), or a path relative to the folder "
                 "this workbook is in (ukb_hf.tsv.gz, results/ukb_hf.tsv.gz). Plain or gzipped; tab-, comma- or space-separated, "
                 "with a header line. One file per row."},
        {"key": "cohort", "group": "required", "kind": "text", "choices": "cohorts", "example": cohorts[0] if cohorts else "UK Biobank",
         "help": "The cohort's name. Pick one already on the site, or type a new name to create that cohort (with the cohort "
                 "details on its rows)."},
        {"key": "phenotype", "group": "required", "kind": "text", "choices": "phenotypes", "example": project.phenotypes[0] if project.phenotypes else "",
         "help": "The phenotype code (see Phenotypes below; its trait type decides which sample-size columns to fill)."},
        {"key": "ancestry", "group": "required", "kind": "text", "choices": "ancestries", "example": project.ancestries[0] if project.ancestries else "",
         "help": "The ancestry group of the samples."},
        {"key": "sex", "group": "required", "kind": "text", "choices": "sexes", "example": project.sexes[0] if project.sexes else "",
         "help": "The sex stratum."},
        {"key": "build", "group": "required", "kind": "text", "choices": "builds", "example": "GRCh37",
         "help": "The genome build of the file's positions: GRCh37 or GRCh38."},
        {"key": "effect_type", "group": "required", "kind": "text", "choices": "effect_types", "example": "beta",
         "help": "What col_effect holds: beta (log odds ratio for case/control traits, or the effect of a quantitative trait) "
                 "or odds_ratio. Quantitative phenotypes need beta."},
        {"key": "n_cases", "group": "required", "kind": "int", "example": "12000",
         "help": "Case/control phenotypes: the number of cases. Leave empty for quantitative phenotypes."},
        {"key": "n_controls", "group": "required", "kind": "int", "example": "380000",
         "help": "Case/control phenotypes: the number of controls. Leave empty for quantitative phenotypes."},
        {"key": "n_total", "group": "required", "kind": "int", "example": "",
         "help": "Quantitative phenotypes: the total sample size. Case/control phenotypes: optional; if given it must equal "
                 "n_cases + n_controls."},
    ]
    for key, what, req in FILE_COLUMNS:
        cols.append({"key": f"col_{key}", "group": "required" if req else "optional", "kind": "text",
                     "example": {"chr": "CHR", "pos": "BP", "ea": "A1", "oa": "A2", "effect": "BETA", "se": "SE", "eaf": "FRQ", "p": "P"}.get(key, ""),
                     "help": f"{what}: the exact header of that column in the file (case-sensitive)."
                             + ("" if req else " Leave empty if the file has no such column.")})
    shown, required = set(cfg["shown"]), set(cfg["required"])
    for sec in metadata_schema.sections(custom):
        group = "cohort" if sec["level"] == "cohort" else "dataset"
        if "grid" in sec:
            if sec["key"] not in shown:
                continue
            for r in sec["grid"]["rows"]:
                for c in sec["grid"]["cols"]:
                    cols.append({"key": f"meta_{r['key']}_{c['key']}", "group": group, "kind": r["type"], "example": "",
                                 "help": f"{sec['title']}: {r['label']}, {c['label'].lower()}."
                                         + (" At least one cell of this table is required." if sec["key"] in required else "")})
            continue
        for f in sec.get("fields", []):
            if f["key"] not in shown:
                continue
            cols.append({"key": f"meta_{f['key']}", "group": group, "kind": f["type"], "example": "",
                         "choices": f"opt_{f['key']}" if f.get("options") else None, "options": f.get("options"),
                         "help": f"{f['label']}{' (required)' if f['key'] in required else ''}."
                                 + (" A date (YYYY-MM-DD)." if f["type"] == "date" else "")})
    cols.append({"key": "sha256", "group": "optional", "kind": "text", "example": "",
                 "help": "Optional: the file's sha256 checksum; the script checks the file against it before uploading."})
    return cols


def template(project, custom, cfg, cohorts: list[str]) -> bytes:
    cols = columns(project, custom, cfg, cohorts)
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    sheet = wb.create_sheet("Manifest")
    lists = wb.create_sheet("Lists")
    lists.sheet_state = "hidden"

    # Lists for the drop-downs (kept on their own sheet: Excel limits inline lists to 255 characters).
    named = {"cohorts": cohorts, "phenotypes": list(project.phenotypes), "ancestries": list(project.ancestries),
             "sexes": list(project.sexes), "builds": ["GRCh37", "GRCh38"], "effect_types": EFFECT_TYPES}
    for c in cols:
        if c.get("options"):
            named[c["choices"]] = list(c["options"])
    refs = {}
    for i, (name, values) in enumerate(named.items(), 1):
        letter = get_column_letter(i)
        lists.cell(1, i, name)
        for j, v in enumerate(values, 2):
            lists.cell(j, i, v)
        refs[name] = f"Lists!${letter}$2:${letter}${max(len(values) + 1, 2)}"

    # The Manifest sheet: the header row, help on hover, drop-downs, formats.
    for i, c in enumerate(cols, 1):
        cell = sheet.cell(1, i, c["key"])
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=FILL[c["group"]])
        cell.comment = Comment(c["help"], "GWASHub", width=320, height=140)
        letter = get_column_letter(i)
        sheet.column_dimensions[letter].width = max(14, min(40, len(c["key"]) + 4)) if c["key"] != "file" else 48
        data = f"{letter}2:{letter}{ROWS + 1}"
        if c.get("choices") and named.get(c["choices"]) is not None:
            free = c["key"] == "cohort"  # new cohorts may be typed in
            dv = DataValidation(type="list", formula1=f"={refs[c['choices']]}", allow_blank=True, showErrorMessage=not free,
                                errorTitle="Not in the list", error="Pick a value from the list.")
            sheet.add_data_validation(dv)
            dv.add(data)
        fmt = {"date": "yyyy-mm-dd", "int": "0", "integer": "0"}.get(c["kind"], "@" if c["kind"] in ("text", "textarea", "email") and not c.get("choices") else None)
        if fmt:
            for row in sheet.iter_rows(min_row=2, max_row=ROWS + 1, min_col=i, max_col=i):
                row[0].number_format = fmt
    sheet.freeze_panes = "B2"

    # README.
    w = readme
    w.column_dimensions["A"].width = 26
    w.column_dimensions["B"].width = 16
    w.column_dimensions["C"].width = 100
    w.column_dimensions["D"].width = 28
    r = 1

    def line(text="", bold=False, size=None):
        nonlocal r
        cell = w.cell(r, 1, text)
        cell.font = Font(bold=bold, size=size)
        r += 1

    line(f"GWASHub bulk upload: {project.name} ({project.id})", bold=True, size=14)
    line()
    for t in ["How to use this workbook",
              "1. Fill in the Manifest sheet: one row per summary-statistics file. Hover over a column's name for help; "
              "the table below explains every column.",
              "2. Save it as .xlsx (keep the sheet names and the header row as they are).",
              "3. Check it: python3 gwashub_upload.py check manifest.xlsx  (lists every problem, by row; uploads nothing).",
              "4. Upload: python3 gwashub_upload.py upload manifest.xlsx  (run it again to resume after an interruption).",
              "5. The datasets wait on the site as Pending QC: check them there, then click Start QC.",
              "",
              "Rules: nothing is guessed, so name every file column exactly as it appears in the file. A row that matches a "
              "dataset already in the project (or another row) is refused: delete the old one on the site first. A manifest "
              "never changes a cohort that is already on the site: leave its cohort details empty to use its record."]:
        line(t, bold=t == "How to use this workbook")
    line()
    line("Column colours", bold=True)
    for g in ("required", "optional", "cohort", "dataset"):
        cell = w.cell(r, 1, g.capitalize() if g in ("required", "optional") else f"meta_* ({g})")
        cell.fill = PatternFill("solid", fgColor=FILL[g])
        w.cell(r, 3, GROUP_TEXT[g])
        r += 1
    line()
    line("Phenotypes", bold=True)
    for h, col in (("Code", 1), ("Trait type", 2), ("Description", 3)):
        w.cell(r, col, h).font = Font(bold=True)
    r += 1
    descriptions = project.phenotype_descriptions or {}
    for ph in project.phenotypes:
        quant = (project.phenotype_types or {}).get(ph) == "quantitative"
        w.cell(r, 1, ph)
        w.cell(r, 2, "quantitative" if quant else "case/control")
        w.cell(r, 3, "; ".join(x for x in (descriptions.get(ph), "fill n_total" if quant else "fill n_cases and n_controls") if x))
        r += 1
    line()
    line("Allowed values", bold=True)
    for label, values in (("ancestry", project.ancestries), ("sex", project.sexes), ("build", ["GRCh37", "GRCh38"]),
                          ("effect_type", EFFECT_TYPES), ("Cohorts on the site", cohorts or ["(none yet)"])):
        w.cell(r, 1, label)
        w.cell(r, 3, ", ".join(values))
        r += 1
    line()
    line("Columns", bold=True)
    for h, col in (("Column", 1), ("Group", 2), ("What to put", 3), ("Example", 4)):
        w.cell(r, col, h).font = Font(bold=True)
    r += 1
    for c in cols:
        w.cell(r, 1, c["key"]).fill = PatternFill("solid", fgColor=FILL[c["group"]])
        w.cell(r, 2, c["group"])
        w.cell(r, 3, c["help"] + (f" One of: {', '.join(c['options'])}." if c.get("options") else "")).alignment = Alignment(wrap_text=True, vertical="top")
        w.cell(r, 4, c["example"])
        r += 1
    wb.active = 1  # open on the Manifest sheet
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class ManifestError(ValueError):
    pass


def read(content: bytes) -> tuple[list[str], list[dict]]:
    """(columns, rows) from a filled-in workbook's Manifest sheet: rows are {row: Excel row number, values: {key: text}};
    empty rows are skipped. Numbers come back as text (whole numbers without a decimal point), date cells as YYYY-MM-DD."""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise ManifestError("This isn't a readable .xlsx workbook: save the manifest from Excel as .xlsx")
    if "Manifest" not in wb.sheetnames:
        raise ManifestError("The workbook has no sheet called Manifest: start from the template")
    rows_iter = wb["Manifest"].iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        raise ManifestError("The Manifest sheet has no header row")
    cols = [str(h).strip() if h is not None else "" for h in header]
    while cols and not cols[-1]:
        cols.pop()
    rows = []
    for n, raw in enumerate(rows_iter, 2):
        values = {c: text(raw[i] if i < len(raw) else None) for i, c in enumerate(cols) if c}
        if any(values.values()):
            rows.append({"row": n, "values": values})
    return cols, rows


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()
