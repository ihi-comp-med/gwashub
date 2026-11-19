"""Column types, engine options and the migration lock that let GWASHub run on MySQL as well as Postgres and SQLite."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mysql, postgresql, sqlite

from gwashub_api import config, db, migrate
from gwashub_api.dbtypes import UTCDateTime, column_default, migration_datetime

IST = timezone(timedelta(hours=5, minutes=30))


def test_utc_datetime_ddl_per_dialect():
    assert UTCDateTime().compile(dialect=mysql.dialect()) == "DATETIME(6)"
    assert UTCDateTime().compile(dialect=postgresql.dialect()) == "TIMESTAMP WITH TIME ZONE"


def test_utc_datetime_bind_converts_to_utc():
    v = datetime(2026, 1, 1, 12, 0, tzinfo=IST)
    assert UTCDateTime().process_bind_param(v, mysql.dialect()) == datetime(2026, 1, 1, 6, 30)
    assert UTCDateTime().process_bind_param(v, sqlite.dialect()) == datetime(2026, 1, 1, 6, 30)
    assert UTCDateTime().process_bind_param(v, postgresql.dialect()) == datetime(2026, 1, 1, 6, 30, tzinfo=timezone.utc)


def test_utc_datetime_bind_takes_naive_as_utc():
    assert UTCDateTime().process_bind_param(datetime(2026, 1, 1, 6, 30), sqlite.dialect()) == datetime(2026, 1, 1, 6, 30)
    assert UTCDateTime().process_bind_param(datetime(2026, 1, 1, 6, 30), postgresql.dialect()) == \
        datetime(2026, 1, 1, 6, 30, tzinfo=timezone.utc)


def test_utc_datetime_bind_keeps_none():
    assert UTCDateTime().process_bind_param(None, mysql.dialect()) is None
    assert UTCDateTime().process_result_value(None, mysql.dialect()) is None


def test_utc_datetime_result_is_aware_utc_with_microseconds():
    out = UTCDateTime().process_result_value(datetime(2026, 1, 1, 6, 30, 0, 123456), mysql.dialect())
    assert out == datetime(2026, 1, 1, 6, 30, 0, 123456, tzinfo=timezone.utc) and out.tzinfo is not None


def test_utc_datetime_result_converts_aware_to_utc():
    out = UTCDateTime().process_result_value(datetime(2026, 1, 1, 12, 0, tzinfo=IST), postgresql.dialect())
    assert out == datetime(2026, 1, 1, 6, 30, tzinfo=timezone.utc) and out.utcoffset() == timedelta(0)


def test_sqlite_round_trip_keeps_the_instant_microseconds_and_order():
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    events = sa.Table("events", meta, sa.Column("id", sa.Integer, primary_key=True), sa.Column("at", UTCDateTime()))
    meta.create_all(engine)
    earlier = datetime(2026, 1, 1, 12, 0, 0, 5, tzinfo=IST)  # 06:30:00.000005 UTC
    later = datetime(2026, 1, 1, 6, 30, 0, 10, tzinfo=timezone.utc)  # 5 microseconds after
    with engine.begin() as conn:
        conn.execute(events.insert(), [{"id": 1, "at": later}, {"id": 2, "at": earlier}, {"id": 3, "at": None}])
        rows = conn.execute(sa.select(events).where(events.c.at.is_not(None)).order_by(events.c.at)).all()
        assert conn.execute(sa.select(events.c.at).where(events.c.id == 3)).scalar() is None
    assert [r.id for r in rows] == [2, 1]
    assert [r.at for r in rows] == [earlier, later]
    assert all(r.at.tzinfo is not None and r.at.utcoffset() == timedelta(0) for r in rows)
    assert rows[0].at.microsecond == 5


def test_column_default_per_dialect():
    assert str(column_default("[]", "mysql")) == "('[]')"
    assert column_default("[]", "postgresql") == "[]" and column_default("", "sqlite") == ""


def test_migration_datetime_variants():
    assert migration_datetime().compile(dialect=mysql.dialect()) == "DATETIME(6)"
    assert migration_datetime().compile(dialect=postgresql.dialect()) == "TIMESTAMP WITH TIME ZONE"


@pytest.fixture
def fresh_engine(monkeypatch, tmp_path):
    """Settings and engine rebuilt from the environment a test sets, and put back afterwards."""
    monkeypatch.setenv("GWASHUB_DATA_DIR", str(tmp_path))

    def build():
        config.settings.cache_clear()
        db.reset_engine()
        return db.engine()
    yield build
    monkeypatch.undo()
    db.reset_engine()
    config.settings.cache_clear()


def test_engine_options_for_mysql(monkeypatch, fresh_engine):
    # Creating an engine does not connect, so no MySQL server is needed here.
    monkeypatch.setenv("GWASHUB_DB_URL", "mysql+pymysql://u:p@127.0.0.1/x")
    e = fresh_engine()
    assert e.dialect.name == "mysql"
    assert e.pool._pre_ping is True and e.pool._recycle == 1800


def test_engine_options_for_sqlite_unchanged(monkeypatch, fresh_engine, tmp_path):
    monkeypatch.setenv("GWASHUB_DB_URL", f"sqlite:///{tmp_path / 'x.db'}")
    e = fresh_engine()
    assert e.pool._pre_ping is False and e.pool._recycle == -1


class FakeConn:
    """Just enough of a Connection for the migration lock: records SQL, answers GET_LOCK with `lock_result`."""

    def __init__(self, dialect_name, lock_result=1):
        self.dialect = type("D", (), {"name": dialect_name})()
        self.lock_result = lock_result
        self.sql = []
        self.params = []

    def execute(self, statement, params=None):
        self.sql.append(str(statement))
        self.params.append(params)
        return self

    def scalar(self):
        return self.lock_result


def test_migration_lock_mysql():
    conn = FakeConn("mysql")
    migrate._acquire_lock(conn)
    assert "GET_LOCK" in conn.sql[0]
    assert conn.params[0] == {"name": migrate.LOCK_NAME, "timeout": migrate.LOCK_TIMEOUT}
    migrate._release_lock(conn)
    assert "RELEASE_LOCK" in conn.sql[1]
    assert conn.params[1] == {"name": migrate.LOCK_NAME}


def test_migration_lock_postgres():
    conn = FakeConn("postgresql")
    migrate._acquire_lock(conn)
    assert "pg_advisory_lock" in conn.sql[0] and conn.params[0] == {"id": migrate.LOCK_ID}
    migrate._release_lock(conn)
    assert "pg_advisory_unlock" in conn.sql[1] and conn.params[1] == {"id": migrate.LOCK_ID}


def test_migration_lock_timeout():
    conn = FakeConn("mysql", lock_result=0)
    with pytest.raises(RuntimeError, match="600 s"):
        migrate._acquire_lock(conn)
