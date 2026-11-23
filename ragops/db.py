"""Database engine and session management (PostgreSQL in production, SQLite for local/tests)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ragops.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    import os

    from ragops import models, vectors  # noqa: F401  (register tables)

    engine = get_engine()
    vectors.prepare(engine)
    if engine.dialect.name == "postgresql" and os.environ.get("RAGOPS_AUTO_MIGRATE", "true").lower() != "false":
        # PostgreSQL: versioned Alembic migrations (upgrades databases from older releases in place)
        from ragops import migrate

        migrate.upgrade(engine)
        vectors.sync_with_schema(engine)
    else:
        # SQLite / local use: create tables directly
        models.Base.metadata.create_all(engine)
    vectors.create_indexes(engine)
    _adopt_orphan_projects(engine)


def _adopt_orphan_projects(engine) -> None:
    """Projects created before organisations existed join the 'default' organisation."""
    from sqlalchemy import select, update
    from sqlalchemy.orm import Session

    from ragops.models import Organization, Project

    with Session(engine) as s:
        if s.scalar(select(Project.id).where(Project.org_id.is_(None)).limit(1)) is None:
            return
        org = s.scalar(select(Organization).where(Organization.name == "default"))
        if org is None:
            org = Organization(name="default", settings={})
            s.add(org)
            s.flush()
        s.execute(update(Project).where(Project.org_id.is_(None)).values(org_id=org.id))
        s.commit()


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
