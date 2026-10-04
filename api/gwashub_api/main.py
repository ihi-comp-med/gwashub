"""GWASHub API.

    cd gwashub && .venv/bin/uvicorn gwashub_api.main:app --app-dir api --reload --port 8000
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api import jobs
from gwashub_api.auth import COOKIE, set_session
from gwashub_api.config import settings
from gwashub_api.db import create_all, get_db
from gwashub_api.models import Membership, User
from gwashub_api.routers import cognito, datasets, meta, pipeline, projects


@asynccontextmanager
async def lifespan(app):
    create_all()
    jobs.recover()
    yield


app = FastAPI(title="GWASHub API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings().cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (projects.router, datasets.router, pipeline.router, meta.router, cognito.router):
    app.include_router(r)


class Login(BaseModel):
    email: str


def dev_mode() -> bool:
    return settings().auth_mode == "dev"


@app.get("/api/auth/mode")
def auth_mode():
    """Public: which sign-in form the website should show, and the environment banner."""
    return {"mode": settings().auth_mode, "banner": settings().banner}


@app.post("/api/auth/login")
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    """Dev mode only: sign in as an existing user by email. Cognito mode signs in through
    GET /api/auth/login (routers/cognito.py) instead."""
    if not dev_mode():
        raise HTTPException(404, "Not Found")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user:
        raise HTTPException(404, "No user with that email (local mode: see the seeded users on the login page)")
    set_session(response, user.id)
    projects_ = [m.project_id for m in db.scalars(select(Membership).where(Membership.user_id == user.id))]
    return {"id": user.id, "name": user.name, "email": user.email, "projects": projects_}


@app.post("/api/auth/logout")
def logout(response: Response):
    """Clears the session. In Cognito mode the website then goes to logout_url, which ends the
    Cognito session too (otherwise the next sign-in would skip the password)."""
    response.delete_cookie(COOKIE, path="/")
    out = {"ok": True}
    if settings().auth_mode == "cognito":
        out["logout_url"] = cognito.logout_url()
    return out


@app.get("/api/auth/users")
def dev_users(db: Session = Depends(get_db)):
    """Local mode only: list seeded users for the login page."""
    if not dev_mode():
        raise HTTPException(404, "Not Found")
    out = []
    for u in db.scalars(select(User).order_by(User.id)):
        roles = {m.project.short: m.role for m in db.scalars(select(Membership).where(Membership.user_id == u.id))}
        out.append({"email": u.email, "name": u.name, "institution": u.institution, "roles": roles})
    return out


@app.get("/api/steps/default")
def default_steps():
    """Public: the default step library and the default pipeline template (for the QC methods page)."""
    from gwashub_api.services import catalog, default_template, images, step_info
    return {"template": [s["step"] for s in default_template()], "steps": [step_info(sd) for sd in catalog().values()],
            "images": list(images().values())}


@app.get("/api/health")
def health():
    return {"ok": True}


if settings().web_dir:
    # Serve the built website from the same origin (single-container deployment). Registered last so
    # /api routes win; unknown paths fall back to the SPA shell so client-side routes work on reload.
    _web = os.path.realpath(settings().web_dir)

    @app.get("/{path:path}", include_in_schema=False)
    def website(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        f = os.path.realpath(os.path.join(_web, path))
        if f.startswith(_web + os.sep) and os.path.isfile(f):
            return FileResponse(f)
        shell = os.path.join(_web, "200.html")
        return FileResponse(shell if os.path.isfile(shell) else os.path.join(_web, "index.html"))
