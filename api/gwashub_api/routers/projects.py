from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import re

from gwashub_api import services
from gwashub_api.accounts import InviteError, find_or_invite
from gwashub_api.auth import current_user, membership, require_member
from gwashub_api.db import get_db
from gwashub_api.models import Dataset, Membership, Project, User
from gwashub_api.services import current_pipeline

router = APIRouter(prefix="/api", tags=["projects"])
STATUSES = ("passed", "review", "failed", "processing")


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
    out = []
    for m in db.scalars(select(Membership).where(Membership.user_id == user.id)):
        p = m.project
        out.append({"id": p.id, "name": p.name, "short": p.short, "description": p.description, "role": m.role,
                    "members": len(p.members), "counts": counts(db, p.id)})
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
        "ancestries": p.ancestries, "sexes": p.sexes,
        "created_at": p.created_at, "role": m.role, "counts": counts(db, p.id),
        "members": [{"id": x.user.id, "name": x.user.name, "institution": x.user.institution, "role": x.role,
                     "is_me": x.user_id == user.id, "uploads": uploads.get(x.user_id, 0),
                     **({"email": x.user.email} if m.role == "reviewer" else {})}  # reviewers manage members
                    for x in sorted(p.members, key=lambda x: x.user.name.lower())],
        "pipeline": pv and {"version": pv.version, "steps": len(pv.steps), "created_at": pv.created_at,
                            "consortium_steps": sum(1 for s in pv.steps if not s["step"].startswith("gwashub/"))},
    }


ALL_ANCESTRIES = ["EUR", "AFR", "EAS", "SAS", "AMR", "Multi"]
ALL_SEXES = ["Combined", "Female", "Male"]


class UploadOptions(BaseModel):
    phenotypes: list[str]
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
    p.phenotypes = phenos
    p.ancestries = [a for a in ALL_ANCESTRIES if a in body.ancestries]
    p.sexes = [s for s in ALL_SEXES if s in body.sexes]
    db.commit()
    in_use = {ph for (ph,) in db.execute(select(Dataset.phenotype).where(Dataset.project_id == project_id))}
    return {"phenotypes": p.phenotypes, "ancestries": p.ancestries, "sexes": p.sexes,
            "removed_but_in_use": sorted(in_use - set(phenos))}


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
    m = db.scalar(select(Membership).where(Membership.project_id == project_id, Membership.user_id == user_id))
    if not m:
        raise HTTPException(404, "Not a member of this project")
    if m.role == "reviewer" and _reviewer_count(db, project_id) == 1:
        raise HTTPException(409, "A project needs at least one reviewer")
    db.delete(m)
    db.commit()


# ---------- projects (platform admins create them) ----------

class ProjectIn(BaseModel):
    id: str
    name: str
    short: str
    description: str = ""
    phenotypes: list[str]


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
    p = Project(id=pid, name=name, short=short, description=body.description.strip(), phenotypes=phenos)
    db.add(p)
    db.flush()
    db.add(Membership(user_id=user.id, project_id=pid, role="reviewer"))
    services.ensure_project_pipeline(db, p, user.id)
    db.commit()
    return {"id": pid}
