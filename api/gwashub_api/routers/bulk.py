"""Bulk upload support for the upload script (client/gwashub_upload.py): the manifest template (an Excel workbook,
manifest_xlsx.py), reading a filled-in manifest, the dry-run check of all its rows, and the status of the caller's
uploads. Each accepts the session cookie or an API token (auth.upload_caller). The files themselves go through the
ordinary upload endpoints (routers/datasets.py).

The manifest has one row per file, every value spelled out: no column suggestions or guesses. The check reports every
problem at once; the script uploads nothing unless there are none. Duplicates are refused."""
import base64
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import manifest_xlsx, metadata_schema, services
from gwashub_api.auth import Caller, current_user, require_member, upload_caller
from gwashub_api.config import settings
from gwashub_api.db import get_db
from gwashub_api.models import Cohort, Dataset, User
from gwashub_api.routers.datasets import check_upload

router = APIRouter(prefix="/api", tags=["bulk"])

BASE_REQUIRED = ["file", "cohort", "phenotype", "ancestry", "sex", "build", "effect_type"]
BASE_OPTIONAL = ["n_cases", "n_controls", "n_total", "sha256"]
COUNTS = ("n_cases", "n_controls", "n_total")
COL_REQUIRED = ["chr", "pos", "ea", "oa", "effect", "se", "eaf"]
COL_OPTIONAL = ["p", "log10p", "n", "ncase", "ncontrol", "rsid", "info"]
EFFECT_TYPES = {"beta": "beta", "odds_ratio": "or"}  # manifest value -> the dataset's effect_type
MAX_WORKBOOK = 20 * 1024 * 1024


def meta_fields(project) -> dict:
    """Metadata fields a manifest may fill (meta_<key>): every field except hidden project fields."""
    custom = services.custom_fields(project)
    hidden = {c["key"] for c in custom if c.get("hidden")}
    return {k: f for k, f in metadata_schema.fields(custom).items() if k not in hidden}


def manifest_columns(project) -> list[str]:
    return (BASE_REQUIRED + BASE_OPTIONAL + [f"col_{c}" for c in COL_REQUIRED + COL_OPTIONAL]
            + [f"meta_{k}" for k in meta_fields(project)])


def caller_member(db, caller: Caller, project_id: str):
    caller.check_project(project_id)
    return require_member(db, caller.user, project_id)


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "client", "gwashub_upload.py")


@router.get("/bulk/script")
def script(user: User = Depends(current_user)):
    """The upload script, with this site's address filled in (signed-in users)."""
    with open(SCRIPT) as f:
        text = f.read()
    text = text.replace('SERVER = ""', f'SERVER = "{settings().public_url}"', 1)
    return Response(text, media_type="text/x-python", headers={"Content-Disposition": 'attachment; filename="gwashub_upload.py"'})


@router.get("/bulk/whoami")
def whoami(caller: Caller = Depends(upload_caller)):
    """Who the script is acting as: the user, and the token's project and expiry."""
    t = caller.token
    return {"name": caller.user.name, "email": caller.user.email,
            "token": {"name": t.name, "project_id": t.project_id, "expires_at": t.expires_at} if t else None}


# ---------- the workbook ----------

@router.get("/projects/{project_id}/bulk/template")
def template(project_id: str, caller: Caller = Depends(upload_caller), db: Session = Depends(get_db)):
    """An empty manifest workbook for the project: README, the Manifest sheet with drop-downs, the project's metadata
    fields (those it shows; others may be added as meta_<key>)."""
    p = caller_member(db, caller, project_id).project
    cohorts = [c.name for c in db.scalars(select(Cohort).where(Cohort.project_id == p.id).order_by(Cohort.name))]
    content = manifest_xlsx.template(p, services.custom_fields(p), services.metadata_config(p), cohorts)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="gwashub_manifest_{p.id}.xlsx"'})


class Workbook(BaseModel):
    content: str  # the .xlsx file, base64


@router.post("/projects/{project_id}/bulk/parse")
def parse(project_id: str, body: Workbook, caller: Caller = Depends(upload_caller), db: Session = Depends(get_db)):
    """A filled-in manifest workbook's columns and rows (the script reads the files the rows name, then calls check)."""
    caller_member(db, caller, project_id)
    try:
        content = base64.b64decode(body.content, validate=True)
    except ValueError:
        raise HTTPException(422, "The workbook wasn't sent as base64")
    if len(content) > MAX_WORKBOOK:
        raise HTTPException(422, "The workbook is larger than 20 MB")
    try:
        cols, rows = manifest_xlsx.read(content)
    except manifest_xlsx.ManifestError as e:
        raise HTTPException(422, str(e))
    if not rows:
        raise HTTPException(422, "The Manifest sheet has no rows")
    return {"columns": cols, "rows": rows}


# ---------- check ----------

class BulkRow(BaseModel):
    row: int  # the row number in the Manifest sheet, for messages
    values: dict[str, str | None]
    header: list[str] | None = None  # the file's header (the script reads it); None if the file couldn't be read
    file_error: str | None = None  # what went wrong reading the file
    file_size: int | None = None


class BulkCheck(BaseModel):
    columns: list[str]
    rows: list[BulkRow]
    own_ids: list[int] = []  # datasets already uploaded from this manifest (a resumed upload): not duplicates


def clean(v):
    return None if v is None or (isinstance(v, str) and not v.strip()) else v.strip() if isinstance(v, str) else v


@router.post("/projects/{project_id}/bulk/check")
def check(project_id: str, body: BulkCheck, caller: Caller = Depends(upload_caller), db: Session = Depends(get_db)):
    """Every problem in a manifest, by row; creates nothing. ok is true only when there are none; then uploads has
    each row's details exactly as the upload endpoint takes them ({row number: meta})."""
    p = caller_member(db, caller, project_id).project
    if not body.rows:
        raise HTTPException(422, "The manifest has no rows")
    if len(body.rows) > 1000:
        raise HTTPException(422, "Up to 1,000 rows per manifest")
    custom = services.custom_fields(p)
    labels = {k: f["label"] for k, f in meta_fields(p).items()}
    cohort_level = metadata_schema.cohort_keys(custom)
    problems: list[dict] = []

    def problem(row, msg):
        problems.append({"row": row, "message": msg})

    # Columns: fixed names; unknown ones are errors (a typo mustn't be ignored).
    allowed, cols = set(manifest_columns(p)), body.columns
    for c in cols:
        if c not in allowed:
            hidden = c.startswith("meta_") and c[5:] in {x["key"] for x in custom if x.get("hidden")}
            problem(None, f"Column '{c}' is {'a hidden project field' if hidden else 'not a manifest column'}")
    for c in BASE_REQUIRED + [f"col_{x}" for x in COL_REQUIRED]:
        if c not in cols:
            problem(None, f"Column '{c}' is missing")
    if "col_p" not in cols and "col_log10p" not in cols:
        problem(None, "Give col_p or col_log10p")
    for c in {c for c in cols if cols.count(c) > 1}:
        problem(None, f"Column '{c}' appears more than once")

    checked = []  # (row, fields, existing cohort, cohort-level values given, values)
    uploads = {}
    for r in body.rows:
        v = {k: clean(x) for k, x in r.values.items()}
        if r.file_error:
            problem(r.row, f"File {v.get('file') or '(no name)'}: {r.file_error}")
        bad = [k for k in COUNTS if v.get(k) is not None and not str(v[k]).isdigit()]
        for k in bad:
            problem(r.row, f"{k} must be a whole number, not '{v[k]}'")
        given_cols = {k: x for k, x in v.items() if k.startswith("col_") and x}
        if r.header is not None:
            for k, h in given_cols.items():
                if h not in r.header:
                    problem(r.row, f"{k}: the file has no column '{h}' (its columns: {', '.join(r.header[:30])})")
        for h in {h for h in given_cols.values() if list(given_cols.values()).count(h) > 1}:
            problem(r.row, f"The file column '{h}' is given for more than one col_*")
        mapping = {("beta" if k == "col_effect" else k[4:]): x for k, x in given_cols.items()}  # QC's names
        effect = EFFECT_TYPES.get(v.get("effect_type") or "")
        if not effect:
            problem(r.row, f"effect_type must be beta or odds_ratio, not '{v.get('effect_type') or ''}'")
        if v.get("phenotype") in p.phenotypes:  # sample sizes follow the trait type; the other columns must be empty
            if services.trait_type(p, v["phenotype"]) == "quantitative":
                for k in ("n_cases", "n_controls"):
                    if v.get(k) is not None:
                        problem(r.row, f"{k}: leave it empty, {v['phenotype']} is a quantitative phenotype (give n_total)")
            elif v.get("n_total") is not None and not bad and v.get("n_cases") is not None and v.get("n_controls") is not None \
                    and int(v["n_total"]) != int(v["n_cases"]) + int(v["n_controls"]):
                problem(r.row, f"n_total ({int(v['n_total']):,}) isn't n_cases + n_controls "
                               f"({int(v['n_cases']) + int(v['n_controls']):,}); correct it or leave it empty")
        meta = {k[5:]: x for k, x in v.items() if k.startswith("meta_") and x is not None and k[5:] in labels}
        d = {"cohort": v.get("cohort"), "phenotype": v.get("phenotype"), "ancestry": v.get("ancestry"), "sex": v.get("sex"),
             "build": v.get("build"), "effect_type": effect or "beta", "column_mapping": mapping, "study_meta": meta,
             **{k: int(v[k]) if v.get(k) is not None and k not in bad else None for k in COUNTS}}
        try:
            fields, cohort, clean_meta = check_upload(db, p, d)
        except HTTPException as e:
            if not (bad and any(w in str(e.detail) for w in ("cases", "controls", "sample size"))):  # already reported
                problem(r.row, str(e.detail))
            continue
        given = {k: x for k, x in clean_meta.items() if k in cohort_level}
        checked.append((r, fields, cohort, given, v))
        uploads[r.row] = {k: d[k] for k in ("cohort", "phenotype", "ancestry", "sex", "build", "effect_type", "column_mapping", "study_meta")}
        uploads[r.row].update({k: d[k] for k in COUNTS if d[k] is not None})

    # Cohorts: an existing cohort's record is never changed by a manifest; a new cohort's rows must agree.
    new_cohorts: dict[str, list] = {}
    for r, fields, cohort, given, _ in checked:
        if cohort:
            for k, x in given.items():
                if (cohort.meta or {}).get(k) != x:
                    rec = (cohort.meta or {}).get(k)
                    problem(r.row, f"meta_{k} ({labels.get(k, k)}) differs from cohort {cohort.name}'s record "
                                   f"({'empty' if rec is None else rec}); leave it empty to use the record, or change the cohort on the site")
        else:
            new_cohorts.setdefault(services.norm_name(fields["cohort"]), []).append((r, fields, given))
    for items in new_cohorts.values():
        first_row, first_fields, first = items[0]
        for r, _, given in items[1:]:
            for k in sorted(set(first) | set(given)):
                if first.get(k) != given.get(k):
                    problem(r.row, f"meta_{k} ({labels.get(k, k)}) differs from row {first_row.row} for new cohort "
                                   f"{first_fields['cohort']}: every row of a cohort must give the same cohort values")

    # Duplicates: within the manifest, and against the project's datasets (except this manifest's own uploads).
    own = {i for i in body.own_ids if (ds := db.get(Dataset, i)) and ds.project_id == project_id and ds.uploaded_by == caller.user.id}
    existing = [ds for ds in db.scalars(select(Dataset).where(Dataset.project_id == project_id)) if ds.id not in own]
    seen_file, seen_combo = {}, {}
    for r, fields, _, _, v in checked:
        combo = (services.norm_name(fields["cohort"]), fields["phenotype"], fields["ancestry"], fields["sex"])
        label = " · ".join([fields["cohort"], *combo[1:]])
        if v.get("file") in seen_file:
            problem(r.row, f"The same file as row {seen_file[v['file']]}")
        seen_file.setdefault(v.get("file"), r.row)
        if combo in seen_combo:
            problem(r.row, f"The same cohort, phenotype, ancestry and sex as row {seen_combo[combo]} ({label})")
        seen_combo.setdefault(combo, r.row)
        name = (v.get("file") or "").replace("\\", "/").rsplit("/", 1)[-1]
        sha = (v.get("sha256") or "").lower()
        for ds in existing:
            same = ((services.norm_name(ds.cohort), ds.phenotype, ds.ancestry, ds.sex) == combo
                    or (sha and ds.sha256 == sha) or (r.file_size and ds.file_name == name and ds.file_size == r.file_size))
            if same:
                problem(r.row, f"Matches dataset {ds.id} ({ds.cohort} · {ds.phenotype} · {ds.ancestry} · {ds.sex}, "
                               f"{ds.file_name}): delete it on the site first, or remove this row")
                break

    problems.sort(key=lambda x: (x["row"] is not None, x["row"] or 0))
    new_names = {items[0][1]["cohort"] for items in new_cohorts.values()}
    return {"ok": not problems, "problems": problems, "rows": len(body.rows), "new_cohorts": sorted(new_names),
            "uploads": uploads if not problems else {}}


# ---------- status ----------

@router.get("/projects/{project_id}/bulk/status")
def status(project_id: str, ids: str, caller: Caller = Depends(upload_caller), db: Session = Depends(get_db)):
    """The caller's own datasets among ids (comma-separated): status and where to find them."""
    caller_member(db, caller, project_id)
    out = []
    for i in [int(x) for x in ids.split(",") if x.strip().isdigit()][:1000]:
        ds = db.get(Dataset, i)
        if not ds or ds.project_id != project_id or ds.uploaded_by != caller.user.id:
            out.append({"id": i, "status": "not found"})
            continue
        out.append({"id": ds.id, "status": ds.status, "file_name": ds.file_name, "cohort": ds.cohort, "phenotype": ds.phenotype,
                    "ancestry": ds.ancestry, "sex": ds.sex, "path": f"/projects/{project_id}/datasets?open={ds.id}"})
    return out
