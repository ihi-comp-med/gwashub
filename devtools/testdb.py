"""Empty a test database so the migrations can run on it from scratch (used by api/tests and devtools/test_db.sh)."""
from sqlalchemy import inspect, text


def reset_database(engine) -> None:
    """Drop every table, alembic_version included. Postgres: the whole public schema. MySQL: each table, with foreign
    key checks off for the drops (the tables reference each other)."""
    dialect = engine.dialect.name
    if dialect == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    elif dialect == "mysql":
        quote = engine.dialect.identifier_preparer.quote
        tables = inspect(engine).get_table_names()
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                for name in tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {quote(name)}"))
            finally:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    else:
        raise ValueError(f"reset_database supports Postgres and MySQL, not {dialect}")
