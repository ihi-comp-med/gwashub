"""Database migrations (Alembic), used for Postgres. Local SQLite keeps using create_all.

    python -m gwashub_api.migrate                 # upgrade to the latest revision
    python -m gwashub_api.migrate revision "add x" # autogenerate a new revision from the models
"""
import logging
import os
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from gwashub_api.db import engine

MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
# The API and the worker may start at the same time: whichever takes the lock runs the migrations, the other waits.
LOCK_ID = 7412001  # Postgres advisory lock
LOCK_NAME = "gwashub_migrate"  # MySQL named lock
LOCK_TIMEOUT = 600  # seconds to wait for the MySQL lock
MYSQL_COLLATION = "utf8mb4_0900_as_cs"  # the collation a MySQL database is created with (docs/SHARED_HOST.md)


def config(connection=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade():
    # Whichever process takes the lock runs the migrations; make sure its "Running upgrade" lines reach the logs (the
    # worker configures logging itself, uvicorn doesn't).
    alembic_log = logging.getLogger("alembic.runtime.migration")
    if not logging.getLogger().handlers and not alembic_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        alembic_log.addHandler(handler)
        alembic_log.setLevel(logging.INFO)
    with engine().connect() as conn:
        _acquire_lock(conn)
        try:
            # Only once the lock is held: on MySQL the check's read fixes what this transaction sees, and it must see what
            # a migration that ran while this one waited has made.
            if conn.dialect.name == "mysql":
                _check_mysql_collation(conn)
            command.upgrade(config(conn), "head")
            conn.commit()
        finally:
            _release_lock(conn)
            conn.commit()


def _check_mysql_collation(conn) -> None:
    """GWASHub compares names case-sensitively, as Postgres does ("UKB" and "ukb" are two cohorts). On MySQL that holds
    only if the database was created with a case-sensitive utf8mb4 collation; the server's usual default
    (utf8mb4_0900_ai_ci) isn't one. Refuse to touch a database that isn't."""
    row = conn.execute(text("SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA "
                            "WHERE SCHEMA_NAME = DATABASE()")).first()
    charset, collation = row if row is not None else (None, None)
    if charset != "utf8mb4" or not collation or not collation.endswith(("_cs", "_bin")):
        raise RuntimeError(
            f"The MySQL database has character set {charset} and collation {collation}; GWASHub needs character set "
            f"utf8mb4 and the case-sensitive collation {MYSQL_COLLATION} (or another utf8mb4 collation ending in _cs or "
            f"_bin). Create the database with CHARACTER SET utf8mb4 COLLATE {MYSQL_COLLATION}.")


def _acquire_lock(conn) -> None:
    if conn.dialect.name == "mysql":
        got = conn.execute(text("SELECT GET_LOCK(:name, :timeout)"), {"name": LOCK_NAME, "timeout": LOCK_TIMEOUT}).scalar()
        if got != 1:  # 0 = timed out, NULL = error
            raise RuntimeError(f"Timed out after {LOCK_TIMEOUT} s waiting for the migration lock")
    else:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})


def _release_lock(conn) -> None:
    if conn.dialect.name == "mysql":
        conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
    else:
        conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] == ["revision"]:
        command.revision(config(), message=argv[1] if len(argv) > 1 else "change", autogenerate=True)
    else:
        upgrade()
        print("Database is up to date")


if __name__ == "__main__":
    main()
