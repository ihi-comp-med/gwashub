"""Contact person split into Contact name and Contact email

Revision ID: c4e9a1f7b2d3
Revises: b8f4c2d6e913
Create Date: 2026-10-11 19:30:00.000000

The cohort field contact_person ("Name and email" in one box) becomes contact_name and contact_email. Existing values
are split: the first email address found goes to contact_email and the rest, tidied, to contact_name. Projects that
showed or required contact_person show or require both new fields.
"""
import json
import re

from alembic import op
import sqlalchemy as sa


revision = 'c4e9a1f7b2d3'
down_revision = 'b8f4c2d6e913'
branch_labels = None
depends_on = None

EMAIL = re.compile(r"[^@\s<>(),;:\"']+@[^@\s<>(),;:\"']+\.[^@\s<>(),;:\"']+")


def split_contact(value: str) -> dict:
    """{"contact_name", "contact_email"} from a free-text contact, either missing when absent."""
    text = str(value or "").strip()
    m = EMAIL.search(text)
    email = m.group(0).rstrip(".") if m else None
    name = text.replace(m.group(0), " ") if m else text
    name = re.sub(r"[<>()\[\],;:|/]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -–.") or None
    return {k: v for k, v in (("contact_name", name), ("contact_email", email)) if v}


def _load(v):
    return json.loads(v) if isinstance(v, str) else v


def _move(conn, table, column, forward):
    for row_id, raw in conn.execute(sa.text(f"SELECT id, {column} FROM {table}")):
        meta = _load(raw)
        if not isinstance(meta, dict):
            continue
        if forward and "contact_person" in meta:
            meta = {**{k: v for k, v in meta.items() if k != "contact_person"}, **split_contact(meta["contact_person"])}
        elif not forward and ("contact_name" in meta or "contact_email" in meta):
            joined = " ".join(p for p in (meta.get("contact_name"), meta.get("contact_email") and f"<{meta['contact_email']}>") if p)
            meta = {k: v for k, v in meta.items() if k not in ("contact_name", "contact_email")}
            if joined:
                meta["contact_person"] = joined
        else:
            continue
        conn.execute(sa.text(f"UPDATE {table} SET {column} = :m WHERE id = :id"), {"m": json.dumps(meta), "id": row_id})


def _config(conn, forward):
    old, new = ["contact_person"], ["contact_name", "contact_email"]
    src, dst = (old, new) if forward else (new, old)
    for row_id, raw in conn.execute(sa.text("SELECT id, metadata_config FROM project WHERE metadata_config IS NOT NULL")):
        cfg = _load(raw)
        if not isinstance(cfg, dict):
            continue
        changed = False
        for part in ("shown", "required"):
            keys = list(cfg.get(part) or [])
            if any(k in keys for k in src):
                at = min(keys.index(k) for k in src if k in keys)
                keys = [k for k in keys if k not in src]
                keys[at:at] = [k for k in dst if k not in keys]
                cfg[part] = keys
                changed = True
        if changed:
            conn.execute(sa.text("UPDATE project SET metadata_config = :c WHERE id = :id"), {"c": json.dumps(cfg), "id": row_id})


def upgrade():
    conn = op.get_bind()
    _move(conn, "cohort", "meta", True)
    _move(conn, "dataset", "study_meta", True)
    _config(conn, True)


def downgrade():
    conn = op.get_bind()
    _move(conn, "cohort", "meta", False)
    _move(conn, "dataset", "study_meta", False)
    _config(conn, False)
