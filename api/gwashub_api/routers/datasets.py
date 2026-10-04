import io
import json
import logging
import mimetypes
import os
import time

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import jobs, services, storage
from gwashub_api.auth import can_open_dataset, current_user, require_dataset, require_member
from gwashub_api.colmap import suggest
from gwashub_api import metadata_schema
from gwashub_api.db import get_db
from gwashub_api.models import Cohort, Dataset, QCRun, User
from gwashub_api.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["datasets"])
ANCESTRIES = ("EUR", "AFR", "EAS", "SAS", "AMR", "Multi")
SEXES = ("Combined", "Female", "Male")
BUILDS = ("GRCh37", "GRCh38")


# QC metrics shown in the datasets table (Details) and the dataset panel: (output key, step name, metric key).
QC_METRICS = [("lambda_gc", "stats_summary", "lambda_gc"), ("lambda_1000", "stats_summary", "lambda_1000"),
              ("lambda_maf01", "stats_summary", "lambda_maf01"), ("n_variants", "stats_summary", "n_variants"),
              ("n_gws", "stats_summary", "n_genome_wide_sig"), ("n_suggestive", "stats_summary", "n_suggestive"),
              ("n_loci", "stats_summary", "n_loci"), ("lifted", "liftover", "lifted"), ("unmapped", "liftover", "unmapped"),
              ("strand_flips", "liftover", "strand_flipped"), ("ldsc_intercept", "ldsc", "intercept"),
              ("ldsc_intercept_se", "ldsc", "intercept_se"), ("ldsc_ratio", "ldsc", "ratio")]


def qc_metrics(run: QCRun | None) -> dict:
    """Headline QC numbers from a run's step logs (by step name, so consortium pipelines work too)."""
    if not run or not run.result:
        return {}
    by_step = {s.get("key", "").split("/")[-1]: s.get("metrics") or {} for s in run.result.get("steps", [])}
    out = {k: by_step[step][metric] for k, step, metric in QC_METRICS if metric in by_step.get(step, {})}
    rows = (run.result.get("outputs") or {}).get("rows")
    if rows is not None:
        out["rows_out"] = rows
    return out


def latest_run(ds: Dataset) -> QCRun | None:
    return ds.runs[-1] if ds.runs else None


def ds_row(ds: Dataset, can_open: bool, cfg: dict | None = None):
    run = latest_run(ds)
    meta = metadata_schema.effective(ds.cohort_ref.meta if ds.cohort_ref else {}, ds.study_meta)
    return {"id": ds.id, "cohort": ds.cohort, "cohort_id": ds.cohort_id, "phenotype": ds.phenotype, "ancestry": ds.ancestry,
            "sex": ds.sex, "build": ds.build, "n_cases": ds.n_cases, "n_controls": ds.n_controls, "file_name": ds.file_name,
            "file_size": ds.file_size, "status": ds.status, "uploaded_by": ds.uploaded_by,
            "uploader": ds.uploader.name, "created_at": ds.created_at, "can_open": can_open, "review": ds.review,
            "pipeline_version": run.pipeline_version.version if run else None,
            "qc": qc_metrics(run) if can_open else {},
            "metadata_complete": metadata_schema.completeness(meta, cfg)}


def run_view(run: QCRun):
    steps = (run.result or {}).get("steps") or jobs.live_progress(run) or run.progress or []
    return {"id": run.id, "status": run.status, "overall_verdict": run.overall_verdict, "error": run.error,
            "pipeline_version": run.pipeline_version.version, "started_at": run.started_at, "finished_at": run.finished_at,
            "stop_reason": (run.result or {}).get("stop_reason"), "rows_out": ((run.result or {}).get("outputs") or {}).get("rows"),
            "steps": steps, "profile": (run.result or {}).get("profile")}


@router.get("/metadata-schema")
def metadata_schema_view(user: User = Depends(current_user)):
    """The whole field catalogue; each project shows the subset in its metadata config."""
    return {"sections": metadata_schema.SECTIONS, "units": metadata_schema.units(), "default_config": metadata_schema.default_config()}


@router.get("/projects/{project_id}/datasets")
def list_datasets(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    cfg = services.metadata_config(m.project)
    q = select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at.desc())
    # Unfinished uploads are listed only to the person uploading.
    return [ds_row(ds, can_open_dataset(m, user, ds), cfg) for ds in db.scalars(q)
            if ds.status != "uploading" or ds.uploaded_by == user.id]


@router.post("/projects/{project_id}/column-suggestion")
async def column_suggestion(project_id: str, file: UploadFile = File(...), user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    """Read only the head of the file: header, preview rows, suggested mapping and effect type."""
    require_member(db, user, project_id)
    head = await file.read(256 * 1024)
    if head[:2] == b"\x1f\x8b":
        import gzip
        import zlib
        head = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head)
    text = head.decode("utf-8", errors="replace")
    lines = text.splitlines()[:-1] or text.splitlines()
    first = lines[0] if lines else ""
    sep = "\t" if "\t" in first else "," if first.count(",") >= 2 else r"\s+"
    df = pd.read_csv(io.StringIO("\n".join(lines[:200])), sep=sep, dtype=str, engine="python")
    s = suggest(list(df.columns))
    beta_col = s["mapping"].get("beta")
    if beta_col:  # value check: all positive and centred on 1 -> odds ratios
        v = pd.to_numeric(df[beta_col], errors="coerce").dropna()
        if len(v) and (v > 0).all() and 0.7 < v.median() < 1.4:
            s["effect_type"] = "or"
    return {"columns": list(df.columns), "preview": df.head(5).fillna("").values.tolist(), **s}


def validate_upload(db, project, d: dict, user: User) -> dict:
    """Checks an upload's details and resolves its cohort: an existing one (cohort_id), or found / created by
    name. A new cohort takes the cohort-level metadata given here; for an existing cohort, values that differ
    from the cohort's become this dataset's overrides. Fields the project requires must be filled in."""
    if d.get("cohort_id"):
        c = db.get(Cohort, int(d["cohort_id"]))
        if not c or c.project_id != project.id:
            raise HTTPException(422, "That cohort isn't in this project")
        d = {**d, "cohort": c.name}
    for f in ("cohort", "phenotype", "ancestry", "sex", "build", "column_mapping"):
        if not d.get(f) or (f == "cohort" and not str(d[f]).strip()):
            raise HTTPException(422, f"missing field: {f}")
    if d["phenotype"] not in project.phenotypes:
        raise HTTPException(422, f"Phenotype '{d['phenotype']}' isn't one of this project's phenotypes")
    if d["ancestry"] not in project.ancestries:
        raise HTTPException(422, f"Ancestry '{d['ancestry']}' isn't allowed in this project")
    if d["sex"] not in project.sexes:
        raise HTTPException(422, f"Sex stratum '{d['sex']}' isn't expected in this project")
    if d["build"] not in BUILDS:
        raise HTTPException(422, "build must be GRCh37 or GRCh38")
    meta = metadata_schema.validate(d.get("study_meta"))
    if len(str(d["cohort"]).strip()) > 200:
        raise HTTPException(422, "Cohort names are limited to 200 characters")
    if d.get("cohort_id"):
        c = db.get(Cohort, int(d["cohort_id"]))
    else:
        c, _ = services.get_or_create_cohort(db, project.id, str(d["cohort"]), user.id, meta)
    stored = services.dataset_overrides(c, meta)
    missing = metadata_schema.missing_required(metadata_schema.effective(c.meta, stored), services.metadata_config(project))
    if missing:
        raise HTTPException(422, f"This project requires: {', '.join(missing)}")
    return {"cohort": c.name, "cohort_id": c.id, "phenotype": d["phenotype"], "ancestry": d["ancestry"], "sex": d["sex"],
            "build": d["build"], "effect_type": d.get("effect_type", "beta"), "n_cases": d.get("n_cases"),
            "n_controls": d.get("n_controls"), "column_mapping": d["column_mapping"], "study_meta": stored}


def safe_name(name: str | None) -> str:
    return os.path.basename((name or "").replace("\\", "/")).strip() or "upload"


def upload_key(ds: Dataset) -> str:
    return f"uploads/{ds.project_id}/{ds.id}/{ds.file_name}"


@router.post("/projects/{project_id}/datasets", status_code=201)
def upload_dataset(project_id: str, file: UploadFile = File(...), meta: str = Form(...),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Single-request upload, for scripts and tests (the website uploads in parts, see /uploads below).
    multipart: file + meta JSON {cohort, phenotype, ancestry, sex, build, effect_type, n_cases, n_controls, column_mapping}."""
    m = require_member(db, user, project_id)
    try:
        d = json.loads(meta)
    except json.JSONDecodeError:
        raise HTTPException(422, "meta must be JSON")
    ds = Dataset(project_id=project_id, uploaded_by=user.id, **validate_upload(db, m.project, d, user),
                 file_name=safe_name(file.filename), file_size=0, storage_path="", status="processing")
    db.add(ds)
    db.flush()
    ds.storage_path, ds.file_size, _ = storage.save_stream("uploads", upload_key(ds), file.file)
    run = start_run(db, ds)
    db.commit()
    jobs.start_qc(run.id)
    return {"id": ds.id, "run_id": run.id, "status": ds.status}


# ---------- uploads in parts: browser → S3 directly (AWS) or → the API (local) ----------

class UploadStart(BaseModel):
    meta: dict
    file_name: str
    file_size: int


@router.post("/projects/{project_id}/uploads", status_code=201)
def start_upload(project_id: str, body: UploadStart, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Create the dataset (status "uploading") and start a multipart upload. The browser then asks for
    part URLs, PUTs the parts and calls /complete."""
    m = require_member(db, user, project_id)
    if body.file_size < 1:
        raise HTTPException(422, "The file is empty")
    n_parts = -(-body.file_size // storage.PART_SIZE)
    if n_parts > 10_000:
        raise HTTPException(422, "The file is too large (limit 640 GB)")
    ds = Dataset(project_id=project_id, uploaded_by=user.id, **validate_upload(db, m.project, body.meta, user),
                 file_name=safe_name(body.file_name), file_size=body.file_size, storage_path="", status="uploading")
    db.add(ds)
    db.flush()
    ds.storage_path = storage.location("uploads", upload_key(ds))
    ds.upload_id = storage.start_multipart(ds.storage_path)
    db.commit()
    return {"id": ds.id, "part_size": storage.PART_SIZE, "parts": n_parts}


def require_own_upload(db, user, dataset_id) -> Dataset:
    ds, _ = require_dataset(db, user, dataset_id)
    if ds.uploaded_by != user.id:
        raise HTTPException(403, "Only the uploader can send this file")
    if ds.status != "uploading" or not ds.upload_id:
        raise HTTPException(409, "This upload has already finished")
    return ds


class PartsRequest(BaseModel):
    parts: list[int]


@router.post("/datasets/{dataset_id}/upload/urls")
def part_urls(dataset_id: int, body: PartsRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds = require_own_upload(db, user, dataset_id)
    if not body.parts or len(body.parts) > 100 or any(not 1 <= n <= 10_000 for n in body.parts):
        raise HTTPException(422, "Ask for 1 to 100 part numbers between 1 and 10,000")
    return {"urls": {str(n): storage.part_url(ds.storage_path, ds.upload_id, n, f"ds{ds.id}") for n in body.parts}}


@router.put("/uploads/local/{owner}/{upload_id}/{part}", include_in_schema=False)
async def local_part(owner: str, upload_id: str, part: int, expires: int, token: str, request: Request,
                     db: Session = Depends(get_db)):
    """Local storage only: receives one part of a dataset ("ds<id>") or resource ("res") upload, authorised by
    the signed URL (like an S3 presigned URL)."""
    import hmac
    if settings().storage != "local":
        raise HTTPException(404, "Not Found")
    if expires < time.time() or not hmac.compare_digest(storage.local_part_token(owner, upload_id, part, expires), token):
        raise HTTPException(403, "This upload link has expired; try again")
    if owner.startswith("ds"):
        ds = db.get(Dataset, int(owner[2:])) if owner[2:].isdigit() else None
        if not ds or ds.upload_id != upload_id:
            raise HTTPException(404, "No such upload")
    elif owner != "res":
        raise HTTPException(404, "No such upload")
    import tempfile
    with tempfile.SpooledTemporaryFile(max_size=8 << 20) as buf:
        async for chunk in request.stream():
            buf.write(chunk)
        buf.seek(0)
        etag = storage.save_local_part("", upload_id, part, buf)
    return Response(status_code=200, headers={"ETag": etag})


class PartDone(BaseModel):
    n: int
    etag: str


class UploadComplete(BaseModel):
    parts: list[PartDone]


@router.post("/datasets/{dataset_id}/upload/complete")
def complete_upload(dataset_id: int, body: UploadComplete, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds = require_own_upload(db, user, dataset_id)
    expected = -(-ds.file_size // storage.PART_SIZE)
    if sorted(p.n for p in body.parts) != list(range(1, expected + 1)):
        raise HTTPException(422, f"Expected parts 1 to {expected}")
    try:
        size = storage.complete_multipart(ds.storage_path, ds.upload_id, [p.model_dump() for p in body.parts])
    except Exception as e:
        log.warning("Completing upload of dataset %s failed: %s", ds.id, e)
        raise HTTPException(409, "The upload could not be completed; some parts are missing. Try uploading again.")
    if size != ds.file_size:
        raise HTTPException(409, f"Received {size:,} bytes but expected {ds.file_size:,}; try uploading again")
    ds.upload_id, ds.status = "", "processing"
    run = start_run(db, ds)
    db.commit()
    jobs.start_qc(run.id)
    return {"id": ds.id, "run_id": run.id, "status": ds.status}


def start_run(db, ds: Dataset) -> QCRun:
    pv = services.current_pipeline(db, ds.project_id)
    run = QCRun(dataset_id=ds.id, pipeline_version_id=pv.id, status="queued")
    db.add(run)
    db.flush()
    return run


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds, m = require_dataset(db, user, dataset_id)
    run = latest_run(ds)
    cohort_meta = (ds.cohort_ref.meta if ds.cohort_ref else {}) or {}
    return {**ds_row(ds, True, services.metadata_config(m.project)), "column_mapping": ds.column_mapping, "effect_type": ds.effect_type,
            "study_meta": metadata_schema.effective(cohort_meta, ds.study_meta), "cohort_meta": cohort_meta,
            "overrides": sorted(k for k in (ds.study_meta or {}) if k in metadata_schema.COHORT_KEYS),
            "metadata_config": services.metadata_config(m.project),
            "edits": ds.edits or [], "can_edit": m.role == "reviewer" or ds.uploaded_by == user.id,
            "run": run and run_view(run), "runs": [{"id": r.id, "status": r.status, "overall_verdict": r.overall_verdict,
                                                    "pipeline_version": r.pipeline_version.version, "finished_at": r.finished_at} for r in ds.runs]}


def send_file(loc: str, filename: str | None = None, media_type: str | None = None, inline: bool = False):
    """Local: stream the file. S3: redirect to a short-lived presigned URL (the API never streams big files)."""
    url = storage.download_url(loc, filename, inline=inline, content_type=media_type)
    if url:
        return RedirectResponse(url, status_code=307)
    if not os.path.isfile(loc):
        raise HTTPException(404, "File not found")
    return FileResponse(loc, filename=None if inline else filename, media_type=media_type)


PREVIEW_LIMIT = 20 * 1024 * 1024  # larger step files are download-only


def step_path_parts(path: str) -> list[str]:
    parts = path.replace("\\", "/").strip("/").split("/")
    if parts[0] != "steps" or len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(404, "No such file")
    return parts


@router.get("/datasets/{dataset_id}/runs/{run_id}/files")
def run_files(dataset_id: int, run_id: int, dir: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Every file a step wrote (dir = steps/NN_name), declared as an artifact or not."""
    ds, _ = require_dataset(db, user, dataset_id)
    run = next((r for r in ds.runs if r.id == run_id), None)
    if not run or not run.run_dir:
        raise HTTPException(404, "No run output")
    parts = step_path_parts(dir)
    return [{"path": "/".join([*parts, f["path"]]), "size": f["size"]} for f in storage.list_files(storage.join(run.run_dir, *parts))]


@router.get("/datasets/{dataset_id}/files/{kind}")
def dataset_file(dataset_id: int, kind: str, path: str | None = None, run_id: int | None = None, download: bool = False,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    """kind: raw | qc | report | artifact (path = steps/NN_name/file; download=1 for a direct download)."""
    ds, _ = require_dataset(db, user, dataset_id)
    if kind == "raw":
        if ds.status == "uploading":
            raise HTTPException(409, "The upload hasn't finished")
        return send_file(ds.storage_path, ds.file_name)
    run = next((r for r in ds.runs if r.id == run_id), None) if run_id else (ds.runs[-1] if ds.runs else None)
    if not run or not run.run_dir:
        raise HTTPException(404, "No run output")
    if kind == "qc":
        if run.status != "completed":
            raise HTTPException(409, "QC did not complete; no cleaned file")
        return send_file(storage.join(run.run_dir, "gwas_qc.tsv"), f"{ds.cohort}_{ds.phenotype}_{ds.ancestry}_{ds.sex}_qc.tsv".replace(" ", "_"))
    if kind == "report":
        rep = ((run.result or {}).get("outputs") or {}).get("qc_report")
        if not rep:
            raise HTTPException(404, "No report")
        return send_file(storage.join(run.run_dir, rep), f"qc_report_{ds.id}.html", media_type="text/html")
    if kind == "artifact" and path:
        parts = step_path_parts(path)
        if len(parts) < 3:
            raise HTTPException(404, "No such file")
        loc = storage.join(run.run_dir, *parts)
        if download:  # straight from storage (a presigned S3 link on AWS)
            return send_file(loc, f"run{run.id}_{parts[1]}_{parts[-1]}")
        # Previews in the website (plots, tables, logs) are read through the API, same origin.
        size = storage.size(loc)
        if size is None:
            raise HTTPException(404, "No such file")
        if size > PREVIEW_LIMIT:
            raise HTTPException(413, "This file is too large to preview; download it instead")
        return Response(storage.read_bytes(loc), media_type=mimetypes.guess_type(parts[-1])[0] or "application/octet-stream")
    raise HTTPException(404, "Unknown file kind")


@router.post("/datasets/{dataset_id}/rerun")
def rerun(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds, m = require_dataset(db, user, dataset_id)
    if m.role != "reviewer":
        raise HTTPException(403, "Only reviewers can re-run QC")
    if ds.status in ("processing", "uploading"):
        raise HTTPException(409, "QC is already running")
    ds.status, ds.review = "processing", None
    run = start_run(db, ds)
    db.commit()
    jobs.start_qc(run.id)
    return {"run_id": run.id}


class DatasetEdit(BaseModel):
    cohort: str | None = None
    phenotype: str | None = None
    ancestry: str | None = None
    sex: str | None = None
    n_cases: int | None = None
    n_controls: int | None = None
    build: str | None = None
    study_meta: dict | None = None  # full replacement of the study metadata


CORE_EDITABLE = ("cohort", "phenotype", "ancestry", "sex", "n_cases", "n_controls", "build")


@router.patch("/datasets/{dataset_id}")
def edit_dataset(dataset_id: int, body: DatasetEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The uploader or a reviewer can edit descriptive fields and study metadata. Every change is logged."""
    ds, m = require_dataset(db, user, dataset_id)
    if not (m.role == "reviewer" or ds.uploaded_by == user.id):
        raise HTTPException(403, "Only the uploader or a reviewer can edit this dataset")
    p = m.project
    changes = {}
    old_meta = metadata_schema.effective(ds.cohort_ref.meta if ds.cohort_ref else {}, ds.study_meta)
    for f in CORE_EDITABLE:
        v = getattr(body, f)
        if v is None or v == getattr(ds, f):
            continue
        if f == "cohort":
            if not str(v).strip() or len(str(v).strip()) > 200:
                raise HTTPException(422, "Give a cohort name (up to 200 characters)")
            # Moving a dataset to another cohort (found by name, or created).
            c, _ = services.get_or_create_cohort(db, ds.project_id, str(v), user.id)
            if c.id == ds.cohort_id:
                continue
            changes["cohort"] = [ds.cohort, c.name]
            ds.cohort_id, ds.cohort, ds.cohort_ref = c.id, c.name, c
            continue
        if f == "phenotype" and v not in p.phenotypes:
            raise HTTPException(422, f"Phenotype '{v}' isn't one of this project's phenotypes")
        if f == "ancestry" and v not in p.ancestries:
            raise HTTPException(422, f"Ancestry '{v}' isn't allowed in this project")
        if f == "sex" and v not in p.sexes:
            raise HTTPException(422, f"Sex stratum '{v}' isn't expected in this project")
        if f in ("n_cases", "n_controls") and v < 1:
            raise HTTPException(422, f"{f} must be at least 1")
        if f == "build":
            if v not in BUILDS:
                raise HTTPException(422, "build must be GRCh37 or GRCh38")
            if ds.status in ("processing", "uploading"):
                raise HTTPException(409, "QC is running; change the build when it has finished")
        changes[f] = [getattr(ds, f), v]
        setattr(ds, f, v)
    if body.study_meta is not None:
        # The client sends the dataset's full (effective) metadata; cohort-level values equal to the cohort's
        # aren't stored on the dataset, so editing the cohort later still flows through.
        new = metadata_schema.validate(body.study_meta)
        for k in sorted(set(old_meta) | set(new)):
            if old_meta.get(k) != new.get(k):
                changes[k] = [old_meta.get(k), new.get(k)]
        ds.study_meta = services.dataset_overrides(ds.cohort_ref, new)
    if changes:
        ds.edits = [*(ds.edits or []), {"by": user.name, "at": jobs.now().isoformat(), "changes": changes}]
    # The build decides liftover, so QC output made with the old build is wrong: re-run straight away.
    run = None
    if "build" in changes:
        ds.status, ds.review = "processing", None
        run = start_run(db, ds)
    db.commit()
    if run:
        jobs.start_qc(run.id)
    rerun_advised = not run and any(k in changes for k in ("ancestry", "n_cases", "n_controls"))
    return {"changed": sorted(changes), "rerun_advised": rerun_advised, "rerun_started": bool(run)}


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The uploader or a reviewer can delete a dataset: the upload, every QC run and its outputs.
    Meta-analyses already run keep their results."""
    ds, m = require_dataset(db, user, dataset_id)
    if not (m.role == "reviewer" or ds.uploaded_by == user.id):
        raise HTTPException(403, "Only the uploader or a reviewer can delete this dataset")
    if ds.status == "processing":
        raise HTTPException(409, "QC is running; delete the dataset when it has finished")
    if ds.status == "uploading" and ds.upload_id:
        storage.abort_multipart(ds.storage_path, ds.upload_id)
    folders = [storage.location("uploads", f"uploads/{ds.project_id}/{ds.id}")] + [r.run_dir for r in ds.runs]
    log.info("Dataset %s (%s, %s) deleted by %s", ds.id, ds.cohort, ds.file_name, user.email)
    db.delete(ds)
    db.commit()
    for loc in folders:
        storage.delete_prefix(loc)


class Review(BaseModel):
    decision: str  # approve | reject | request_changes
    note: str = ""


@router.post("/datasets/{dataset_id}/review")
def review(dataset_id: int, body: Review, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds, m = require_dataset(db, user, dataset_id)
    if m.role != "reviewer":
        raise HTTPException(403, "Only reviewers can review datasets")
    if body.decision not in ("approve", "reject", "request_changes"):
        raise HTTPException(422, "decision must be approve, reject or request_changes")
    if ds.status == "processing":
        raise HTTPException(409, "QC is still running")
    ds.status = "passed" if body.decision == "approve" else "failed"
    ds.review = {"decision": body.decision, "note": body.note, "by": user.name, "at": jobs.now().isoformat()}
    db.commit()
    return ds_row(ds, True)


class BulkRerun(BaseModel):
    ids: list[int]


@router.post("/projects/{project_id}/datasets/rerun")
def bulk_rerun(project_id: str, body: BulkRerun, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewer-only: re-run QC on several datasets with the project's current pipeline version."""
    require_member(db, user, project_id, role="reviewer")
    if not body.ids or len(body.ids) > 500:
        raise HTTPException(422, "Choose between 1 and 500 datasets")
    started, skipped = [], []
    for i in dict.fromkeys(body.ids):
        ds = db.get(Dataset, i)
        if not ds or ds.project_id != project_id:
            skipped.append({"id": i, "reason": "not in this project"})
        elif ds.status in ("processing", "uploading"):
            skipped.append({"id": i, "reason": "QC is already running" if ds.status == "processing" else "upload not finished"})
        else:
            ds.status, ds.review = "processing", None
            started.append(start_run(db, ds).id)
    db.commit()
    for run_id in started:
        jobs.start_qc(run_id)
    return {"started": len(started), "skipped": skipped}
