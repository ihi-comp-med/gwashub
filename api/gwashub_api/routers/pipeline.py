import base64
import hashlib
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import services, storage
from gwashub_api.auth import current_user, require_member
from gwashub_api.config import settings
from gwashub_api.db import get_db
from gwashub_api.models import PipelineVersion, Project, Resource, User

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
    """Platform admins only (single request, for scripts and tests; the website uploads in parts, see
    /resources/uploads). meta JSON: {key, title, description, build, attest: true}."""
    m = require_member(db, user, project_id)
    if not user.is_admin:
        raise HTTPException(403, "Only platform admins can add resources")
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


# ---------- resources: platform admins upload them in parts (straight to S3 on AWS) ----------
# Nothing is written to the database until the upload completes: the browser carries a signed ticket.

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class ResourceUploadStart(BaseModel):
    scope: str  # "shared" (every project) or a project id
    key: str  # the file name steps see in --resources, e.g. ldscores_EUR.tsv.gz
    title: str
    description: str = ""
    build: str = "Any"
    file_size: int
    attest: bool = False


def _ticket_sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    mac = hmac.new(settings().secret.encode(), f"resource-upload.{raw}".encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{mac}"


def _ticket_read(ticket: str, user: User) -> dict:
    raw, _, mac = ticket.rpartition(".")
    good = hmac.new(settings().secret.encode(), f"resource-upload.{raw}".encode(), hashlib.sha256).hexdigest()
    if not raw or not hmac.compare_digest(good, mac):
        raise HTTPException(403, "Invalid upload ticket")
    t = json.loads(base64.urlsafe_b64decode(raw))
    if t["exp"] < time.time() or t["uid"] != user.id:
        raise HTTPException(403, "This upload has expired; start again")
    return t


def _require_admin(user: User):
    if not user.is_admin:
        raise HTTPException(403, "Only platform admins can add resources")


@router.post("/resources/uploads", status_code=201)
def start_resource_upload(body: ResourceUploadStart, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    if not body.attest:
        raise HTTPException(422, "Confirm the file contains no individual-level data")
    if not KEY_RE.match(body.key) or ".." in body.key:
        raise HTTPException(422, "The file name may contain letters, digits, dots, hyphens and underscores only")
    if not body.title.strip():
        raise HTTPException(422, "Give the resource a title")
    if body.file_size < 1:
        raise HTTPException(422, "The file is empty")
    parts = -(-body.file_size // storage.PART_SIZE)
    if parts > 10_000:
        raise HTTPException(422, "The file is too large (limit 640 GB)")
    if body.scope == "shared":
        project_id, folder = None, "shared"
    else:
        require_member(db, user, body.scope)
        if db.scalar(select(Resource).where(Resource.project_id.is_(None), Resource.key == body.key)):
            raise HTTPException(409, f"{body.key} is a shared GWASHub resource; choose another name")
        project_id, folder = body.scope, body.scope
    prev = db.scalar(select(Resource).where((Resource.project_id.is_(None)) if project_id is None else (Resource.project_id == project_id),
                                            Resource.key == body.key).order_by(Resource.version.desc()).limit(1))
    version = (prev.version + 1) if prev else 1
    loc = storage.location("resources", f"resources/{folder}/{body.key}/v{version}/{body.key}")
    upload_id = storage.start_multipart(loc)
    ticket = _ticket_sign({"loc": loc, "upload_id": upload_id, "project_id": project_id, "key": body.key, "version": version,
                           "title": body.title.strip(), "description": body.description.strip(), "build": body.build or "Any",
                           "size": body.file_size, "uid": user.id, "exp": int(time.time()) + 24 * 3600})
    return {"ticket": ticket, "part_size": storage.PART_SIZE, "parts": parts, "version": version}


class ResourceParts(BaseModel):
    ticket: str
    parts: list[int]


@router.post("/resources/uploads/urls")
def resource_part_urls(body: ResourceParts, user: User = Depends(current_user)):
    _require_admin(user)
    t = _ticket_read(body.ticket, user)
    if not body.parts or len(body.parts) > 100 or any(not 1 <= n <= 10_000 for n in body.parts):
        raise HTTPException(422, "Ask for 1 to 100 part numbers between 1 and 10,000")
    return {"urls": {str(n): storage.part_url(t["loc"], t["upload_id"], n, "res") for n in body.parts}}


class ResourcePartDone(BaseModel):
    n: int
    etag: str


class ResourceComplete(BaseModel):
    ticket: str
    parts: list[ResourcePartDone]


@router.post("/resources/uploads/complete", status_code=201)
def complete_resource_upload(body: ResourceComplete, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    t = _ticket_read(body.ticket, user)
    expected = -(-t["size"] // storage.PART_SIZE)
    if sorted(p.n for p in body.parts) != list(range(1, expected + 1)):
        raise HTTPException(422, f"Expected parts 1 to {expected}")
    size = storage.complete_multipart(t["loc"], t["upload_id"], [p.model_dump() for p in body.parts])
    if size != t["size"]:
        raise HTTPException(409, f"Received {size:,} bytes but expected {t['size']:,}; try uploading again")
    if db.scalar(select(Resource).where((Resource.project_id.is_(None)) if t["project_id"] is None else (Resource.project_id == t["project_id"]),
                                        Resource.key == t["key"], Resource.version == t["version"])):
        raise HTTPException(409, "This resource version was already added")
    r = Resource(project_id=t["project_id"], key=t["key"], title=t["title"], description=t["description"], build=t["build"],
                 size=size, sha256="", version=t["version"], source="Uploaded", storage_path=t["loc"], added_by=user.id)
    db.add(r)
    db.commit()
    project = db.get(Project, t["project_id"]) if t["project_id"] else None
    return resource_view(db, r, project.short if project else "GWASHub")
