"""Schema migrations (Alembic), packaged with RAGOps so `pip install` users can upgrade in place.

    ragops db upgrade     # apply pending migrations (also runs automatically at startup on PostgreSQL)
    ragops db current     # show the database revision
    ragops db history     # list revisions

Databases created by v0.4/v0.5 with `create_all` (no alembic_version table) are detected and stamped with the
matching revision first, then upgraded, so existing installs keep their data.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect

log = logging.getLogger("ragops.migrate")
HERE = Path(__file__).parent / "migrations"


def _config(engine):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%"))
    return cfg


def detect_legacy_revision(engine) -> str | None:
    """Revision matching a pre-migrations database, or None for an empty database."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "alembic_version" in tables or "projects" not in tables:
        return None
    if "organizations" in tables:
        return "0003_v06_orgs_sso"
    if "eval_runs" in tables:
        return "0002_v05_runs_vectors"
    return "0001_v04_baseline"


def upgrade(engine, revision: str = "head") -> str:
    from alembic import command

    cfg = _config(engine)
    legacy = detect_legacy_revision(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        if legacy:
            log.warning("database created without migrations; stamping %s before upgrading", legacy)
            command.stamp(cfg, legacy)
        command.upgrade(cfg, revision)
    return current(engine)


def current(engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_config_no_engine()).get_current_head()


def _config_no_engine():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    return cfg


def history() -> list[str]:
    from alembic.script import ScriptDirectory

    sd = ScriptDirectory.from_config(_config_no_engine())
    return [f"{r.revision}: {r.doc}" for r in reversed(list(sd.walk_revisions()))]
