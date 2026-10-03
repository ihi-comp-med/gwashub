"""Alembic environment: the database URL and models come from the app, not alembic.ini."""
from alembic import context

from gwashub_api import models  # noqa: F401 (registers the tables)
from gwashub_api.db import Base, engine

target_metadata = Base.metadata


def run():
    connection = context.config.attributes.get("connection")
    if connection is None:
        with engine().connect() as connection:
            _configure_and_run(connection)
    else:
        _configure_and_run(connection)


def _configure_and_run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run()
