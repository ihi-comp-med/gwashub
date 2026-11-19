"""MySQL: the migrations build the models' schema, and timestamps, case-sensitive names and dropped connections behave.

Runs only when GWASHUB_TEST_DB_URL is a MySQL URL (devtools/test_db.sh mysql-8.0 or mysql-8.4 starts one).
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from devtools.testdb import reset_database

TEST_URL = os.environ.get("GWASHUB_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not TEST_URL.startswith("mysql"), reason="needs GWASHUB_TEST_DB_URL set to a MySQL URL")

IST = timezone(timedelta(hours=5, minutes=30))
ENV_KEYS = ("GWASHUB_DB_URL", "GWASHUB_DATA_DIR")


@pytest.fixture(scope="module", autouse=True)
def mysql_settings(tmp_path_factory):
    """Point the app at the test MySQL (test_api.py's fixture removes GWASHUB_DB_URL when it finishes), and undo it."""
    from gwashub_api import config, db
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    os.environ["GWASHUB_DB_URL"] = TEST_URL
    os.environ["GWASHUB_DATA_DIR"] = str(tmp_path_factory.mktemp("mysql") / "local")
    config.settings.cache_clear()
    db.reset_engine()
    yield
    db.reset_engine()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.settings.cache_clear()


@pytest.fixture
def migrated():
    """An empty database brought to the latest revision by the migrations."""
    from gwashub_api import db, migrate
    reset_database(db.engine())
    migrate.upgrade()


def make_project(session, pid="p1", **fields):
    from gwashub_api.models import Project
    session.add(Project(id=pid, name=pid, short=pid, **fields))
    session.flush()


def test_migrations_reach_models():
    from gwashub_api import db, migrate, models  # noqa: F401 (registers the tables)
    reset_database(db.engine())
    migrate.upgrade()
    with db.engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), db.Base.metadata)
    assert diff == []


def test_concurrent_migrations():
    from gwashub_api import db, migrate
    reset_database(db.engine())
    errors = []

    def run():
        try:
            migrate.upgrade()
        except Exception as e:  # noqa: BLE001 (reported below)
            errors.append(e)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not [t for t in threads if t.is_alive()], "a migration was still running after 300 s"
    assert errors == []
    head = ScriptDirectory.from_config(migrate.config()).get_current_head()
    with db.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [head]


def test_refuses_a_database_with_a_case_insensitive_collation():
    """A database made with the server's usual default (utf8mb4_0900_ai_ci) would treat "UKB" and "ukb" as the same name:
    the migrations must refuse it, naming the collation found and the one needed, and create nothing."""
    from gwashub_api import config, db, migrate
    other = "gwashub_test_ci"
    root = sa.create_engine(TEST_URL)
    try:
        with root.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {other}"))
            conn.execute(text(f"CREATE DATABASE {other} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
        os.environ["GWASHUB_DB_URL"] = sa.make_url(TEST_URL).set(database=other).render_as_string(hide_password=False)
        config.settings.cache_clear()
        db.reset_engine()
        with pytest.raises(RuntimeError, match="utf8mb4_0900_ai_ci") as e:
            migrate.upgrade()
        assert "utf8mb4_0900_as_cs" in str(e.value)
        with db.engine().connect() as conn:
            assert conn.execute(text("SHOW TABLES")).all() == []
    finally:
        os.environ["GWASHUB_DB_URL"] = TEST_URL
        config.settings.cache_clear()
        db.reset_engine()
        with root.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {other}"))
        root.dispose()


def test_datetime_round_trip_keeps_microseconds_and_utc(migrated):
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Project
    moment = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=IST)
    with SessionLocal() as s:
        make_project(s, created_at=moment)
        s.commit()
    with SessionLocal() as s:
        got = s.get(Project, "p1").created_at
    assert got == datetime(2026, 1, 1, 6, 30, 0, 123456, tzinfo=timezone.utc)
    assert got.utcoffset() == timedelta(0)


def test_names_differing_in_case_are_distinct(migrated):
    from gwashub_api.db import SessionLocal
    from gwashub_api.models import Cohort
    with SessionLocal() as s:
        make_project(s)
        s.add_all([Cohort(project_id="p1", name="UKB"), Cohort(project_id="p1", name="ukb")])
        s.commit()
        assert len(s.scalars(select(Cohort).where(Cohort.name == "UKB")).all()) == 1
        assert len(s.scalars(select(Cohort)).all()) == 2


def test_reconnects_after_server_drops_connection():
    from gwashub_api import db
    db.reset_engine()  # an empty pool: the one connection made below is the one the next session is handed
    with db.SessionLocal() as s:
        cid = s.execute(text("SELECT CONNECTION_ID()")).scalar()
    killer = sa.create_engine(TEST_URL)
    try:
        with killer.connect() as conn:
            conn.execute(text(f"KILL {int(cid)}"))
            for _ in range(50):  # KILL is not instant for a sleeping connection: wait until the server has let go of it
                if not conn.execute(text("SELECT 1 FROM information_schema.processlist WHERE id = :id"), {"id": cid}).first():
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"connection {cid} was still in the process list 5 s after KILL")
    finally:
        killer.dispose()
    with db.SessionLocal() as s:
        assert s.execute(text("SELECT 1")).scalar() == 1
