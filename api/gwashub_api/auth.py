"""Authentication + server-side permission checks.

Dev mode (GWASHUB_AUTH=dev, local only): POST /api/auth/login {email} signs you in as an existing
user (no password). Cognito mode (AWS): sign-in happens on the Cognito hosted pages (see
routers/cognito.py) and the API only learns who you are from a verified ID token.

In every mode the session is an HMAC-signed cookie holding the user id and an expiry time. Project
roles live in the GWASHub database, so the permission helpers below are the same in every mode.
"""
import hashlib
import hmac
import time

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api.config import settings
from gwashub_api.db import get_db
from gwashub_api.models import Dataset, Membership, Project, User

COOKIE = "gh_session"


def _mac(payload: str) -> str:
    return hmac.new(settings().secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign(user_id: int, expires: int | None = None) -> str:
    """Session token "<uid>.<expires>.<mac>"; expires defaults to now + GWASHUB_SESSION_HOURS."""
    expires = expires or int(time.time() + settings().session_hours * 3600)
    return f"{user_id}.{expires}.{_mac(f'{user_id}.{expires}')}"


def verify(token: str | None) -> int | None:
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    uid, expires, mac = parts
    if int(expires) < time.time() or not hmac.compare_digest(_mac(f"{uid}.{expires}"), mac):
        return None
    return int(uid)


def set_session(response, user_id: int):
    response.set_cookie(COOKIE, sign(user_id), httponly=True, samesite="lax", secure=settings().auth_mode != "dev",
                        max_age=int(settings().session_hours * 3600), path="/")


def current_user(gh_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    uid = verify(gh_session)
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def membership(db: Session, user: User, project_id: str) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.project_id == project_id))


def require_member(db: Session, user: User, project_id: str, role: str | None = None) -> Membership:
    """404 for non-members (don't reveal the project exists); 403 if the role is insufficient."""
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    m = membership(db, user, project_id)
    if not m:
        raise HTTPException(404, "Project not found")
    if role == "reviewer" and m.role != "reviewer":
        raise HTTPException(403, "Only project reviewers can do this")
    return m


def can_open_dataset(m: Membership, user: User, ds: Dataset) -> bool:
    return m.role == "reviewer" or ds.uploaded_by == user.id


def require_dataset(db: Session, user: User, dataset_id: int, open_: bool = True) -> tuple[Dataset, Membership]:
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    m = require_member(db, user, ds.project_id)
    if open_ and not can_open_dataset(m, user, ds):
        raise HTTPException(403, "Uploaders can only open datasets they submitted")
    return ds, m
