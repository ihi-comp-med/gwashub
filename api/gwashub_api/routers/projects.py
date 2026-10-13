from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import re

import logging

from gwashub_api import metadata_schema, practice, services, storage
from gwashub_api.accounts import InviteError, find_or_invite
from gwashub_api.auth import current_user, membership, require_member
from gwashub_api.db import get_db
from gwashub_api.models import ApiToken, Cohort, Dataset, Membership, MetaAnalysis, Pipeline, PipelineVersion, Project, Resource, User
from gwashub_api.services import current_pipeline

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["projects"])
STATUSES = ("passed", "review", "changes_requested", "failed", "processing", "pending")


def counts(db, project_id):
    c = {s: 0 for s in STATUSES}
    for (st,) in db.execute(select(Dataset.status).where(Dataset.project_id == project_id)):
        c[st] = c.get(st, 0) + 1
    c["total"] = sum(c[s] for s in STATUSES)
    return c


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "institution": user.institution, "is_admin": user.is_admin}


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    practice.ensure_member(db, user)
    out = []
    for m in db.scalars(select(Membership).where(Membership.user_id == user.id)):
        p = m.project
        out.append({"id": p.id, "name": p.name, "short": p.short, "description": p.description, "role": m.role,
                    "members": len(p.members), "counts": counts(db, p.id), "is_practice": p.id == practice.PROJECT_ID})
    return sorted(out, key=lambda p: p["name"])


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    p = db.get(Project, project_id)
    uploads = {}
    for (uid,) in db.execute(select(Dataset.uploaded_by).where(Dataset.project_id == project_id)):
        uploads[uid] = uploads.get(uid, 0) + 1
    pv = current_pipeline(db, project_id)
    return {
        "id": p.id, "name": p.name, "short": p.short, "description": p.description, "phenotypes": p.phenotypes,
        "phenotype_descriptions": p.phenotype_descriptions or {}, **project_metadata(p),
        "phenotype_types": {ph: services.trait_type(p, ph) for ph in p.phenotypes}, "metadata_config": services.metadata_config(p),
        "is_admin": user.is_admin,
        "ancestries": p.ancestries, "sexes": p.sexes,
        "created_at": p.created_at, "role": m.role, "counts": counts(db, p.id), "is_practice": p.id == practice.PROJECT_ID,
        # The caller's datasets a reviewer sent back (a badge on the Datasets tab)
        "my_changes_requested": db.scalar(select(func.count()).select_from(Dataset).where(
            Dataset.project_id == project_id, Dataset.uploaded_by == user.id, Dataset.status == "changes_requested")),
        # Everyone is in the Practice project: each person sees only themselves there.
        "members": [{"id": x.user.id, "name": x.user.name, "institution": x.user.institution, "role": x.role,
                     "is_me": x.user_id == user.id, "uploads": uploads.get(x.user_id, 0),
                     **({"email": x.user.email} if m.role == "reviewer" else {})}  # reviewers manage members
                    for x in sorted(p.members, key=lambda x: x.user.name.lower())
                    if p.id != practice.PROJECT_ID or x.user_id == user.id],
        "pipeline": pv and {"version": pv.version, "steps": len(pv.steps), "created_at": pv.created_at,
                            "consortium_steps": sum(1 for s in pv.steps if not s["step"].startswith("gwashub/"))},
        # The project's pipelines (for choosing a dataset's pipeline), default first.
        "pipelines": [{"id": pp.id, "name": pp.name, "is_default": pp.is_default,
                       "version": getattr(services.current_version(db, pp.id), "version", None)}
                      for pp in db.scalars(select(Pipeline).where(Pipeline.project_id == p.id).order_by(Pipeline.is_default.desc(), Pipeline.name))],
    }


ALL_ANCESTRIES = ["EUR", "AFR", "EAS", "SAS", "AMR", "Multi"]
ALL_SEXES = ["Combined", "Female", "Male"]


def phenotype_types(phenos: list[str], given: dict) -> dict:
    """Only the quantitative phenotypes are stored (binary is the default)."""
    bad = sorted({v for v in given.values() if v not in services.TRAIT_TYPES})
    if bad:
        raise HTTPException(422, f"Trait types are binary or quantitative, not {', '.join(bad)}")
    return {ph: "quantitative" for ph in phenos if given.get(ph) == "quantitative"}


class UploadOptions(BaseModel):
    phenotypes: list[str]  # codes
    phenotype_descriptions: dict[str, str] | None = None  # code -> description
    phenotype_types: dict[str, str] | None = None  # code -> binary | quantitative (unlisted: binary)
    ancestries: list[str]
    sexes: list[str]


@router.put("/projects/{project_id}/upload-options")
def put_upload_options(project_id: str, body: UploadOptions, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewer-only: the phenotypes, ancestries and sex strata offered (and accepted) at upload."""
    m = require_member(db, user, project_id, role="reviewer")
    phenos = list(dict.fromkeys(p.strip() for p in body.phenotypes if p.strip()))
    if not phenos:
        raise HTTPException(422, "Add at least one phenotype")
    if any(len(p) > 120 for p in phenos):
        raise HTTPException(422, "Phenotype names must be 120 characters or fewer")
    bad = [a for a in body.ancestries if a not in ALL_ANCESTRIES] + [s for s in body.sexes if s not in ALL_SEXES]
    if bad:
        raise HTTPException(422, f"Unknown value(s): {', '.join(bad)}")
    if not body.ancestries or not body.sexes:
        raise HTTPException(422, "Allow at least one ancestry and one sex stratum")
    p = m.project
    if body.phenotype_descriptions is not None:
        descs = {k: str(v).strip() for k, v in body.phenotype_descriptions.items() if k in phenos and str(v).strip()}
        if any(len(v) > 300 for v in descs.values()):
            raise HTTPException(422, "Phenotype descriptions must be 300 characters or fewer")
        p.phenotype_descriptions = descs
    else:
        p.phenotype_descriptions = {k: v for k, v in (p.phenotype_descriptions or {}).items() if k in phenos}
    if body.phenotype_types is not None:
        types = phenotype_types(phenos, body.phenotype_types)
        # A phenotype's datasets were described for its type (cases/controls or a total), so it can't change under them.
        in_use = {ph for (ph,) in db.execute(select(Dataset.phenotype).where(Dataset.project_id == project_id))}
        changed = [ph for ph in phenos if ph in in_use and types.get(ph, "binary") != services.trait_type(p, ph)]
        if changed:
            raise HTTPException(409, f"{', '.join(changed)} already has datasets, so its trait type can't change")
        p.phenotype_types = types
    else:
        p.phenotype_types = {k: v for k, v in (p.phenotype_types or {}).items() if k in phenos}
    p.phenotypes = phenos
    p.ancestries = [a for a in ALL_ANCESTRIES if a in body.ancestries]
    p.sexes = [s for s in ALL_SEXES if s in body.sexes]
    db.commit()
    in_use = {ph for (ph,) in db.execute(select(Dataset.phenotype).where(Dataset.project_id == project_id))}
    return {"phenotypes": p.phenotypes, "phenotype_descriptions": p.phenotype_descriptions,
            "phenotype_types": {ph: services.trait_type(p, ph) for ph in p.phenotypes}, "ancestries": p.ancestries,
            "sexes": p.sexes, "removed_but_in_use": sorted(in_use - set(phenos))}


# ---------- metadata fields shown per project ----------

class MetadataConfig(BaseModel):
    shown: list[str]
    required: list[str] = []


@router.put("/projects/{project_id}/metadata-config")
def put_metadata_config(project_id: str, body: MetadataConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Which metadata fields this project shows, and which must be filled in at upload. Reviewers only (a platform
    admin is the first reviewer of a project they create). Existing datasets missing a newly required field are
    shown as "metadata incomplete"; nothing is blocked."""
    m = require_member(db, user, project_id, role="reviewer")
    m.project.metadata_config = metadata_schema.validate_config(body.model_dump(), services.custom_fields(m.project))
    db.commit()
    return m.project.metadata_config


class CustomField(BaseModel):
    key: str | None = None  # absent for a new field (one is made from the label)
    label: str
    level: str  # cohort | dataset
    type: str  # text | number | integer | yesno | select | date
    options: list[str] = []  # select only
    hidden: bool = False  # "deleted": off the forms, values kept


class CustomFields(BaseModel):
    fields: list[CustomField]


@router.put("/projects/{project_id}/metadata-fields")
def put_metadata_fields(project_id: str, body: CustomFields, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewers manage the project's own metadata fields: add, rename, add choices, hide (delete) or unhide. A field's
    key, level and type are fixed once it exists, its choices can only grow, and it can't be removed outright (hide it),
    so saved values and the edit history stay valid. Hidden fields leave the shown/required settings."""
    m = require_member(db, user, project_id, role="reviewer")
    p = m.project
    old = {c["key"]: c for c in services.custom_fields(p)}
    builtin = {f["label"].lower() for f in metadata_schema.FIELDS.values()}
    out, labels = [], set()
    for f in body.fields:
        label = " ".join(f.label.split())
        if not label or len(label) > 120:
            raise HTTPException(422, "Give each field a name of up to 120 characters")
        if label.lower() in labels or label.lower() in builtin:
            raise HTTPException(422, f"There's already a field called {label}")
        labels.add(label.lower())
        if f.level not in metadata_schema.CUSTOM_SECTIONS or f.type not in metadata_schema.CUSTOM_TYPES:
            raise HTTPException(422, f"{label}: level must be cohort or dataset, and type one of {', '.join(metadata_schema.CUSTOM_TYPES)}")
        options = list(dict.fromkeys(o.strip() for o in f.options if o.strip())) if f.type == "select" else []
        if f.type == "select" and not options:
            raise HTTPException(422, f"{label}: a choice field needs at least one choice")
        if f.key:
            was = old.get(f.key)
            if not was:
                raise HTTPException(422, f"No field {f.key} in this project")
            if (was["level"], was["type"]) != (f.level, f.type):
                raise HTTPException(422, f"{label}: a field's level and type can't change once it exists (add a new field instead)")
            lost = [o for o in was.get("options") or [] if o not in options]
            if lost:
                raise HTTPException(422, f"{label}: choices can be added but not removed ({', '.join(lost)})")
            key = f.key
        else:
            base = "custom_" + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "field")
            key, n = base, 2
            while key in old or key in metadata_schema.FIELDS or any(c["key"] == key for c in out):
                key, n = f"{base}_{n}", n + 1
        out.append({"key": key, "label": label, "level": f.level, "type": f.type, "options": options, "hidden": f.hidden})
    missing = [old[k]["label"] for k in old if k not in {c["key"] for c in out}]
    if missing:
        raise HTTPException(422, f"Fields can't be removed, only hidden: {', '.join(missing)}")
    p.metadata_custom = out
    # New fields start shown; hidden fields can't be shown or required.
    cfg = services.metadata_config(p)
    added = [c["key"] for c in out if c["key"] not in old and not c["hidden"]]
    p.metadata_config = metadata_schema.normalise_config({**cfg, "shown": cfg["shown"] + added}, out)
    db.commit()
    return project_metadata(p)


def project_metadata(p) -> dict:
    """The project's metadata settings for the website: its own fields (for editing, and as catalogue sections and
    units to add to the built-in ones) and which fields are shown and required."""
    custom = services.custom_fields(p)
    return {"metadata_custom": custom, "metadata_custom_sections": metadata_schema.custom_sections(custom),
            "metadata_custom_units": [u for u in metadata_schema.units(custom) if u["section"] in ("custom_cohort", "custom_dataset")],
            "metadata_config": services.metadata_config(p)}


@router.get("/projects/{project_id}/summary")
def summary(project_id: str, by: str = "phenotype_ancestry_sex", user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Dataset counts by QC status, grouped by phenotype×ancestry×sex (default) or a subset (visible to all members)."""
    require_member(db, user, project_id)
    groupings = {"phenotype_ancestry_sex": ("phenotype", "ancestry", "sex"), "ancestry_sex": ("ancestry", "sex"),
                 "ancestry": ("ancestry",), "sex": ("sex",), "phenotype": ("phenotype",)}
    if by not in groupings:
        raise HTTPException(422, f"by must be one of {', '.join(groupings)}")
    fields = groupings[by]
    rows = {}
    for ds in db.scalars(select(Dataset).where(Dataset.project_id == project_id, Dataset.status.in_(STATUSES))):
        key = tuple(getattr(ds, f) for f in fields)
        r = rows.setdefault(key, {**dict(zip(fields, key)), **{s: 0 for s in STATUSES}, "total": 0})
        r[ds.status] += 1
        r["total"] += 1
    return {"by": by, "fields": fields, "rows": sorted(rows.values(), key=lambda r: tuple(str(r[f]) for f in fields)),
            "totals": counts(db, project_id)}


@router.get("/projects/{project_id}/sample-sizes")
def sample_sizes(project_id: str, exclude_failed: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Cases, controls and total N summed over the project's datasets, by phenotype (when it has several), ancestry and
    sex, in the project's own order (visible to all members). Every dataset by default; exclude_failed leaves out those
    that failed QC (they won't be meta-analysed). Cases and controls are None for a quantitative phenotype, which gives
    only its total."""
    m = require_member(db, user, project_id)
    p = m.project
    fields = (("phenotype",) if len(p.phenotypes) > 1 else ()) + ("ancestry", "sex")
    rows = {}
    statuses = [s for s in STATUSES if not (exclude_failed and s == "failed")]
    for ds in db.scalars(select(Dataset).where(Dataset.project_id == project_id, Dataset.status.in_(statuses))):
        key = tuple(getattr(ds, f) for f in fields)
        quantitative = services.trait_type(p, ds.phenotype) == "quantitative"
        r = rows.setdefault(key, {**dict(zip(fields, key)), "datasets": 0, "n_cases": None if quantitative else 0,
                                  "n_controls": None if quantitative else 0, "n_total": 0})
        r["datasets"] += 1
        r["n_total"] += ds.n_total or 0
        if not quantitative:
            r["n_cases"] += ds.n_cases or 0
            r["n_controls"] += ds.n_controls or 0
    rank = {"phenotype": p.phenotypes, "ancestry": p.ancestries, "sex": ["Male", "Female", "Combined"]}
    order = lambda r: tuple(rank[f].index(r[f]) if r[f] in rank[f] else len(rank[f]) for f in fields)
    return {"fields": fields, "rows": sorted(rows.values(), key=order)}


# ---------- members (reviewers manage their project's members) ----------

class MemberIn(BaseModel):
    email: str
    name: str = ""
    institution: str = ""
    role: str


class RoleIn(BaseModel):
    role: str


def _check_role(role: str):
    if role not in ("uploader", "reviewer"):
        raise HTTPException(422, "Role must be uploader or reviewer")


def _reviewer_count(db, project_id) -> int:
    return len(db.scalars(select(Membership).where(Membership.project_id == project_id, Membership.role == "reviewer")).all())


@router.post("/projects/{project_id}/members", status_code=201)
def add_member(project_id: str, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewer-only. Adds someone to the project; new people get an account and an invitation email."""
    require_member(db, user, project_id, role="reviewer")
    if project_id == practice.PROJECT_ID:
        raise HTTPException(409, "Everyone is a reviewer in the Practice project: its members can't be changed")
    _check_role(body.role)
    try:
        person, how = find_or_invite(db, body.email, body.name, body.institution)
    except InviteError as e:
        db.rollback()
        raise HTTPException(e.status, str(e))
    if membership(db, person, project_id):
        db.rollback()
        raise HTTPException(409, f"{person.name} is already a member of this project")
    db.add(Membership(user_id=person.id, project_id=project_id, role=body.role))
    db.commit()
    return {"id": person.id, "name": person.name, "email": person.email, "role": body.role, "account": how}


@router.patch("/projects/{project_id}/members/{user_id}")
def change_role(project_id: str, user_id: int, body: RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, project_id, role="reviewer")
    if project_id == practice.PROJECT_ID:
        raise HTTPException(409, "Everyone is a reviewer in the Practice project: its members can't be changed")
    _check_role(body.role)
    m = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id))
    if not m:
        raise HTTPException(404, "Not a member of this project")
    if m.role == "reviewer" and body.role != "reviewer" and _reviewer_count(db, project_id) == 1:
        raise HTTPException(409, "A project needs at least one reviewer")
    m.role = body.role
    db.commit()
    return {"id": user_id, "role": m.role}


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
def remove_member(project_id: str, user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewer-only. The person keeps their account and their uploaded datasets stay in the project."""
    require_member(db, user, project_id, role="reviewer")
    if project_id == practice.PROJECT_ID:
        raise HTTPException(409, "Everyone is a reviewer in the Practice project: its members can't be changed")
    m = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id))
    if not m:
        raise HTTPException(404, "Not a member of this project")
    if m.role == "reviewer" and _reviewer_count(db, project_id) == 1:
        raise HTTPException(409, "A project needs at least one reviewer")
    db.delete(m)
    db.commit()


# ---------- projects (platform admins create them) ----------

METADATA_PRESETS = {"default": None, "all": lambda: {"shown": list(metadata_schema.UNITS), "required": []}}


class ProjectIn(BaseModel):
    id: str
    name: str
    short: str
    description: str = ""
    phenotypes: list[str]
    phenotype_types: dict[str, str] = {}  # code -> binary | quantitative (unlisted: binary)
    metadata_preset: str = "default"  # "default" (the HERMES set) or "all" (every field, HERMES and SGC)
    template: str = "default"  # the pipeline template the first pipeline starts from (pipeline/qc_steps/templates)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Platform admins only. The creator becomes the project's first reviewer and it gets the default pipeline."""
    if not user.is_admin:
        raise HTTPException(403, "Only platform admins can create projects")
    pid = body.id.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", pid):
        raise HTTPException(422, "The project ID must be 2 to 40 lower-case letters, digits or hyphens, starting with a letter")
    if db.get(Project, pid):
        raise HTTPException(409, f"A project with the ID {pid} already exists")
    name, short = body.name.strip(), body.short.strip()
    phenos = list(dict.fromkeys(p.strip() for p in body.phenotypes if p.strip()))
    if not name or not short or len(name) > 300 or len(short) > 50:
        raise HTTPException(422, "Give a name (up to 300 characters) and a short name (up to 50)")
    if not phenos:
        raise HTTPException(422, "Add at least one phenotype")
    if body.metadata_preset not in METADATA_PRESETS:
        raise HTTPException(422, "metadata_preset must be default or all")
    tpl = services.templates().get(body.template)
    if not tpl:
        raise HTTPException(422, f"Unknown pipeline template {body.template}")
    preset = METADATA_PRESETS[body.metadata_preset]
    p = Project(id=pid, name=name, short=short, description=body.description.strip(), phenotypes=phenos,
                phenotype_types=phenotype_types(phenos, body.phenotype_types), metadata_config=preset() if preset else None,
                meta_defaults={"method": tpl["meta_method"]} if tpl["meta_method"] else {})
    db.add(p)
    db.flush()
    db.add(Membership(user_id=user.id, project_id=pid, role="reviewer"))
    services.ensure_project_pipeline(db, p, user.id, body.template)
    db.commit()
    return {"id": pid}


class ProjectDelete(BaseModel):
    confirm: str  # the project's name, typed out


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, body: ProjectDelete, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Platform admins only: delete a project and everything in it (datasets, QC runs, meta-analyses, cohorts,
    pipelines, project resources, members) and all its files in storage. The project's name must be given exactly.
    On AWS the buckets are versioned: deleted files stay recoverable until the lifecycle rule expires them."""
    if not user.is_admin:
        raise HTTPException(403, "Only platform admins can delete projects")
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    if body.confirm != p.name:
        raise HTTPException(422, "Type the project's name exactly to delete it")
    datasets = db.scalars(select(Dataset).where(Dataset.project_id == project_id)).all()
    metas = db.scalars(select(MetaAnalysis).where(MetaAnalysis.project_id == project_id)).all()
    busy = sum(ds.status == "processing" for ds in datasets) + sum(ma.status in ("queued", "running") for ma in metas)
    if busy:
        raise HTTPException(409, f"Jobs still running in this project: {busy} (QC runs and meta-analyses). Delete it when they have finished.")
    resources = db.scalars(select(Resource).where(Resource.project_id == project_id)).all()
    # Every folder the records point at, then the project's own prefixes in each area (catches anything left over).
    folders = [storage.parent(ds.storage_path) for ds in datasets if ds.storage_path]
    folders += [r.run_dir for ds in datasets for r in ds.runs] + [ma.run_dir for ma in metas]
    folders += [storage.parent(r.storage_path) for r in resources if r.storage_path]
    folders += [storage.location("uploads", f"uploads/{project_id}"), storage.location("results", f"qc/{project_id}"),
                storage.location("results", f"meta/{project_id}"), storage.location("resources", f"resources/{project_id}")]
    for ds in datasets:
        if ds.status == "uploading" and ds.upload_id:
            storage.abort_multipart(ds.storage_path, ds.upload_id)
    counts = {"datasets": len(datasets), "meta-analyses": len(metas), "resources": len(resources)}
    for ds in datasets:
        db.delete(ds)  # and its QC runs
    for rows in (metas, resources):
        for r in rows:
            db.delete(r)
    db.flush()
    for model in (Cohort, PipelineVersion, Pipeline, ApiToken):
        for r in db.scalars(select(model).where(model.project_id == project_id)):
            db.delete(r)
        db.flush()
    db.delete(p)  # and its memberships
    db.commit()
    log.info("Project %s (%s) deleted by %s: %s", project_id, p.name, user.email, counts)
    for loc in dict.fromkeys(f for f in folders if f):
        storage.delete_prefix(loc)


# ---------- the Practice project's files ----------

@router.get("/practice/files")
def practice_files(user: User = Depends(current_user)):
    """The synthetic files for the Practice project, with the details to enter for each."""
    return practice.files()


@router.get("/practice/files/{name}")
def practice_file(name: str, user: User = Depends(current_user)):
    path = practice.file_path(name)
    if not path:
        raise HTTPException(404, "No such practice file")
    return FileResponse(path, filename=name, media_type="application/gzip")
