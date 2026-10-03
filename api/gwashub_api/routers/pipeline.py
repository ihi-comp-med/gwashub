import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import services, storage
from gwashub_api.auth import current_user, require_member
from gwashub_api.config import settings
from gwashub_api.db import get_db
from gwashub_api.models import PipelineVersion, Resource, User

router = APIRouter(prefix="/api", tags=["pipeline"])


def user_name(db, uid):
    u = db.get(User, uid) if uid else None
    return u.name if u else "GWASHub"


def resource_view(db, r: Resource, project_short: str):
    return {"id": r.id, "key": r.key, "title": r.title, "description": r.description, "scope": "project" if r.project_id else "shared",
            "scope_label": project_short if r.project_id else "GWASHub", "build": r.build, "size": r.size, "sha256": r.sha256,
            "version": r.version, "source": r.source, "added_by": user_name(db, r.added_by), "added_at": r.added_at}


@router.get("/projects/{project_id}/pipeline")
def get_pipeline(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, project_id)
    project = m.project
    cat = services.catalog()
    pv = services.current_pipeline(db, project_id)
    history = db.scalars(select(PipelineVersion).where(PipelineVersion.project_id == project_id).order_by(PipelineVersion.version.desc()))
    return {
        "role": m.role,
        "version": pv.version, "steps": pv.steps, "pinned_resources": services.pinned_resources(pv),
        "history": [{"version": h.version, "note": h.note, "by": user_name(db, h.created_by), "at": h.created_at} for h in history],
        "catalog": [services.step_info(sd) for sd in cat.values()],
        "images": list(services.images().values()),
        "groups": next((r["groups"] for r in pv.resolved or [] if "groups" in r), None),
        "contributing_url": settings().contributing_url,
        "resources": [resource_view(db, r, project.short) for r in services.latest_resources(db, project_id).values()],
    }


class PipelineSave(BaseModel):
    steps: list[dict]
    note: str = ""


@router.put("/projects/{project_id}/pipeline")
def save_pipeline(project_id: str, body: PipelineSave, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, project_id, role="reviewer")
    try:
        pv = services.save_pipeline(db, project_id, body.steps, body.note or "Updated pipeline", user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return {"version": pv.version}


SHOW_EXT = (".py", ".r", ".R", ".sh", ".yaml", ".yml", ".md", ".tsv", ".txt", ".json")


@router.get("/projects/{project_id}/steps/files")
def step_files(project_id: str, key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Source files for the code viewer: the step's folder plus the shared _lib (as deployed)."""
    require_member(db, user, project_id)
    cat = services.catalog()
    if key not in cat:
        raise HTTPException(404, "Unknown step")
    sd = cat[key]
    qc = settings().qc_steps_dir
    roots = [(sd.workdir, f"pipeline/qc_steps/{sd.key}"), (os.path.join(qc, "_lib"), "pipeline/qc_steps/_lib")]
    files = []
    for root, prefix in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
            for fn in sorted(filenames):
                if not fn.endswith(SHOW_EXT):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.join(prefix, os.path.relpath(full, root)).lstrip("/")
                with open(full, errors="replace") as f:
                    content = f.read(200_000)
                files.append({"path": rel, "content": content})
    main = sd.source.get("path")
    files.sort(key=lambda f: (f["path"] != main, f["path"].count("/") == 0, f["path"]))
    return {"key": key, "title": sd.title, "source": sd.source, "files": files}


@router.post("/projects/{project_id}/resources", status_code=201)
def add_resource(project_id: str, file: UploadFile | None = File(default=None), meta: str = Form(...),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reviewer-only. meta JSON: {key, title, description, build, attest: true, url?}. A file upload or a URL import."""
    m = require_member(db, user, project_id, role="reviewer")
    d = json.loads(meta)
    if not d.get("attest"):
        raise HTTPException(422, "You must confirm the file contains no individual-level data")
    key = os.path.basename(d.get("key") or (file.filename if file else "") or "")
    if not key or not d.get("title"):
        raise HTTPException(422, "key and title are required")
    shared = db.scalar(select(Resource).where(Resource.project_id.is_(None), Resource.key == key))
    if shared:
        raise HTTPException(409, f"{key} is a shared GWASHub resource; choose another name")
    prev = db.scalar(select(Resource).where(Resource.project_id == project_id, Resource.key == key).order_by(Resource.version.desc()).limit(1))
    version = (prev.version + 1) if prev else 1
    if file is None:
        raise HTTPException(422, "URL import isn't available in local mode; upload the file instead")
    path, size, sha = storage.save_stream("resources", f"resources/{project_id}/{key}/v{version}/{key}", file.file)
    r = Resource(project_id=project_id, key=key, title=d["title"], description=d.get("description", ""),
                 build=d.get("build", "Any"), size=size, sha256=sha, version=version, source="Uploaded",
                 storage_path=path, added_by=user.id)
    db.add(r)
    db.commit()
    return resource_view(db, r, m.project.short)
