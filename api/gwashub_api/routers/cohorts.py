"""Cohorts: the studies contributing datasets to a project, each with cohort-level metadata that its
datasets inherit (and may override). Cohorts belong to one project; projects are isolated."""
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gwashub_api import metadata_schema, services
from gwashub_api.auth import current_user, require_member
from gwashub_api.db import get_db
from gwashub_api.models import Cohort, Dataset, User

router = APIRouter(prefix="/api", tags=["cohorts"])


def cohort_view(db, c: Cohort, cfg: dict, can_edit: bool, n_datasets: int) -> dict:
    creator = db.get(User, c.created_by) if c.created_by else None
    cohort_cfg = {"shown": [k for k in cfg["shown"] if metadata_schema.UNITS[k]["level"] == "cohort"],
                  "required": [k for k in cfg["required"] if metadata_schema.UNITS[k]["level"] == "cohort"]}
    return {"id": c.id, "name": c.name, "meta": c.meta or {}, "datasets": n_datasets, "created_by": creator.name if creator else None,
            "created_at": c.created_at, "can_edit": can_edit, "metadata_complete": metadata_schema.completeness(c.meta, cohort_cfg)}


def can_edit_cohort(m, user: User, c: Cohort) -> bool:
    return m.role == "reviewer" or c.created_by == user.id


@router.get("/projects/{project_id}/cohorts")
def list_cohorts(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    cfg = services.metadata_config(m.project)
    counts = dict(db.execute(select(Dataset.cohort_id, func.count()).where(Dataset.project_id == project_id)
                             .group_by(Dataset.cohort_id)).all())
    cohorts = db.scalars(select(Cohort).where(Cohort.project_id == project_id)).all()
    return [cohort_view(db, c, cfg, can_edit_cohort(m, user, c), counts.get(c.id, 0))
            for c in sorted(cohorts, key=lambda c: c.name.lower())]


@router.get("/projects/{project_id}/cohorts/similar")
def similar_cohorts(project_id: str, name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Existing cohorts with a name like this one, to warn before creating a near-duplicate."""
    require_member(db, user, project_id)
    key = services.norm_name(name)
    if len(key) < 2:
        return []
    out = []
    for c in db.scalars(select(Cohort).where(Cohort.project_id == project_id)):
        other = services.norm_name(c.name)
        if other == key or key in other or other in key or SequenceMatcher(None, key, other).ratio() >= 0.75:
            out.append({"id": c.id, "name": c.name, "exact": other == key})
    return out


class CohortIn(BaseModel):
    name: str
    meta: dict = {}


class CohortEdit(BaseModel):
    name: str | None = None
    meta: dict | None = None  # full replacement of the cohort-level metadata


@router.post("/projects/{project_id}/cohorts", status_code=201)
def create_cohort(project_id: str, body: CohortIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Any project member can add a cohort (the website warns about similar names first)."""
    m = require_member(db, user, project_id)
    name = " ".join(body.name.split())
    if not name or len(name) > 200:
        raise HTTPException(422, "Give a cohort name (up to 200 characters)")
    if services.find_cohort(db, project_id, name):
        raise HTTPException(409, f"There's already a cohort called {name} in this project")
    meta = metadata_schema.validate(body.meta)
    c, _ = services.get_or_create_cohort(db, project_id, name, user.id, meta)
    db.commit()
    return cohort_view(db, c, services.metadata_config(m.project), True, 0)


@router.patch("/cohorts/{cohort_id}")
def edit_cohort(cohort_id: int, body: CohortEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The cohort's creator or a reviewer can rename it or change its metadata; its datasets see the change."""
    c = db.get(Cohort, cohort_id)
    if not c:
        raise HTTPException(404, "Cohort not found")
    m = require_member(db, user, c.project_id)
    if not can_edit_cohort(m, user, c):
        raise HTTPException(403, "Only the person who added this cohort or a reviewer can change it")
    if body.name is not None:
        name = " ".join(body.name.split())
        if not name or len(name) > 200:
            raise HTTPException(422, "Give a cohort name (up to 200 characters)")
        other = services.find_cohort(db, c.project_id, name)
        if other and other.id != c.id:
            raise HTTPException(409, f"There's already a cohort called {other.name} in this project")
        c.name = name
        for ds in db.scalars(select(Dataset).where(Dataset.cohort_id == c.id)):
            ds.cohort = name
    if body.meta is not None:
        meta = metadata_schema.validate(body.meta)
        dataset_level = [metadata_schema.UNITS[k]["label"] for k in meta if k not in metadata_schema.COHORT_KEYS
                         and k in metadata_schema.UNITS]
        if dataset_level:
            raise HTTPException(422, f"These are set per dataset, not per cohort: {', '.join(dataset_level)}")
        c.meta = metadata_schema.split(meta)[0]
    db.commit()
    count = db.scalar(select(func.count()).where(Dataset.cohort_id == c.id))
    return cohort_view(db, c, services.metadata_config(m.project), True, count)
