"""Column types and helpers that make one set of models and migrations work on SQLite, Postgres and MySQL.

MySQL has no time zone in its DATETIME and rounds to whole seconds unless asked for microseconds, so every timestamp is
stored as UTC (DATETIME(6) on MySQL) and read back as an aware UTC datetime on all three databases.
"""
from datetime import timezone

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.types import TypeDecorator

# Dialects that store a bare date and time: the value is converted to UTC and the zone dropped on the way in.
_NAIVE_DIALECTS = ("mysql", "sqlite")


class UTCDateTime(TypeDecorator):
    """A moment in time. Python side: an aware datetime, always UTC. Naive values going in are taken as UTC."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(sa.DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name in _NAIVE_DIALECTS else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def migration_datetime() -> sa.types.TypeEngine:
    """The timestamp column type for Alembic migrations (which must not import the models)."""
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def column_default(value: str, dialect_name: str) -> str | sa.TextClause:
    """A `server_default` for a text column. MySQL 8 only allows a default on TEXT and JSON columns as an expression,
    so it needs parentheses; the other databases take the plain value."""
    return sa.text(f"('{value}')") if dialect_name == "mysql" else value
