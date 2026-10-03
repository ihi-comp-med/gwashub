import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import jobs, storage
from gwashub_api.auth import current_user, require_member
from gwashub_api.db import get_db
from gwashub_api.models import Dataset, MetaAnalysis, Project, User

router = APIRouter(prefix="/api", tags=["meta-analysis"])

DEFAULT_SETTINGS = {
    "method": "fixed", "min_studies": 2, "maf_threshold": 0.01, "drop_palindromic": False,
    "genomic_control": False, "gws": 5e-8, "clump_kb": 500, "het_p_threshold": 1e-3,
    "include_statuses": ["passed"],  # which QC statuses are eligible by default
}
PUBLIC_FILES = {"manhattan.png", "qq.png", "plot_data.json", "forest.json", "lead_loci.tsv", "summary.json"}
REVIEWER_FILES = {"meta_results.tsv.gz"}


def project_settings(p: Project) -> dict:
    return {**DEFAULT_SETTINGS, **(p.meta_defaults or {})}


@router.get("/projects/{project_id}/meta/settings")
def get_settings(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    return {"settings": project_settings(m.project), "defaults": DEFAULT_SETTINGS}


@router.put("/projects/{project_id}/meta/settings")
def put_settings(project_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id, role="reviewer")
    clean = validate_settings({**project_settings(m.project), **body})
    m.project.meta_defaults = clean
    db.commit()
    return {"settings": clean}


def validate_settings(s: dict) -> dict:
    s = {k: v for k, v in s.items() if k in DEFAULT_SETTINGS}
    if s.get("method") not in ("fixed", "random"):
        raise HTTPException(422, "method must be fixed or random")
    if not (isinstance(s.get("min_studies"), int) and s["min_studies"] >= 1):
        raise HTTPException(422, "min_studies must be a positive integer")
    if not (0 <= float(s.get("maf_threshold", 0)) < 0.5):
        raise HTTPException(422, "maf_threshold must be in [0, 0.5)")
    if not (0 < float(s.get("gws", 5e-8)) < 1):
        raise HTTPException(422, "gws must be in (0, 1)")
    if not set(s.get("include_statuses", [])) <= {"passed", "review"}:
        raise HTTPException(422, "include_statuses may contain passed and review")
    return s


@router.get("/projects/{project_id}/meta/candidates")
def candidates(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Datasets with a completed QC run (only those can be meta-analysed)."""
    require_member(db, user, project_id)
    out = []
    for ds in db.scalars(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.phenotype, Dataset.ancestry, Dataset.cohort)):
        run = jobs.latest_completed_run(ds)
        stats = next((s for s in (run.result or {}).get("steps", []) if s["key"] == "gwashub/stats_summary"), {}) if run else {}
        out.append({"id": ds.id, "cohort": ds.cohort, "phenotype": ds.phenotype, "ancestry": ds.ancestry, "sex": ds.sex,
                    "status": ds.status, "n_cases": ds.n_cases, "n_controls": ds.n_controls, "eligible": run is not None,
                    "rows": ((run.result or {}).get("outputs") or {}).get("rows") if run else None,
                    "lambda_gc": (stats.get("metrics") or {}).get("lambda_gc")})
    return out


class MetaIn(BaseModel):
    name: str
    dataset_ids: list[int]
    settings: dict = {}


def meta_view(db, ma: MetaAnalysis, detail=False):
    v = {"id": ma.id, "name": ma.name, "status": ma.status, "n_datasets": len(ma.dataset_ids), "dataset_ids": ma.dataset_ids,
         "settings": ma.settings, "created_by": db.get(User, ma.created_by).name, "created_at": ma.created_at,
         "finished_at": ma.finished_at, "error": ma.error, "lambda_gc": ma.lambda_gc,
         "summary": {k: v for k, v in ((ma.result or {}).get("summary") or {}).items() if k != "settings"}}
    if detail and ma.status == "completed":
        lead = storage.read_bytes(storage.join(ma.run_dir, "lead_loci.tsv"))
        v["lead_loci"] = pd.read_csv(io.BytesIO(lead), sep="\t", dtype={"chr": str}).fillna("").to_dict("records")
        v["forest"] = json.loads(storage.read_bytes(storage.join(ma.run_dir, "forest.json")))
        v["plot_data"] = json.loads(storage.read_bytes(storage.join(ma.run_dir, "plot_data.json")))
    if detail:
        v["datasets"] = [{"id": d.id, "cohort": d.cohort, "phenotype": d.phenotype, "ancestry": d.ancestry, "sex": d.sex,
                          "status": d.status, "n_cases": d.n_cases, "n_controls": d.n_controls}
                         for d in (db.get(Dataset, i) for i in ma.dataset_ids) if d]
    return v


@router.get("/projects/{project_id}/meta")
def list_meta(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, project_id)
    q = select(MetaAnalysis).where(MetaAnalysis.project_id == project_id).order_by(MetaAnalysis.created_at.desc())
    return [meta_view(db, ma) for ma in db.scalars(q)]


@router.post("/projects/{project_id}/meta", status_code=201)
def create_meta(project_id: str, body: MetaIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id, role="reviewer")
    settings = validate_settings({**project_settings(m.project), **body.settings})
    ids = list(dict.fromkeys(body.dataset_ids))
    if len(ids) < 2:
        raise HTTPException(422, "Choose at least two datasets")
    for i in ids:
        ds = db.get(Dataset, i)
        if not ds or ds.project_id != project_id:
            raise HTTPException(422, f"Dataset {i} is not in this project")
        if not jobs.latest_completed_run(ds):
            raise HTTPException(422, f"{ds.cohort} ({ds.ancestry}, {ds.sex}) has no completed QC run")
        if ds.status not in settings["include_statuses"]:
            raise HTTPException(422, f"{ds.cohort} ({ds.ancestry}, {ds.sex}) has status '{ds.status}', which these settings exclude")
    ma = MetaAnalysis(project_id=project_id, name=body.name.strip() or "Meta-analysis", settings=settings, dataset_ids=ids,
                      created_by=user.id, status="queued")
    db.add(ma)
    db.commit()
    jobs.start_meta(ma.id)
    return meta_view(db, ma)


def require_meta(db, user, meta_id) -> tuple[MetaAnalysis, object]:
    ma = db.get(MetaAnalysis, meta_id)
    if not ma:
        raise HTTPException(404, "Not found")
    return ma, require_member(db, user, ma.project_id)


@router.get("/meta/{meta_id}")
def get_meta(meta_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ma, _ = require_meta(db, user, meta_id)
    return meta_view(db, ma, detail=True)


@router.get("/meta/{meta_id}/files/{name}")
def meta_file(meta_id: int, name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ma, m = require_meta(db, user, meta_id)
    if name in REVIEWER_FILES and m.role != "reviewer":
        raise HTTPException(403, "Only reviewers can download full meta-analysis results")
    if name not in PUBLIC_FILES | REVIEWER_FILES or ma.status != "completed":
        raise HTTPException(404, "No such file")
    from gwashub_api.routers.datasets import send_file
    return send_file(storage.join(ma.run_dir, name), f"meta_{ma.id}_{name}")


@router.delete("/meta/{meta_id}", status_code=204)
def delete_meta(meta_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ma, m = require_meta(db, user, meta_id)
    if m.role != "reviewer":
        raise HTTPException(403, "Only reviewers can delete meta-analyses")
    if ma.status in ("queued", "running"):
        raise HTTPException(409, "Still running")
    folder = ma.run_dir
    db.delete(ma)
    db.commit()
    storage.delete_prefix(folder)
