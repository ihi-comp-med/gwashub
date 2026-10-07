"""The Validation page: each test in validation/<test>/ (test.yaml, result.json, plot.json) for anyone signed in.

Results are files committed with the code, so the page always shows the deployed commit's validation."""
import json
import os

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from gwashub_api.auth import current_user
from gwashub_api.config import settings
from gwashub_api.models import User

router = APIRouter(prefix="/api", tags=["validation"])

# Files of a test anyone can download from the page (the scripts and what they recorded).
DOWNLOADS = ("README.md", "test.yaml", "1_prepare.R", "2_reference.R", "3_compare.R", "site_run.yaml", "result.json",
             "manifest.tsv")


def _tests() -> list[str]:
    root = settings().validation_dir
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if not d.startswith(("_", ".")) and os.path.isfile(os.path.join(root, d, "test.yaml")))


def _read_json(path: str):
    with open(path) as f:
        return json.load(f)


def _test_view(name: str) -> dict:
    d = os.path.join(settings().validation_dir, name)
    with open(os.path.join(d, "test.yaml")) as f:
        cfg = yaml.safe_load(f)
    res = _read_json(os.path.join(d, "result.json")) if os.path.isfile(os.path.join(d, "result.json")) else {}
    return {"test": name, "title": cfg.get("title", name), "claim": cfg.get("claim", ""),
            "status": res.get("status", "not_run"), "run_date": res.get("run_date"), "versions": res.get("versions") or {},
            "site_run": res.get("site_run") or {}, "checks": res.get("checks") or [], "notes": res.get("notes") or [],
            "archive": res.get("archive") or cfg.get("archive"), "inputs": cfg.get("inputs") or [],
            "has_plot": os.path.isfile(os.path.join(d, "plot.json")),
            "files": [f for f in DOWNLOADS if os.path.isfile(os.path.join(d, f))]}


@router.get("/validation")
def list_validation(user: User = Depends(current_user)):
    return [_test_view(t) for t in _tests()]


def _test_dir(name: str) -> str:
    if name not in _tests():
        raise HTTPException(404, "No such validation test")
    return os.path.join(settings().validation_dir, name)


@router.get("/validation/{name}/plot")
def validation_plot(name: str, user: User = Depends(current_user)):
    path = os.path.join(_test_dir(name), "plot.json")
    if not os.path.isfile(path):
        raise HTTPException(404, "This test has no plot yet")
    return _read_json(path)


@router.get("/validation/{name}/files/{file}")
def validation_file(name: str, file: str, user: User = Depends(current_user)):
    path = os.path.join(_test_dir(name), file)
    if file not in DOWNLOADS or not os.path.isfile(path):
        raise HTTPException(404, "No such file")
    return FileResponse(path, filename=f"{name}_{file}", media_type="text/plain")
