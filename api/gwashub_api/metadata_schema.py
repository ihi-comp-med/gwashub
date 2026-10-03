"""Study metadata collected for every dataset (based on the HERMES data-registry upload form).

One definition drives the upload form, the dataset viewer (view + edit) and validation.
All fields are optional; the viewer shows how complete the record is.
"""
import re
from datetime import date

from fastapi import HTTPException

AGE_GROUPS = [("total", "Total cohort"), ("controls", "Controls"), ("cases_diagnosis", "Cases at diagnosis"),
              ("cases_recruitment", "Cases at recruitment")]

SECTIONS = [
    {"key": "study", "title": "Study", "fields": [
        {"key": "data_collection_start", "label": "Data collection start", "type": "date"},
        {"key": "data_collection_end", "label": "Data collection end", "type": "date"},
        {"key": "contact_person", "label": "Contact person", "type": "text", "placeholder": "Name and email"},
        {"key": "references", "label": "References", "type": "textarea", "placeholder": "PMIDs or DOIs describing the cohort"},
        {"key": "acknowledgements", "label": "Acknowledgements", "type": "textarea"},
    ]},
    {"key": "participants", "title": "Participants", "fields": [
        {"key": "case_definition", "label": "Case definition", "type": "textarea", "placeholder": "e.g. ICD-10 I50.x in any position, or adjudicated"},
        {"key": "case_ascertainment", "label": "Case ascertainment", "type": "select",
         "options": ["Electronic health records", "Research study"]},
        {"key": "case_type", "label": "Case type", "type": "select", "options": ["Prevalent", "Incident", "Prevalent + incident"]},
    ]},
    {"key": "genotyping", "title": "Genotyping", "fields": [
        {"key": "genotyping_array", "label": "Genotyping array", "type": "text", "placeholder": "e.g. UK Biobank Axiom"},
        {"key": "calling_algorithm", "label": "Calling algorithm", "type": "text", "placeholder": "e.g. Affymetrix Power Tools"},
    ]},
    {"key": "imputation", "title": "Imputation", "fields": [
        {"key": "imputation_software", "label": "Imputation software", "type": "text", "placeholder": "e.g. IMPUTE4, Minimac4"},
        {"key": "imputation_reference", "label": "Imputation reference panel", "type": "text", "placeholder": "e.g. HRC r1.1, TOPMed r2"},
        {"key": "n_variants_for_imputation", "label": "Variants used for imputation", "type": "integer", "min": 0},
        {"key": "imputation_quality_measure", "label": "Imputation quality measure", "type": "text", "placeholder": "e.g. INFO, Rsq"},
    ]},
    {"key": "genotyping_qc", "title": "Genotyping QC", "fields": [
        {"key": "related_removed", "label": "Related individuals removed", "type": "select", "options": ["Yes", "No"]},
        {"key": "variant_call_rate", "label": "Variant call rate threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 0.98"},
        {"key": "sample_call_rate", "label": "Sample call rate threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 0.97"},
        {"key": "hwe_p", "label": "HWE P threshold", "type": "number", "min": 0, "max": 1, "placeholder": "e.g. 1e-6"},
        {"key": "maf", "label": "MAF threshold", "type": "number", "min": 0, "max": 0.5, "placeholder": "e.g. 0.01"},
        {"key": "other_filters", "label": "Other filters", "type": "textarea"},
    ]},
    {"key": "demographics", "title": "Age and sex distribution", "grid": {
        "rows": [{"key": "age_mean", "label": "Mean age (years)", "type": "number", "min": 0, "max": 120},
                 {"key": "age_sd", "label": "SD of age", "type": "number", "min": 0, "max": 60},
                 {"key": "male_prop", "label": "Proportion male", "type": "number", "min": 0, "max": 1}],
        "cols": [{"key": k, "label": label} for k, label in AGE_GROUPS],
    }},
]


def all_fields():
    for sec in SECTIONS:
        for f in sec.get("fields", []):
            yield f
        if "grid" in sec:
            for r in sec["grid"]["rows"]:
                for c in sec["grid"]["cols"]:
                    yield {**r, "key": f"{r['key']}_{c['key']}", "label": f"{r['label']}, {c['label'].lower()}"}


FIELDS = {f["key"]: f for f in all_fields()}


def validate(meta: dict | None) -> dict:
    """Return a cleaned copy (empty values dropped). Raises 422 listing every problem."""
    meta = meta or {}
    errors, out = [], {}
    for k, v in meta.items():
        f = FIELDS.get(k)
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


def completeness(meta: dict | None) -> dict:
    filled = sum(1 for k in FIELDS if (meta or {}).get(k) not in (None, ""))
    return {"filled": filled, "total": len(FIELDS)}
