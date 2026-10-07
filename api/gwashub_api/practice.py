"""The Practice project: where people learn GWASHub with the guided tours (web/app/utils/tours.ts), on synthetic files
(practice/ at the repo root). Every user is a reviewer in it, so every tour works there; its data is shared with every
user and may be deleted at any time. Turned off with GWASHUB_PRACTICE=0."""
import json
import os

from sqlalchemy.orm import Session

from gwashub_api import services
from gwashub_api.auth import membership
from gwashub_api.config import settings
from gwashub_api.models import Membership, Project, User

PROJECT_ID = "practice"
FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "practice")
DESCRIPTION = ("For learning GWASHub with the guided tours (Help menu, top right). Synthetic data only: everything here is "
               "shared with every user, and may be deleted at any time. Everyone is a reviewer here.")


def files() -> list[dict]:
    with open(os.path.join(FOLDER, "files.json")) as f:
        return json.load(f)


def file_path(name: str) -> str | None:
    """The path of a practice file by name (only those listed in files.json)."""
    return os.path.join(FOLDER, name) if name in {f["file"] for f in files()} else None


def ensure_project(db: Session):
    """Create the Practice project if it's switched on and missing (it is recreated if deleted)."""
    if not settings().practice or db.get(Project, PROJECT_ID):
        return
    p = Project(id=PROJECT_ID, name="Practice project", short="Practice", description=DESCRIPTION,
                phenotypes=["Heart failure"], phenotype_descriptions={"Heart failure": "All-cause heart failure (synthetic)"},
                phenotype_types={}, ancestries=["EUR", "AFR", "EAS", "SAS", "AMR"], sexes=["Combined", "Female", "Male"])
    db.add(p)
    db.flush()
    services.ensure_project_pipeline(db, p)
    db.commit()


def ensure_member(db: Session, user: User):
    """Every user is a reviewer in the Practice project."""
    if not settings().practice or not db.get(Project, PROJECT_ID) or membership(db, user, PROJECT_ID):
        return
    db.add(Membership(user_id=user.id, project_id=PROJECT_ID, role="reviewer"))
    db.commit()
