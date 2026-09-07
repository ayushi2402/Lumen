"""Alembic environment.

The database URL is read from the application settings (``DATABASE_URL``)
rather than from ``alembic.ini``, so a connection string with credentials
never has to be written into a committed file.

No ORM models exist yet - ``target_metadata`` is wired to the shared
declarative Base so autogenerate works as soon as the first model lands.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base

# Importing the models registers them on Base.metadata so --autogenerate can
# see them. Without this import autogenerate would emit an empty migration.
import app.db.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve the database URL, failing with a clear message when unset."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in .env before running "
            "Alembic. The application and the intelligence engine run without "
            "it; only migrations require a database."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL without connecting to a database."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
