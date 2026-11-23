"""Alembic environment. Run through `ragops db upgrade` (or automatically at startup on PostgreSQL)."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from ragops.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.",
                                         poolclass=pool.NullPool)
        with connectable.connect() as conn:
            _run(conn)
    else:
        _run(connectable)


def _run(conn) -> None:
    context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=conn.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
