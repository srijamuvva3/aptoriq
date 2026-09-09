"""Database engine and session handling.

SQLite is the target for the classroom deployment, but everything goes through
SQLAlchemy so switching to PostgreSQL is a URL change. WAL mode matters here: a full
class submitting event batches concurrently will deadlock the default journal.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "etap.db"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    configured = os.getenv("ETAP_DATABASE_URL")
    if configured:
        return configured
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH}"


def build_engine(url: str | None = None) -> Engine:
    resolved = url or database_url()
    is_sqlite = resolved.startswith("sqlite")
    engine = create_engine(
        resolved,
        future=True,
        # SQLite's default check would reject the threadpool FastAPI runs sync routes on.
        connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {},
    )
    if is_sqlite:
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def configure(engine: Engine) -> None:
    """Point the module at a specific engine. Used by tests and by the CLI."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_all() -> None:
    from . import models  # noqa: F401  (registers mappers before create_all)

    Base.metadata.create_all(get_engine())


def session_scope() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
