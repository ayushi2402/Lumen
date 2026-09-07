"""Database engine and session management.

The engine is created lazily and only when ``DATABASE_URL`` is configured.
Importing this module never opens a connection, so the intelligence tests and
``/health`` keep working with no database present - which is the whole point
at this stage.

Connecting to Postgres or Supabase later is a matter of setting
``DATABASE_URL``; nothing here needs to change.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when database access is attempted without ``DATABASE_URL`` set."""


@lru_cache
def get_engine() -> Engine | None:
    """Create the engine on first use. Returns ``None`` when unconfigured.

    ``pool_pre_ping`` is on because hosted Postgres (Supabase in particular)
    drops idle connections, and a background poller sitting idle overnight is
    exactly the case that would otherwise fail on the next market open.
    """
    settings = get_settings()
    if not settings.database_url:
        return None
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session] | None:
    engine = get_engine()
    if engine is None:
        return None
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def is_database_available() -> bool:
    """Whether a database is configured. Does not attempt to connect."""
    return get_engine() is not None


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session.

    Raises ``DatabaseNotConfiguredError`` rather than returning ``None`` so a
    missing database surfaces as a clear error at the point of use instead of
    an ``AttributeError`` somewhere deeper.
    """
    factory = get_session_factory()
    if factory is None:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set. Configure it to enable database-backed features."
        )
    session = factory()
    try:
        yield session
    finally:
        session.close()
