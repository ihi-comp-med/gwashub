"""Glue between the database and the runner: catalogs, pipeline versions, resources, run dirs."""
import os
import re

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gwashub_api.config import settings
from gwashub_api import metadata_schema
from gwashub_api.models import Cohort, Dataset, Pipeline, PipelineVersion, Project, Resource
from pipeline.runner import params as P
from pipeline.runner.catalog import StepDef, load_images, load_steps
from pipeline.runner.pipeline import entries_from_dicts, plan_groups, resolve


def templates() -> dict:
    """Pipeline templates (pipeline/qc_steps/templates/*.yaml), default first: {name: {name, title, description, steps,
    meta_method}}. A new project's first pipeline starts from one; meta_method (optional) is its default method."""
    folder = os.path.join(settings().qc_steps_dir, "templates")
    out = {}
    for fn in sorted(os.listdir(folder), key=lambda f: (f != "default.yaml", f)):
        if fn.endswith(".yaml"):
            with open(os.path.join(folder, fn)) as f:
                t = yaml.safe_load(f)
            out[t["name"]] = {"name": t["name"], "title": t.get("title", t["name"]), "description": " ".join(str(t.get("description", "")).split()),
                              "steps": t["steps"], "meta_method": t.get("meta_method")}
    return out


def default_template():
    return templates()["default"]["steps"]


def catalog() -> dict:
    """Every step in pipeline/qc_steps (all owners); all projects can use all of them."""
    return load_steps(settings().qc_steps_dir)


def images() -> dict:
    return load_images(settings().images_dir)


LANGUAGES = {"Rscript": "R", "python": "Python", "python3": "Python", "bash": "Bash", "sh": "Shell"}


def step_info(sd: StepDef) -> dict:
    return {"key": sd.key, "owner": sd.owner, "title": sd.title, "description": sd.description, "image": sd.image,
            "narrative": sd.narrative or sd.description, "interpreter": sd.command[0] if sd.command else "",
            "language": LANGUAGES.get(sd.command[0], sd.command[0]) if sd.command else "",
            "script_name": sd.command[-1] if sd.command else "",
            "on_fail": sd.default_on_fail, "resources": sd.resources, "optional_resources": sd.optional_resources, "params": sd.params,
            "param_schema": sd.param_schema, "source": "default" if sd.owner == "gwashub" else "consortium", "script": sd.source.get("path"),
            "command": " ".join(sd.command)}


def step_contract() -> dict:
    """What every step is given and must write (CONTRIBUTING.md), for the step details on the pipeline page."""
    from pipeline.qc_steps._lib.common import REQUIRED, STANDARD
    sample = [["1", "752566", "G", "A", "-0.0117", "0.0201", "0.561", "0.828", "418311", "7452", "410859", "rs3094315", "0.97"],
              ["1", "776546", "A", "G", "0.0086", "0.0212", "0.686", "0.189", "418311", "7452", "410859", "rs12124819", "0.95"],
              ["1", "832918", "T", "C", "0.0302", "0.0187", "0.106", "0.393", "418311", "7452", "410859", "rs28765502", "1"]]
    return {"standard_columns": STANDARD, "required_columns": REQUIRED, "input_sample": sample,
            "log_fields": [{"key": "step", "about": "the step's name"},
                           {"key": "verdict", "about": "pass, warn or fail"},
                           {"key": "metrics", "about": "numbers shown with the step and in the report (e.g. rows removed)"},
                           {"key": "messages", "about": "plain sentences saying what was found or done"},
                           {"key": "artifacts", "about": "files to show with the step: tables, plots, text ({type, path, label}, "
                                                         "optionally rows and description); removed rows go in removed_rows.tsv.gz with a reason column"}],
            "log_example": {"step": "filter", "verdict": "pass", "metrics": {"rows_in": 68963, "rows_out": 68910},
                            "messages": ["Removed 53 of 68,963 rows (0.08%)."],
                            "artifacts": [{"type": "table", "path": "filter_summary.tsv", "label": "Rows removed by each filter", "rows": 9},
                                          {"type": "table", "path": "removed_rows.tsv.gz", "label": "Removed rows", "rows": 53}]}}


# ---------- pipelines ----------
# A project has named pipelines, each with versions; one is the default. A dataset follows the default unless a reviewer
# pins it to another; a dataset is out of date when its latest run used another pipeline, or an older version.

def default_pipeline(db: Session, project_id: str) -> Pipeline | None:
    return db.scalar(select(Pipeline).where(Pipeline.project_id == project_id, Pipeline.is_default))


def current_version(db: Session, pipeline_id: int) -> PipelineVersion | None:
    return db.scalar(select(PipelineVersion).where(PipelineVersion.pipeline_id == pipeline_id)
                     .order_by(PipelineVersion.version.desc()).limit(1))


def current_pipeline(db: Session, project_id: str) -> PipelineVersion | None:
    """The current version of the project's default pipeline."""
    p = default_pipeline(db, project_id)
    return current_version(db, p.id) if p else None


def pipeline_of(db: Session, ds: Dataset) -> Pipeline | None:
    """The pipeline a dataset runs against: the one it is pinned to, else the project default."""
    return ds.pipeline or default_pipeline(db, ds.project_id)


def pipeline_state(db: Session, ds: Dataset, current: dict | None = None) -> dict:
    """Which pipeline a dataset follows, and whether its latest run used that pipeline's current version.
    current: {pipeline_id: current version} to avoid a query per dataset in lists."""
    p = pipeline_of(db, ds)
    if not p:
        return {"pipeline_id": None, "pipeline_name": None, "pinned": False, "current_version": None, "run_version": None, "out_of_date": False}
    cur_id = (current or {}).get(p.id) if current is not None else getattr(current_version(db, p.id), "id", None)
    run = ds.runs[-1] if ds.runs else None
    cur = db.get(PipelineVersion, cur_id) if cur_id else None
    return {"pipeline_id": p.id, "pipeline_name": p.name, "pinned": ds.pipeline_id is not None,
            "current_version": cur.version if cur else None, "run_version": run.pipeline_version.version if run else None,
            "run_pipeline": run.pipeline_version.pipeline.name if run and run.pipeline_version.pipeline else None,
            "out_of_date": bool(run and cur_id and run.pipeline_version_id != cur_id and ds.status not in ("processing", "uploading"))}


def current_versions(db: Session, project_id: str) -> dict:
    """{pipeline_id: id of its current version} for every pipeline of a project."""
    out = {}
    for pv in db.scalars(select(PipelineVersion).where(PipelineVersion.project_id == project_id).order_by(PipelineVersion.version)):
        if pv.pipeline_id:
            out[pv.pipeline_id] = pv.id
    return out


def ensure_pipelines(db: Session):
    """Give every project a default pipeline, and versions saved before pipelines existed to it (a local database
    from before the change; on Postgres the migration does the same)."""
    changed = False
    for project in db.scalars(select(Project)):
        p = default_pipeline(db, project.id)
        if not p:
            p = Pipeline(project_id=project.id, name="Default", is_default=True)
            db.add(p)
            db.flush()
            changed = True
        for pv in db.scalars(select(PipelineVersion).where(PipelineVersion.project_id == project.id, PipelineVersion.pipeline_id.is_(None))):
            pv.pipeline_id, changed = p.id, True
    if changed:
        db.commit()


def latest_resources(db: Session, project_id: str) -> dict:
    """key -> Resource (latest version), project resources shadow shared ones of the same key."""
    out = {}
    q = select(Resource).where((Resource.project_id.is_(None)) | (Resource.project_id == project_id)).order_by(Resource.version)
    for r in db.scalars(q):
        if r.project_id is None and r.key in out and out[r.key].project_id is not None:
            continue
        out[r.key] = r
    return out


def save_pipeline(db: Session, project_id: str, steps: list, note: str, user_id: int | None,
                  pipeline_id: int | None = None) -> PipelineVersion:
    """A new version of a pipeline (default: the project's default pipeline). Version numbers count across the project."""
    resolved = resolve(entries_from_dicts(steps), catalog(), images=images())  # validates keys, on_fail and images
    steps = [{k: v for k, v in s.items() if k != "image"} for s in steps]  # the image is the step's own (step.yaml)
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
    if pipeline_id is None:
        pipeline_id = default_pipeline(db, project_id).id
    pv = PipelineVersion(project_id=project_id, pipeline_id=pipeline_id, version=last + 1, steps=steps, note=note, created_by=user_id,
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


def ensure_project_pipeline(db: Session, project: Project, user_id: int | None = None, template: str = "default"):
    """A new project's "Default" pipeline, version 1 from a template (the default one unless another is named)."""
    t = templates()[template]
    if not default_pipeline(db, project.id):
        db.add(Pipeline(project_id=project.id, name="Default", is_default=True, created_by=user_id))
        db.flush()
    if not current_pipeline(db, project.id):
        save_pipeline(db, project.id, t["steps"], f"Created from the {t['title']} template", user_id)



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
    c = Cohort(project_id=project_id, name=name, meta=metadata_schema.split(meta or {}, custom_fields(db.get(Project, project_id)))[0],
               created_by=user_id)
    db.add(c)
    db.flush()
    return c, True


def dataset_overrides(cohort: Cohort | None, meta: dict, custom: list | None = None) -> dict:
    """What a dataset stores: its dataset-level fields, plus cohort-level fields only where they differ
    from the cohort's value (overrides). custom: the project's own fields (custom_fields)."""
    cohort_part, dataset_part = metadata_schema.split(meta, custom)
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
        ds.study_meta = dataset_overrides(db.get(Cohort, ds.cohort_id), ds.study_meta or {}, custom_fields(ds.project))
    db.commit()


def custom_fields(project: Project | None) -> list:
    """The project's own metadata fields (Project.metadata_custom), hidden ones included."""
    return list((project.metadata_custom if project else None) or [])


def metadata_config(project: Project) -> dict:
    return metadata_schema.normalise_config(project.metadata_config, custom_fields(project))


# ---------- trait types and sample sizes ----------
# A phenotype is case/control ("binary", the default) or "quantitative" (Project.phenotype_types). Case/control datasets
# give cases and controls (the total is their sum); quantitative datasets give the total only.
TRAIT_TYPES = ("binary", "quantitative")


def trait_type(project: Project, phenotype: str) -> str:
    return (project.phenotype_types or {}).get(phenotype, "binary")


def sample_sizes(ttype: str, n_cases, n_controls, n_total) -> dict:
    """The counts a dataset of this trait type stores, or ValueError with a message for the person uploading."""
    def count(v, label):
        if v is None or v == "":
            raise ValueError(f"Give the number of {label}")
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"The number of {label} must be a whole number")
        if n < 1 or n != float(v):
            raise ValueError(f"The number of {label} must be a whole number of at least 1")
        return n
    if ttype == "quantitative":
        return {"n_cases": None, "n_controls": None, "n_total": count(n_total, "participants (total sample size)")}
    cases, controls = count(n_cases, "cases"), count(n_controls, "controls")
    return {"n_cases": cases, "n_controls": controls, "n_total": cases + controls}
