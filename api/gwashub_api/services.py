"""Glue between the database and the runner: catalogs, pipeline versions, resources, run dirs."""
import os

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gwashub_api.config import settings
from gwashub_api.models import PipelineVersion, Project, Resource
from pipeline.runner.catalog import StepDef, load_images, load_steps
from pipeline.runner.pipeline import entries_from_dicts, plan_groups, resolve


def default_template():
    with open(os.path.join(settings().qc_steps_dir, "templates", "default.yaml")) as f:
        return yaml.safe_load(f)["steps"]


def catalog() -> dict:
    """Every step in pipeline/qc_steps (all owners); all projects can use all of them."""
    return load_steps(settings().qc_steps_dir)


def images() -> dict:
    return load_images(settings().images_dir)


def cache_dir():
    d = os.path.join(settings().data_dir, "cache")
    os.makedirs(d, exist_ok=True)
    return d


def step_info(sd: StepDef) -> dict:
    return {"key": sd.key, "owner": sd.owner, "title": sd.title, "description": sd.description, "image": sd.image,
            "on_fail": sd.default_on_fail, "resources": sd.resources, "params": sd.params,
            "source": "default" if sd.owner == "gwashub" else "consortium", "script": sd.source.get("path"),
            "command": " ".join(sd.command)}


def current_pipeline(db: Session, project_id: str) -> PipelineVersion | None:
    return db.scalar(select(PipelineVersion).where(PipelineVersion.project_id == project_id)
                     .order_by(PipelineVersion.version.desc()).limit(1))


def latest_resources(db: Session, project_id: str) -> dict:
    """key -> Resource (latest version), project resources shadow shared ones of the same key."""
    out = {}
    q = select(Resource).where((Resource.project_id.is_(None)) | (Resource.project_id == project_id)).order_by(Resource.version)
    for r in db.scalars(q):
        if r.project_id is None and r.key in out and out[r.key].project_id is not None:
            continue
        out[r.key] = r
    return out


def save_pipeline(db: Session, project_id: str, steps: list, note: str, user_id: int | None) -> PipelineVersion:
    resolved = resolve(entries_from_dicts(steps), catalog(), images=images())  # validates keys, on_fail and images
    res = latest_resources(db, project_id)
    pinned = {}
    for e in resolved:
        for key in e.step.resources:
            if key in res:
                pinned[key] = res[key].id
    last = db.scalar(select(func.max(PipelineVersion.version)).where(PipelineVersion.project_id == project_id)) or 0
    pv = PipelineVersion(project_id=project_id, version=last + 1, steps=steps, note=note, created_by=user_id,
                         resolved=[{"step": e.step.key, "image": e.image, "on_fail": e.on_fail, "source": e.step.source}
                                   for e in resolved] + [{"resources": pinned}, {"groups": plan_groups(resolved)}])
    db.add(pv)
    db.flush()
    return pv


def pinned_resources(pv: PipelineVersion) -> dict:
    for item in pv.resolved or []:
        if "resources" in item:
            return item["resources"]
    return {}


def resource_locations(db: Session, pv: PipelineVersion) -> dict:
    """key -> storage location, using the resource versions pinned by this pipeline version
    (falling back to the latest version for anything added later)."""
    out = {}
    pinned = pinned_resources(pv)
    for key, r in latest_resources(db, pv.project_id).items():
        rid = pinned.get(key)
        res = db.get(Resource, rid) if rid else r
        out[key] = res.storage_path
    return out


def step_titles() -> dict:
    return {k: sd.title for k, sd in catalog().items()}


def ensure_project_pipeline(db: Session, project: Project, user_id: int | None = None):
    if not current_pipeline(db, project.id):
        save_pipeline(db, project.id, default_template(), "Created from the default template", user_id)

