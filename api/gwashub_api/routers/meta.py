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
from pipeline.runner import params as P
from pipeline.runner.catalog import load_analyses

router = APIRouter(prefix="/api", tags=["meta-analysis"])

DEFAULT_METHOD = "gwashub/ivw"
# Settings every method shares: GWASHub's post-processing (lead loci, heterogeneity flag) uses them.
COMMON_PARAMS = P.normalise([
    {"key": "gws", "label": "Genome-wide significance threshold", "type": "number", "min": 1e-12, "max": 0.05, "default": 5e-8},
    {"key": "clump_kb", "label": "Lead-locus window (kb)", "type": "integer", "min": 10, "max": 10000, "default": 500,
     "help": "Significant variants within this distance of a stronger one belong to the same locus"},
    {"key": "het_p_threshold", "label": "Heterogeneity flag (P)", "type": "number", "min": 0, "max": 1, "default": 1e-3,
     "help": "Lead loci with Cochran's Q P below this are flagged as heterogeneous"},
])
STATUSES = ("passed", "review")
PUBLIC_FILES = {"manhattan.png", "qq.png", "plot_data.json", "forest.json", "lead_loci.tsv", "summary.json", "datasets.tsv", "README.txt"}
REVIEWER_FILES = {"meta_results.tsv.gz"}


def methods() -> dict:
    return load_analyses()


def method_view(md) -> dict:
    return {"key": md.key, "owner": md.owner, "title": md.title, "description": md.description, "reference": md.reference,
            "image": md.image, "params": md.param_schema, "script": md.source.get("path")}


def normalise(raw: dict | None) -> dict:
    """Settings as {method, params, common, include_statuses}. Meta-analyses and project defaults saved before
    there were several methods have flat settings ({"method": "fixed", "maf_threshold": ...}); read them as
    gwashub/ivw."""
    raw = dict(raw or {})
    if "params" not in raw and "common" not in raw and raw.get("method") in (None, "fixed", "random"):
        params = {"model": raw.get("method", "fixed"), "min_studies": raw.get("min_studies", 2), "maf_min": raw.get("maf_threshold", 0.01),
                  "genomic_control": raw.get("genomic_control", False), "drop_palindromic": raw.get("drop_palindromic", False)}
        common = {k: raw[k] for k in ("gws", "clump_kb", "het_p_threshold") if k in raw}
        raw = {"method": DEFAULT_METHOD, "params": {DEFAULT_METHOD: params} if raw else {}, "common": common,
               "include_statuses": raw.get("include_statuses", ["passed"])}
    method = raw.get("method") or DEFAULT_METHOD
    params = raw.get("params") or {}
    if params and not all(isinstance(v, dict) for v in params.values()):  # one method's params, not per method
        params = {method: params}
    return {"method": method, "params": params, "common": raw.get("common") or {},
            "include_statuses": raw.get("include_statuses") or ["passed"]}


def resolve_settings(raw: dict, catalogue: dict) -> dict:
    """Checked settings for one run: the chosen method's params (defaults filled in) and the common ones."""
    s = normalise(raw)
    md = catalogue.get(s["method"])
    if not md:
        raise HTTPException(422, f"Unknown meta-analysis method {s['method']}")
    try:
        params = P.validate(s["params"].get(s["method"]) or {}, md.param_schema, md.title)
        common = P.validate(s["common"], COMMON_PARAMS, "Results")
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not s["include_statuses"] or not set(s["include_statuses"]) <= set(STATUSES):
        raise HTTPException(422, "Eligible datasets: choose passed and/or review")
    return {"method": s["method"], "params": params, "common": common, "include_statuses": s["include_statuses"]}


def project_settings(p: Project) -> dict:
    return normalise(p.meta_defaults)


@router.get("/meta/methods")
def list_methods(user: User = Depends(current_user)):
    """Every meta-analysis method (pipeline/analyses/meta_analysis) with its typed parameters; all projects can use all."""
    ms = sorted(methods().values(), key=lambda m: (m.key != DEFAULT_METHOD, m.owner != "gwashub", m.key))
    return {"methods": [method_view(m) for m in ms], "common": COMMON_PARAMS, "default_method": DEFAULT_METHOD}


@router.get("/projects/{project_id}/meta/settings")
def get_settings(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    return {"settings": project_settings(m.project)}


@router.put("/projects/{project_id}/meta/settings")
def put_settings(project_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewers set the project's defaults: the default method, saved parameters per method, the common
    settings and which QC statuses are eligible."""
    m = require_member(db, user, project_id, role="reviewer")
    s = normalise({**project_settings(m.project), **body})
    catalogue = methods()
    checked = {}
    for key, values in s["params"].items():
        if key in catalogue:
            checked[key] = resolve_settings({"method": key, "params": {key: values}, "common": s["common"],
                                             "include_statuses": s["include_statuses"]}, catalogue)["params"]
    base = resolve_settings(s, catalogue)
    clean = {"method": base["method"], "params": {**checked, base["method"]: base["params"]}, "common": base["common"],
             "include_statuses": base["include_statuses"]}
    m.project.meta_defaults = clean
    db.commit()
    return {"settings": clean}


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
    settings: dict = {}  # {method, params: {...for that method}, common, include_statuses}; missing parts from the project defaults


def meta_view(db, ma: MetaAnalysis, detail=False):
    settings = normalise(ma.settings)
    md = methods().get(settings["method"])
    v = {"id": ma.id, "name": ma.name, "status": ma.status, "n_datasets": len(ma.dataset_ids), "dataset_ids": ma.dataset_ids,
         "settings": {**settings, "params": settings["params"].get(settings["method"], {})},
         "method": method_view(md) if md else {"key": settings["method"], "title": settings["method"], "params": []},
         "common_params": COMMON_PARAMS,
         "created_by": db.get(User, ma.created_by).name, "created_at": ma.created_at,
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
    defaults = project_settings(m.project)
    given = normalise(body.settings) if body.settings else {}
    method = (given or {}).get("method") or defaults["method"]
    params = {**defaults["params"].get(method, {}), **((given or {}).get("params", {}).get(method, {}))}
    settings = resolve_settings({"method": method, "params": {method: params},
                                 "common": {**defaults["common"], **(given.get("common") if given else {})},
                                 "include_statuses": (given.get("include_statuses") if body.settings and "include_statuses" in body.settings else None)
                                 or defaults["include_statuses"]}, methods())
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
                      method=settings["method"], created_by=user.id, status="queued")
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
