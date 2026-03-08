from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(settings.database_url, echo=False, pool_pre_ping=True)


def init_db() -> None:
    if _apply_sql_schema_if_available():
        return
    _ensure_default_schema()
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def _ensure_default_schema() -> None:
    backend = engine.url.get_backend_name()
    if not backend.startswith("postgresql"):
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS public"))
        conn.execute(text("SET search_path TO public"))


def _apply_sql_schema_if_available() -> bool:
    backend = engine.url.get_backend_name()
    if not backend.startswith("postgresql"):
        return False

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    if not schema_path.exists():
        return False

    sql_script = schema_path.read_text(encoding="utf-8")
    _ensure_default_schema()
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        try:
            cursor.execute(sql_script)
        finally:
            cursor.close()
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
    return True
