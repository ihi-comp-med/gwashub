"""Glue between the database and the runner: catalogs, pipeline versions, resources, run dirs."""
import os
import re

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gwashub_api.config import settings
from gwashub_api import metadata_schema
from gwashub_api.models import Cohort, Dataset, PipelineVersion, Project, Resource
from pipeline.runner import params as P
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


def step_info(sd: StepDef) -> dict:
    return {"key": sd.key, "owner": sd.owner, "title": sd.title, "description": sd.description, "image": sd.image,
            "on_fail": sd.default_on_fail, "resources": sd.resources, "optional_resources": sd.optional_resources, "params": sd.params,
            "param_schema": sd.param_schema, "source": "default" if sd.owner == "gwashub" else "consortium", "script": sd.source.get("path"),
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
    steps = [dict(s) for s in steps]
    for e, raw in zip(resolved, steps):  # typed step params (step.yaml); ValueError like resolve()
        if raw.get("params"):
            checked = P.validate(raw["params"], e.step.param_schema, e.step.title)
            raw["params"] = {k: checked[k] for k in raw["params"]}
    res = latest_resources(db, project_id)
    pinned = {}
    for e in resolved:
        for key in e.step.all_resources:
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



# ---------- cohorts ----------

def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def find_cohort(db: Session, project_id: str, name: str) -> Cohort | None:
    """Same project, same name ignoring case and spacing ("UK Biobank" = "uk-biobank")."""
    key = norm_name(name)
    return next((c for c in db.scalars(select(Cohort).where(Cohort.project_id == project_id)) if norm_name(c.name) == key), None)


def get_or_create_cohort(db: Session, project_id: str, name: str, user_id: int | None, meta: dict | None = None) -> tuple[Cohort, bool]:
    name = " ".join(name.split())
    c = find_cohort(db, project_id, name)
    if c:
        return c, False
    c = Cohort(project_id=project_id, name=name, meta=metadata_schema.split(meta or {})[0], created_by=user_id)
    db.add(c)
    db.flush()
    return c, True


def dataset_overrides(cohort: Cohort | None, meta: dict) -> dict:
    """What a dataset stores: its dataset-level fields, plus cohort-level fields only where they differ
    from the cohort's value (overrides)."""
    cohort_part, dataset_part = metadata_schema.split(meta)
    base = (cohort.meta if cohort else {}) or {}
    return {**dataset_part, **{k: v for k, v in cohort_part.items() if base.get(k) != v}}


def link_cohorts(db: Session):
    """Give every dataset a Cohort record (datasets from before cohorts existed only had a name). The new
    cohort takes the cohort-level metadata of its most recent dataset; datasets keep only real overrides."""
    todo = db.scalars(select(Dataset).where(Dataset.cohort_id.is_(None)).order_by(Dataset.id)).all()
    if not todo:
        return
    for ds in reversed(todo):  # newest first, so a new cohort is seeded from the latest upload
        c, _ = get_or_create_cohort(db, ds.project_id, ds.cohort, ds.uploaded_by, ds.study_meta)
        ds.cohort_id, ds.cohort = c.id, c.name
    db.flush()
    for ds in todo:
        ds.study_meta = dataset_overrides(db.get(Cohort, ds.cohort_id), ds.study_meta or {})
    db.commit()


def metadata_config(project: Project) -> dict:
    return metadata_schema.normalise_config(project.metadata_config)
