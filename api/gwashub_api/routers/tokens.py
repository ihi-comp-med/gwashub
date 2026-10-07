"""Personal API tokens for the upload script: made, listed and revoked by their owner while signed in (never with a
token). A token acts as its owner, in one project, for uploading only (auth.upload_caller), for 30 days."""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api.auth import TOKEN_PREFIX, current_user, require_member, token_hash
from gwashub_api.db import get_db
from gwashub_api.models import ApiToken, User

router = APIRouter(prefix="/api", tags=["tokens"])
log = logging.getLogger(__name__)
LIFETIME = timedelta(days=30)


def token_view(t: ApiToken) -> dict:
    now = datetime.now(timezone.utc)
    expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    state = "revoked" if t.revoked_at else "expired" if expires < now else "active"
    return {"id": t.id, "name": t.name, "project_id": t.project_id, "prefix": t.prefix, "created_at": t.created_at,
            "expires_at": t.expires_at, "last_used_at": t.last_used_at, "revoked_at": t.revoked_at, "state": state}


@router.get("/tokens")
def my_tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [token_view(t) for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.id.desc()))]


class TokenNew(BaseModel):
    project_id: str
    name: str


@router.post("/tokens", status_code=201)
def make_token(body: TokenNew, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Any member of the project. The token is in the response once; only its hash is kept."""
    require_member(db, user, body.project_id)
    name = " ".join(body.name.split())
    if not name or len(name) > 100:
        raise HTTPException(422, "Give the token a name of up to 100 characters")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    t = ApiToken(user_id=user.id, project_id=body.project_id, name=name, token_hash=token_hash(token),
                 prefix=token[:10], expires_at=datetime.now(timezone.utc) + LIFETIME)
    db.add(t)
    db.commit()
    log.info("API token %s (%s) made by %s for project %s", t.id, t.prefix, user.email, t.project_id)
    return {**token_view(t), "token": token}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ApiToken, token_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "No such token")
    if not t.revoked_at:
        t.revoked_at = datetime.now(timezone.utc)
        db.commit()
        log.info("API token %s (%s) revoked by %s", t.id, t.prefix, user.email)
