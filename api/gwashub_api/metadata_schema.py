"""The catalogue of study metadata fields (HERMES and SGC data-registry forms).

Every field any project might use is defined here. Each section is either **cohort**-level (the same for
all of a cohort's datasets: study, genotyping, imputation, QC) or **dataset**-level (participants,
association testing, age/sex distribution). Per project, which fields are shown and which are required at
upload are stored in the database (Project.metadata_config); fields marked `default` are shown for a new
project. One definition drives the upload form, the dataset and cohort viewers, and validation.

A dataset's metadata is its cohort's values with the dataset's own values on top: a dataset may override
a cohort-level field (e.g. a different imputation panel for one phenotype).

Projects can add their own fields (Project.metadata_custom: [{key, label, level, type, options, hidden}]); they form
two extra sections, per cohort and per dataset, and every function here takes the project's list as `custom`. A
custom field's key never changes (renaming changes the label); a deleted field is hidden: it leaves the forms but its
values are kept and still validate.
"""
import re
from datetime import date

from fastapi import HTTPException

AGE_GROUPS = [("total", "Total cohort"), ("controls", "Controls"), ("cases_diagnosis", "Cases at diagnosis"),
              ("cases_recruitment", "Cases at recruitment")]
YES_NO = ["Yes", "No"]
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

SECTIONS = [
    {"key": "study", "title": "Study", "level": "cohort", "fields": [
        {"key": "data_collection_start", "label": "Data collection start", "type": "date", "default": True},
        {"key": "data_collection_end", "label": "Data collection end", "type": "date", "default": True},
        {"key": "data_freeze_date", "label": "Data freeze date", "type": "date"},
        {"key": "country", "label": "Country/countries of ascertainment", "type": "text"},
        {"key": "contact_name", "label": "Contact name", "type": "text", "placeholder": "e.g. Jane Smith", "default": True},
        {"key": "contact_email", "label": "Contact email", "type": "email", "placeholder": "name@institution.org", "default": True},
        {"key": "lead_collaborator", "label": "Lead collaborator", "type": "text"},
        {"key": "other_authors", "label": "Other contributors to include as authors", "type": "textarea"},
        {"key": "publication_requirements", "label": "Other publication requirements", "type": "textarea",
         "placeholder": "e.g. whether approval is needed by the study steering committee"},
        {"key": "summary_stats_public", "label": "Summary statistics publicly available?", "type": "select",
         "options": ["Yes", "No", "On publication"]},
        {"key": "references", "label": "References", "type": "textarea", "placeholder": "PMIDs or DOIs describing the cohort", "default": True},
        {"key": "acknowledgements", "label": "Acknowledgements and funding", "type": "textarea", "default": True},
        {"key": "ethics_approval", "label": "Ethics approval", "type": "textarea"},
        {"key": "study_ascertainment", "label": "Study ascertainment", "type": "textarea"},
        {"key": "ancestry_method", "label": "How ancestry was determined and outliers removed", "type": "textarea"},
    ]},
    {"key": "genotyping", "title": "Genotyping", "level": "cohort", "fields": [
        {"key": "genotyping_technology", "label": "Genotyping technology", "type": "select",
         "options": ["Imputed array", "Whole-genome sequencing", "Whole-exome sequencing", "Other"]},
        {"key": "genotyping_array", "label": "Genotyping array", "type": "text", "placeholder": "e.g. UK Biobank Axiom", "default": True},
        {"key": "calling_algorithm", "label": "Calling algorithm", "type": "text", "placeholder": "e.g. Affymetrix Power Tools", "default": True},
    ]},
    {"key": "imputation", "title": "Imputation", "level": "cohort", "fields": [
        {"key": "imputation_software", "label": "Imputation software", "type": "text", "placeholder": "e.g. IMPUTE4, Minimac4", "default": True},
        {"key": "imputation_reference", "label": "Imputation reference panel", "type": "text", "placeholder": "e.g. HRC r1.1, TOPMed r2", "default": True},
        {"key": "n_variants_for_imputation", "label": "Variants used for imputation", "type": "integer", "min": 0, "default": True},
        {"key": "imputation_quality_measure", "label": "Imputation quality measure", "type": "text", "placeholder": "e.g. INFO, Rsq", "default": True},
        {"key": "imputation_quality_threshold", "label": "Post-imputation quality threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 0.3"},
    ]},
    {"key": "genotyping_qc", "title": "Genotyping QC", "level": "cohort", "fields": [
        {"key": "related_removed", "label": "Related individuals removed", "type": "select", "options": YES_NO, "default": True},
        {"key": "variant_call_rate", "label": "Variant call rate threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 0.98", "default": True},
        {"key": "sample_call_rate", "label": "Sample call rate threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 0.97", "default": True},
        {"key": "hwe_p", "label": "HWE P threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 1e-6", "default": True},
        {"key": "maf", "label": "MAF threshold", "type": "number", "min": 0, "max": 0.5, "placeholder": "e.g. 0.01", "default": True},
        {"key": "other_filters", "label": "Other filters", "type": "textarea", "default": True},
    ]},
    {"key": "participants", "title": "Participants", "level": "dataset", "fields": [
        {"key": "case_definition", "label": "Case definition", "type": "textarea", "placeholder": "e.g. ICD-10 I50.x in any position, or adjudicated", "default": True},
        {"key": "case_coding_system", "label": "Coding system used to define cases", "type": "text", "placeholder": "e.g. ICD-10, SNOMED"},
        {"key": "case_ascertainment", "label": "Case ascertainment", "type": "select",
         "options": ["Electronic health records", "Research study"], "default": True},
        {"key": "case_type", "label": "Case type", "type": "select", "options": ["Prevalent", "Incident", "Prevalent + incident"], "default": True},
    ]},
    {"key": "association", "title": "Association testing", "level": "dataset", "fields": [
        {"key": "association_software", "label": "Association software", "type": "text", "placeholder": "e.g. REGENIE v3, SAIGE"},
        {"key": "association_methods", "label": "Association testing methods", "type": "textarea", "placeholder": "Model, covariates, adjustments"},
    ]},
    {"key": "demographics", "title": "Age and sex distribution", "level": "dataset", "default": True, "grid": {
        "rows": [{"key": "age_mean", "label": "Mean age (years)", "type": "number", "min": 0, "max": 120},
                 {"key": "age_sd", "label": "SD of age", "type": "number", "min": 0, "max": 60},
                 {"key": "male_prop", "label": "Proportion male", "type": "number", "min": 0, "max": 1}],
        "cols": [{"key": k, "label": label} for k, label in AGE_GROUPS],
    }},
]


# Custom field types (the website's choices) -> the catalogue's types; yes/no is a select of Yes, No.
CUSTOM_TYPES = {"text": "text", "number": "number", "integer": "integer", "yesno": "select", "select": "select", "date": "date"}
CUSTOM_SECTIONS = {"cohort": ("custom_cohort", "Project fields (per cohort)"), "dataset": ("custom_dataset", "Project fields (per dataset)")}


def custom_field(c: dict) -> dict:
    """A custom field definition as a catalogue field."""
    f = {"key": c["key"], "label": c["label"], "type": CUSTOM_TYPES[c["type"]], "custom": True}
    if c["type"] == "yesno":
        f["options"] = YES_NO
    elif c["type"] == "select":
        f["options"] = list(c.get("options") or [])
    return f


def custom_sections(custom: list | None, include_hidden: bool = False) -> list:
    """The project's own fields as sections (per cohort, per dataset); hidden fields only with include_hidden."""
    out = []
    for level, (key, title) in CUSTOM_SECTIONS.items():
        fields = [custom_field(c) for c in (custom or []) if c["level"] == level and (include_hidden or not c.get("hidden"))]
        if fields:
            out.append({"key": key, "title": title, "level": level, "fields": fields})
    return out


def sections(custom: list | None = None, include_hidden: bool = False) -> list:
    return SECTIONS + custom_sections(custom, include_hidden)


def all_fields(custom: list | None = None):
    for sec in sections(custom, include_hidden=True):
        for f in sec.get("fields", []):
            yield f
        if "grid" in sec:
            for r in sec["grid"]["rows"]:
                for c in sec["grid"]["cols"]:
                    yield {**r, "key": f"{r['key']}_{c['key']}", "label": f"{r['label']}, {c['label'].lower()}"}


FIELDS = {f["key"]: f for f in all_fields()}


def fields(custom: list | None = None) -> dict:
    return FIELDS if not custom else {f["key"]: f for f in all_fields(custom)}


def validate(meta: dict | None, custom: list | None = None) -> dict:
    """Return a cleaned copy (empty values dropped). Raises 422 listing every problem."""
    meta = meta or {}
    known = fields(custom)
    errors, out = [], {}
    for k, v in meta.items():
        f = known.get(k)
        if f is None:
            errors.append(f"unknown field '{k}'")
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        t = f["type"]
        try:
            if t in ("text", "textarea"):
                v = str(v).strip()
                if len(v) > (5000 if t == "textarea" else 300):
                    raise ValueError("too long")
            elif t == "email":
                v = str(v).strip()
                if len(v) > 254 or not EMAIL.fullmatch(v):
                    raise ValueError("must be an email address, e.g. name@institution.org")
            elif t == "select":
                if v not in f["options"]:
                    raise ValueError(f"must be one of {', '.join(f['options'])}")
            elif t == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
                    raise ValueError("must be a date (YYYY-MM-DD)")
                date.fromisoformat(v)
            elif t in ("number", "integer"):
                if isinstance(v, bool):
                    raise ValueError("must be a number")
                v = int(v) if t == "integer" else float(v)
                if t == "integer" and float(meta[k]) != int(v):
                    raise ValueError("must be a whole number")
                if "min" in f and v < f["min"] or "max" in f and v > f["max"]:
                    raise ValueError(f"must be between {f.get('min', '-∞')} and {f.get('max', '∞')}")
        except (TypeError, ValueError) as e:
            errors.append(f"{f['label']}: {e}")
            continue
        out[k] = v
    s, e = out.get("data_collection_start"), out.get("data_collection_end")
    if s and e and e < s:
        errors.append("Data collection end is before the start")
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return out


# ---------- per-project configuration ----------
# A *unit* is what a project switches on or marks required: a field, or a whole grid section.

def units(custom: list | None = None):
    """[{key, label, section, level, default}] for every configurable unit, in catalogue order (hidden custom fields
    aren't units: they can't be shown or required)."""
    out = []
    for sec in sections(custom):
        if "grid" in sec:
            out.append({"key": sec["key"], "label": sec["title"], "section": sec["key"], "level": sec["level"],
                        "default": bool(sec.get("default"))})
        for f in sec.get("fields", []):
            out.append({"key": f["key"], "label": f["label"], "section": sec["key"], "level": sec["level"],
                        "default": bool(f.get("default"))})
    return out


UNITS = {u["key"]: u for u in units()}
COHORT_KEYS = {f["key"] for sec in SECTIONS if sec["level"] == "cohort" for f in sec.get("fields", [])}


def units_by_key(custom: list | None = None) -> dict:
    return UNITS if not custom else {u["key"]: u for u in units(custom)}


def cohort_keys(custom: list | None = None) -> set:
    """Cohort-level field keys (custom ones included, hidden or not: their values still flow to datasets)."""
    return COHORT_KEYS | {c["key"] for c in (custom or []) if c["level"] == "cohort"}


def default_config() -> dict:
    return {"shown": [u["key"] for u in units() if u["default"]], "required": []}


def normalise_config(cfg: dict | None, custom: list | None = None) -> dict:
    """A project's config, falling back to the defaults; unknown keys dropped; required ⊆ shown."""
    if not cfg:
        return default_config()
    shown = [k for k in units_by_key(custom) if k in set(cfg.get("shown") or [])]
    required = [k for k in shown if k in set(cfg.get("required") or [])]
    return {"shown": shown, "required": required}


def validate_config(cfg: dict, custom: list | None = None) -> dict:
    known = units_by_key(custom)
    unknown = [k for k in (cfg.get("shown") or []) + (cfg.get("required") or []) if k not in known]
    if unknown:
        raise HTTPException(422, f"Unknown metadata field(s): {', '.join(sorted(set(unknown)))}")
    not_shown = set(cfg.get("required") or []) - set(cfg.get("shown") or [])
    if not_shown:
        raise HTTPException(422, "A required field must also be shown: " + ", ".join(known[k]["label"] for k in sorted(not_shown)))
    return normalise_config(cfg, custom)


def unit_filled(meta: dict, key: str) -> bool:
    sec = next((s for s in SECTIONS if s["key"] == key and "grid" in s), None)
    if sec:
        return any(meta.get(f"{r['key']}_{c['key']}") not in (None, "") for r in sec["grid"]["rows"] for c in sec["grid"]["cols"])
    return meta.get(key) not in (None, "")


def completeness(meta: dict | None, cfg: dict | None = None, custom: list | None = None) -> dict:
    """How complete a record is against the project's shown fields; lists required fields that are missing."""
    cfg, meta, known = normalise_config(cfg, custom), meta or {}, units_by_key(custom)
    filled = sum(1 for k in cfg["shown"] if unit_filled(meta, k))
    missing = [known[k]["label"] for k in cfg["required"] if not unit_filled(meta, k)]
    return {"filled": filled, "total": len(cfg["shown"]), "missing_required": missing}


def missing_required(meta: dict | None, cfg: dict | None, level: str | None = None, custom: list | None = None) -> list[str]:
    cfg, meta, known = normalise_config(cfg, custom), meta or {}, units_by_key(custom)
    return [known[k]["label"] for k in cfg["required"]
            if (level is None or known[k]["level"] == level) and not unit_filled(meta, k)]


def effective(cohort_meta: dict | None, dataset_meta: dict | None, custom: list | None = None) -> dict:
    """A dataset's metadata: its cohort's values, with the dataset's own values (including overrides) on top."""
    keys = cohort_keys(custom)
    return {**{k: v for k, v in (cohort_meta or {}).items() if k in keys}, **(dataset_meta or {})}


def split(meta: dict, custom: list | None = None) -> tuple[dict, dict]:
    """(cohort-level part, dataset-level part) of a metadata dict."""
    keys = cohort_keys(custom)
    return ({k: v for k, v in meta.items() if k in keys}, {k: v for k, v in meta.items() if k not in keys})
