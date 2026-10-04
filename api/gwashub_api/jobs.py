"""QC runs and meta-analyses as jobs.

Each job is described by a spec (see pipeline/runner/jobspec.py) and leaves result.json in its
output location. GWASHUB_JOBS picks where it runs:
  local  a thread in this process (development; tests run it inline);
  batch  AWS Batch. A QC run is one job per group of consecutive steps that share an image, each in
         that image and depending on the previous job; a meta-analysis is one job in gwashub/base.
         Batch state changes reach an SQS queue through EventBridge, and the worker
         (gwashub_api.worker) calls handle_batch_event to record the outcome.
"""
import json
import logging
import os
import re
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import func, select

from gwashub_api import services, storage
from gwashub_api.config import settings
from gwashub_api.db import SessionLocal
from gwashub_api.models import Dataset, MetaAnalysis, QCRun

log = logging.getLogger(__name__)
_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=settings().max_workers, thread_name_prefix="gwashub-job")
    return _pool


def _in_background(fn, *args):
    if settings().inline_jobs:
        fn(*args)
    else:
        pool().submit(fn, *args)


def now():
    return datetime.now(timezone.utc)


def status_from_result(result: dict) -> str:
    if result["status"] != "completed":
        return "failed"
    return {"pass": "passed", "warn": "review", "fail": "failed"}[result["overall_verdict"]]


def dataset_params(ds: Dataset) -> dict:
    return {"column_mapping": ds.column_mapping, "build": ds.build, "ancestry": ds.ancestry, "effect_type": ds.effect_type,
            "n_cases": ds.n_cases, "n_controls": ds.n_controls, "cohort": ds.cohort, "phenotype": ds.phenotype, "sex": ds.sex}


def latest_completed_run(ds: Dataset) -> QCRun | None:
    for r in reversed(ds.runs):
        if r.status == "completed" and r.result:
            return r
    return None


# ---------- specs ----------

def qc_spec(db, run: QCRun) -> dict:
    from pipeline.runner.pipeline import entries_from_dicts, plan_groups, resolve
    ds, pv = run.dataset, run.pipeline_version
    groups = plan_groups(resolve(entries_from_dicts(pv.steps), services.catalog(), images=services.images()))
    return {"kind": "qc", "run_id": run.id, "input": ds.storage_path, "params": dataset_params(ds), "steps": pv.steps,
            "resources": services.resource_locations(db, pv), "output": run.run_dir, "groups": groups,
            "images": {g["image"]: settings().images.get(g["image"], {}).get("uri", "local") for g in groups}}


def meta_spec(db, ma: MetaAnalysis) -> dict:
    inputs = []
    for did in ma.dataset_ids:
        ds = db.get(Dataset, did)
        run = latest_completed_run(ds) if ds else None
        if not run:
            raise RuntimeError(f"dataset {did} has no completed QC run")
        inputs.append({"id": ds.id, "label": f"{ds.cohort} · {ds.ancestry} · {ds.sex}", "loc": storage.join(run.run_dir, "gwas_qc.tsv"),
                       "cohort": ds.cohort, "phenotype": ds.phenotype, "n_cases": ds.n_cases, "n_controls": ds.n_controls,
                       "ancestry": ds.ancestry, "sex": ds.sex, "qc_run_id": run.id})
    from gwashub_api.routers.meta import normalise
    s = normalise(ma.settings)
    image = meta_image(s["method"])
    return {"kind": "meta", "meta_id": ma.id, "method": s["method"], "inputs": inputs, "params": s["params"].get(s["method"], {}),
            "common": s["common"], "output": ma.run_dir, "images": {image: settings().images.get(image, {}).get("uri", "local")}}


def meta_image(method_key: str) -> str:
    from pipeline.runner.catalog import load_analyses
    md = load_analyses().get(method_key)
    return md.image if md else "gwashub/base"


# ---------- starting jobs ----------

def start_qc(run_id: int):
    """Call after the QCRun is committed."""
    db = SessionLocal()
    try:
        run = db.get(QCRun, run_id)
        run.run_dir = storage.location("results", f"qc/{run.id}")
        spec = qc_spec(db, run)
        titles = services.step_titles()
        run.progress = [{"key": s["step"], "title": titles.get(s["step"], s["step"]), "state": "queued"} for s in spec["steps"]]
        db.commit()
        if settings().jobs == "batch":
            run.batch_job_id = _submit_qc_groups(spec, run.id)
            db.commit()
        else:
            _in_background(_run_qc_locally, run.id, spec)
    except Exception as e:
        db.rollback()
        _fail_qc(db, run_id, f"Could not start QC: {e}\n{traceback.format_exc()[-1500:]}")
    finally:
        db.close()


def start_meta(meta_id: int):
    db = SessionLocal()
    try:
        ma = db.get(MetaAnalysis, meta_id)
        ma.run_dir = storage.location("results", f"meta/{ma.id}")
        spec = meta_spec(db, ma)
        db.commit()
        if settings().jobs == "batch":
            ma.batch_job_id = _submit_batch(spec, f"gwashub-meta-{ma.id}", meta_image(spec["method"]))
            db.commit()
        else:
            _in_background(_run_meta_locally, ma.id, spec)
    except Exception as e:
        db.rollback()
        _fail_meta(db, meta_id, f"Could not start the meta-analysis: {e}")
    finally:
        db.close()


def _batch():
    import boto3
    if not hasattr(_batch, "client"):
        _batch.client = boto3.client("batch", region_name=settings().aws_region)
    return _batch.client


def _put_spec(spec: dict) -> str:
    spec_loc = storage.join(spec["output"], "spec.json")
    bucket, key = storage.split_s3(spec_loc)
    storage._s3().put_object(Bucket=bucket, Key=key, Body=json.dumps(spec, default=str).encode(), ContentType="application/json")
    return spec_loc


def _submit_batch(spec: dict, name: str, image: str, extra_args=(), depends_on: str | None = None, put_spec=True) -> str:
    image_cfg = settings().images.get(image)
    if not image_cfg:
        raise RuntimeError(f"No Batch job definition for image {image}; deploy it first (pipeline/images)")
    spec_loc = _put_spec(spec) if put_spec else storage.join(spec["output"], "spec.json")
    kwargs = {"dependsOn": [{"jobId": depends_on, "type": "SEQUENTIAL"}]} if depends_on else {}
    r = _batch().submit_job(jobName=name, jobQueue=settings().batch_queue, jobDefinition=image_cfg["job_definition"],
                            containerOverrides={"command": ["python", "-m", "pipeline.runner.aws.batch_entry", spec_loc, *extra_args]},
                            tags={"gwashub": name}, **kwargs)
    log.info("Submitted %s (%s) as Batch job %s", name, image, r["jobId"])
    return r["jobId"]


def _submit_qc_groups(spec: dict, run_id: int) -> str:
    """One job per image group, each waiting for the previous one. Returns the last job's id, whose
    success means the run finished (result.json written)."""
    _put_spec(spec)
    previous = None
    for k, g in enumerate(spec["groups"]):
        previous = _submit_batch(spec, f"gwashub-qc-{run_id}-g{k}", g["image"], ["--group", str(k)], depends_on=previous,
                                 put_spec=False)
    return previous


# ---------- local execution ----------

def _run_qc_locally(run_id: int, spec: dict):
    from pipeline.runner.jobspec import run_spec
    db = SessionLocal()
    try:
        run = db.get(QCRun, run_id)
        run.status, run.started_at = "running", now()
        db.commit()

        def on_step(i, state, res):
            prog = [dict(p) for p in run.progress]
            if i < len(prog):
                prog[i]["state"] = state
                if res:
                    prog[i]["messages"] = res.get("messages", [])[:2]
            run.progress = prog
            db.commit()

        result = run_spec(spec, os.path.join(run.run_dir, "_work"), on_step=on_step)
        apply_qc_result(db, run, result)
    except Exception as e:  # infrastructure error, not a QC verdict
        db.rollback()
        _fail_qc(db, run_id, f"{e}\n{traceback.format_exc()[-1500:]}")
    finally:
        db.close()


def _run_meta_locally(meta_id: int, spec: dict):
    from pipeline.runner.jobspec import run_spec
    db = SessionLocal()
    try:
        ma = db.get(MetaAnalysis, meta_id)
        ma.status = "running"
        db.commit()
        result = run_spec(spec, tempfile.mkdtemp(prefix="gwashub-meta-"))
        apply_meta_result(db, ma, result)
    except Exception as e:
        db.rollback()
        _fail_meta(db, meta_id, str(e)[-3000:])
    finally:
        db.close()


# ---------- recording outcomes ----------

def is_latest(db, run: QCRun) -> bool:
    """Only the dataset's newest run decides its status (an older run finishing late doesn't)."""
    return db.scalar(select(func.max(QCRun.id)).where(QCRun.dataset_id == run.dataset_id)) == run.id


def apply_qc_result(db, run: QCRun, result: dict):
    run.result, run.status, run.overall_verdict, run.finished_at = result, result["status"], result["overall_verdict"], now()
    run.progress = [{k: s.get(k) for k in ("key", "title", "state", "messages") if k in s} for s in result.get("steps", [])]
    ds = run.dataset
    if not ds.review and is_latest(db, run):
        ds.status = status_from_result(result)
    db.commit()


def apply_meta_result(db, ma: MetaAnalysis, result: dict):
    if result.get("status") != "completed":
        ma.status, ma.error, ma.finished_at = "failed", (result.get("error") or "Meta-analysis failed")[-3000:], now()
    else:
        ma.result = {"summary": result["summary"], "log": result["log"]}
        ma.lambda_gc = result["summary"].get("lambda_gc")
        ma.status, ma.finished_at = "completed", now()
    db.commit()


def _fail_qc(db, run_id: int, error: str):
    run = db.get(QCRun, run_id)
    if not run:
        return
    run.status, run.error, run.finished_at = "failed", error, now()
    if is_latest(db, run):
        run.dataset.status = "failed"
    db.commit()


def _fail_meta(db, meta_id: int, error: str):
    ma = db.get(MetaAnalysis, meta_id)
    if ma:
        ma.status, ma.error, ma.finished_at = "failed", error, now()
        db.commit()


def read_result(output_loc: str) -> dict | None:
    loc = storage.join(output_loc, "result.json")
    return json.loads(storage.read_bytes(loc)) if storage.exists(loc) else None


JOB_NAME = re.compile(r"^gwashub-(qc|meta)-(\d+)(?:-g(\d+))?$")


def handle_batch_event(db, event: dict) -> str:
    """Record a Batch "Job State Change" event (from EventBridge via SQS). Returns what was done.
    QC runs have one job per image group; only the last group's success completes the run, and any
    group's failure fails it."""
    d = event.get("detail") or {}
    job_id, status = d.get("jobId"), d.get("status")
    m = JOB_NAME.match(d.get("jobName") or "")
    run = ma = None
    if m and m.group(1) == "qc":
        run = db.get(QCRun, int(m.group(2)))
    elif m:
        ma = db.get(MetaAnalysis, int(m.group(2)))
    if not run and not ma and job_id:
        run = db.scalar(select(QCRun).where(QCRun.batch_job_id == job_id))
        ma = None if run else db.scalar(select(MetaAnalysis).where(MetaAnalysis.batch_job_id == job_id))
    if not run and not ma:
        return f"ignored {job_id}: no matching run"
    target = run or ma
    if target.status in ("completed", "blocked", "failed") and target.finished_at:
        return f"ignored {job_id}: already recorded"
    if status == "RUNNING":
        if target.status != "running":
            target.status = "running"
            if run:
                run.started_at = now()
            db.commit()
        return f"{job_id} running"
    if status == "SUCCEEDED" and run and job_id != run.batch_job_id:
        return f"{job_id} finished a group of run {run.id}"
    if status == "SUCCEEDED":
        result = read_result(target.run_dir)
        if result is None:
            status, d["statusReason"] = "FAILED", "The job finished without writing result.json"
        elif run:
            apply_qc_result(db, run, result)
            return f"qc run {run.id} recorded: {result['status']}"
        else:
            apply_meta_result(db, ma, result)
            return f"meta-analysis {ma.id} recorded: {ma.status}"
    if status == "FAILED":
        container = d.get("container") or {}
        reason = d.get("statusReason") or container.get("reason") or "Batch job failed"
        log_hint = f" (log stream {container['logStreamName']})" if container.get("logStreamName") else ""
        msg = f"The job failed on AWS Batch: {reason}{log_hint}"
        if run:
            _fail_qc(db, run.id, msg)
        else:
            _fail_meta(db, ma.id, msg)
        return f"{job_id} failed: {reason}"
    return f"ignored {job_id}: status {status}"


def live_progress(run: QCRun) -> list | None:
    """While a Batch QC job runs, its step states are in progress.json next to its outputs."""
    if settings().jobs != "batch" or run.status not in ("queued", "running") or not run.run_dir:
        return None
    try:
        return json.loads(storage.read_bytes(storage.join(run.run_dir, "progress.json")))
    except Exception:
        return None


def recover():
    """On startup (local jobs only), restart work interrupted by a restart. Batch jobs carry on by themselves."""
    if settings().jobs != "local":
        return
    db = SessionLocal()
    try:
        runs = [r.id for r in db.scalars(select(QCRun).where(QCRun.status.in_(("queued", "running"))))]
        metas = [m.id for m in db.scalars(select(MetaAnalysis).where(MetaAnalysis.status.in_(("queued", "running"))))]
        for r in db.scalars(select(QCRun).where(QCRun.id.in_(runs))):
            r.status = "queued"
        for m in db.scalars(select(MetaAnalysis).where(MetaAnalysis.id.in_(metas))):
            m.status = "queued"
        db.commit()
    finally:
        db.close()
    for rid in runs:
        start_qc(rid)
    for mid in metas:
        start_meta(mid)
