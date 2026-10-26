import io
import json
import re

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import jobs, services, storage
from gwashub_api.auth import current_user, require_member
from gwashub_api.db import get_db
from gwashub_api.models import Dataset, MetaAnalysis, Project, QCRun, User
from gwashub_api.routers.datasets import qc_metrics
from pipeline.runner import params as P
from pipeline.runner.catalog import load_analyses

router = APIRouter(prefix="/api", tags=["meta-analysis"])

DEFAULT_METHOD = "gwashub/gwama"
# Settings every method shares: GWASHub's post-processing (lead loci by LD clumping, _metalib/loci.py; the
# heterogeneity flag) uses them. The defaults are those of the HERMES 3 analyses (define_loci_plink.R).
COMMON_PARAMS = P.normalise([
    {"key": "gws", "label": "Genome-wide significance threshold", "type": "number", "min": 1e-12, "max": 0.05, "default": 5e-8},
    {"key": "ld_panel", "label": "LD reference panel", "type": "select", "options": ["EUR", "AFR", "AMR", "EAS", "SAS"], "default": "EUR",
     "help": "1000 Genomes super-population whose LD clumps the lead loci; the others are tried for leads with no LD partner in it"},
    {"key": "iss_r2", "label": "Independent signals: r² below", "type": "number", "min": 0, "max": 1, "default": 0.6,
     "help": "Genome-wide significant variants are clumped at this r² into independent significant SNPs"},
    {"key": "lead_r2", "label": "Lead SNPs: r² below", "type": "number", "min": 0, "max": 1, "default": 0.1,
     "help": "Independent significant SNPs are clumped again at this r² into lead SNPs"},
    {"key": "locus_p", "label": "LD cloud: P below", "type": "number", "min": 0, "max": 1, "default": 0.05,
     "help": "Variants with P below this and in LD with a lead (next two settings) set the locus bounds"},
    {"key": "boundary_r2", "label": "LD cloud: r² at least", "type": "number", "min": 0, "max": 1, "default": 0.1},
    {"key": "boundary_kb", "label": "LD cloud: within (kb)", "type": "integer", "min": 10, "max": 10000, "default": 500,
     "help": "Also the window between lead loci when no LD panel has been uploaded"},
    {"key": "merge_kb", "label": "Merge loci within (kb)", "type": "integer", "min": 0, "max": 10000, "default": 250},
    {"key": "min_locus_kb", "label": "Locus with no LD cloud: ± (kb)", "type": "integer", "min": 0, "max": 10000, "default": 250},
    {"key": "min_maf", "label": "Lead loci: minimum MAF", "type": "number", "min": 0, "max": 0.5, "default": 0.01,
     "help": "Only variants with this minor allele frequency or more are considered for lead loci"},
    {"key": "min_studies", "label": "Lead loci: minimum datasets", "type": "integer", "min": 1, "default": 2,
     "help": "Only variants in at least this many datasets are considered for lead loci"},
    {"key": "het_p_threshold", "label": "Heterogeneity flag (P)", "type": "number", "min": 0, "max": 1, "default": 1e-3,
     "help": "Lead loci with Cochran's Q P below this are flagged as heterogeneous"},
])
STATUSES = ("passed", "review")
# The files reviewers can download (uploaders none: they see the results on the page). method_output.txt, the method's
# console, is kept whether it failed or not.
FILES = {"manhattan.png", "qq.png", "plot_data.json", "forest.json", "lead_loci.tsv", "signals.tsv", "summary.json", "datasets.tsv",
         "README.txt", "meta_results.tsv.gz", "method_output.txt"}


def methods() -> dict:
    return load_analyses()


def method_files(ma) -> list:
    """Files the method itself wrote for download (e.g. GWAMA's and METAL's logs), from its log: [{path, label}]."""
    files = ((ma.result or {}).get("log") or {}).get("files") or []
    return [f for f in files if isinstance(f, dict) and "/" not in str(f.get("path", "")) and f.get("path")]


def method_view(md) -> dict:
    return {"key": md.key, "owner": md.owner, "title": md.title, "description": md.description, "reference": md.reference,
            "image": md.image, "params": md.param_schema, "script": md.source.get("path")}


def normalise(raw: dict | None) -> dict:
    """Settings as {method, params, common, include_statuses}."""
    raw = dict(raw or {})
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
                    "status": ds.status, "n_cases": ds.n_cases, "n_controls": ds.n_controls, "n_total": ds.n_total,
                    "trait_type": services.trait_type(ds.project, ds.phenotype), "eligible": run is not None,
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
         "finished_at": ma.finished_at, "error": ma.error, "lambda_gc": ma.lambda_gc, "location": ma.run_dir,
         "batch_note": jobs.batch_note(ma.batch_state) if ma.status in ("queued", "running") else None,
         "live": jobs.live_meta(ma),
         "has_output": ma.status in ("completed", "failed") and bool(ma.run_dir) and storage.exists(storage.join(ma.run_dir, "method_output.txt")),
         "summary": {k: v for k, v in ((ma.result or {}).get("summary") or {}).items() if k != "settings"}}
    if detail and ma.status == "completed":
        lead = storage.read_bytes(storage.join(ma.run_dir, "lead_loci.tsv"))
        v["lead_loci"] = pd.read_csv(io.BytesIO(lead), sep="\t", dtype={"chr": str}).fillna("").to_dict("records")
        v["forest"] = json.loads(storage.read_bytes(storage.join(ma.run_dir, "forest.json")))
        v["plot_data"] = json.loads(storage.read_bytes(storage.join(ma.run_dir, "plot_data.json")))
        v["files"] = [n for n in ("README.txt", "datasets.tsv", "lead_loci.tsv", "signals.tsv", "meta_results.tsv.gz")
                      if storage.exists(storage.join(ma.run_dir, n))]
        v["method_files"] = method_files(ma)
    if detail:
        # Each dataset's QC as it went in: the run whose cleaned file was used (recorded by the post-processing).
        run_of = {st.get("id"): st.get("qc_run_id") for st in ((ma.result or {}).get("summary") or {}).get("studies") or []}
        v["datasets"] = []
        for d in (db.get(Dataset, i) for i in ma.dataset_ids):
            if not d:
                continue
            run = db.get(QCRun, run_of[d.id]) if run_of.get(d.id) else None
            warned = [s.get("title") or s.get("key") for s in ((run.result or {}).get("steps") or [])
                      if s.get("verdict") == "warn"] if run else []
            v["datasets"].append({"id": d.id, "cohort": d.cohort, "phenotype": d.phenotype, "ancestry": d.ancestry, "sex": d.sex,
                                  "status": d.status, "n_cases": d.n_cases, "n_controls": d.n_controls, "n_total": d.n_total,
                                  "trait_type": services.trait_type(d.project, d.phenotype),
                                  "ldsc_intercept": qc_metrics(run).get("ldsc_intercept") if run else None,
                                  "qc_warnings": warned})
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
    types = {services.trait_type(m.project, db.get(Dataset, i).phenotype) for i in ids}
    if len(types) > 1:
        raise HTTPException(422, "These datasets mix case/control and quantitative phenotypes; meta-analyse each kind separately")
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
    region = re.fullmatch(r"region_\d+\.json", name) is not None  # a locus plot's data (_metalib/regions.py)
    # Uploaders see the results on the page but download nothing; the page itself reads only the locus plots' data.
    if m.role != "reviewer" and not region:
        raise HTTPException(403, "Only reviewers can download meta-analysis files")
    available = ma.status == "completed" or (ma.status == "failed" and name == "method_output.txt")
    if not (region or name in FILES | {f["path"] for f in method_files(ma)}) or not available:
        raise HTTPException(404, "No such file")
    if region:  # small, and read by the page itself: returned directly rather than via a (cross-origin) S3 redirect
        loc = storage.join(ma.run_dir, name)
        if not storage.exists(loc):
            raise HTTPException(404, "No such file")
        return json.loads(storage.read_bytes(loc))
    from gwashub_api.routers.datasets import send_file
    return send_file(storage.join(ma.run_dir, name), f"meta_{ma.id}_{name}")


@router.post("/meta/{meta_id}/stop")
def stop_meta(meta_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Stop a queued or running meta-analysis (reviewers). It stays in the list as stopped, to delete or run again."""
    ma, m = require_meta(db, user, meta_id)
    if m.role != "reviewer":
        raise HTTPException(403, "Only reviewers can stop meta-analyses")
    if ma.status not in jobs.STOPPABLE:
        raise HTTPException(409, "This meta-analysis isn't running")
    jobs.stop_meta(ma, user.name)
    db.commit()
    return meta_view(db, ma)


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
