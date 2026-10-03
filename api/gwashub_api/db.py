import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from gwashub_api.config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        s = settings()
        os.makedirs(s.data_dir, exist_ok=True)
        _engine = create_engine(s.db_url, connect_args={"check_same_thread": False} if s.db_url.startswith("sqlite") else {})
        if s.db_url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragmas(conn, _):
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine():
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = _Session = None


def SessionLocal():
    engine()
    return _Session()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all():
    """Make the schema current. SQLite (local dev): create tables and add new columns. Postgres:
    Alembic migrations (api/migrations)."""
    from gwashub_api import models  # noqa: F401
    if not settings().is_sqlite:
        from gwashub_api import migrate
        migrate.upgrade()
        return
    Base.metadata.create_all(engine())
    add_missing_columns()


def add_missing_columns():
    """Tiny dev-only migration: add columns that were added to the models after the DB was created
    (with the column's default). Production will use Alembic."""
    import json

    from sqlalchemy import inspect, text
    eng = engine()
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(eng.dialect)}'))
                default = col.default.arg if col.default is not None else None
                if callable(default):
                    default = default(None)
                if default is not None:
                    conn.execute(text(f'UPDATE "{table.name}" SET "{col.name}" = :v'), {"v": json.dumps(default) if isinstance(default, (list, dict)) else default})
