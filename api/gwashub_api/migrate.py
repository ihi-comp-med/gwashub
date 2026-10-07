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
LOCK_ID = 7412001  # Postgres advisory lock: the API and the worker may start at the same time


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
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
        try:
            command.upgrade(config(conn), "head")
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
            conn.commit()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] == ["revision"]:
        command.revision(config(), message=argv[1] if len(argv) > 1 else "change", autogenerate=True)
    else:
        upgrade()
        print("Database is up to date")


if __name__ == "__main__":
    main()
