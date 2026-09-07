"""Shared test fixtures.

The whole suite runs against an in-memory SQLite database created from the same
``Base.metadata`` the PostgreSQL migration is generated from, so schema drift
between models and tests is impossible. No Postgres, no Redis, no network and
no credentials are required.
"""

from __future__ import annotations

import os

import pytest

# Tests must never touch the live network. Emptying the provider chain removes
# jugaad and yfinance, leaving the deterministic replay fallback - which is
# also exactly the "no market access" deployment case worth testing.
os.environ.setdefault("MARKET_PROVIDER_CHAIN", "")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.dependencies import get_db_session
from app.db.base import Base
from app.db.models import User
from app.main import app
from app.services import catalog


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings are cached; tests that tweak env need a clean read."""
    from app.config import get_settings
    from app.providers.registry import CANDLE_CACHE, QUOTE_CACHE

    get_settings.cache_clear()
    QUOTE_CACHE.clear()
    CANDLE_CACHE.clear()
    yield
    get_settings.cache_clear()
    QUOTE_CACHE.clear()
    CANDLE_CACHE.clear()


@pytest.fixture
def engine():
    """One shared in-memory connection so the schema survives across sessions."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db(engine) -> Session:
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    session = factory()
    catalog.seed_instruments(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(engine, db) -> TestClient:
    """Test client wired to the in-memory database.

    The dependency override is what lets the full API be exercised without
    infrastructure; every route resolves its session through this one seam.
    """

    def override_session():
        yield db

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def guest(client) -> dict:
    """An authenticated guest session with the curated demo watchlist."""
    response = client.post("/api/v1/auth/guest")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def auth_headers(guest) -> dict[str, str]:
    return {"Authorization": f"Bearer {guest['access_token']}"}


@pytest.fixture
def guest_user(db, guest) -> User:
    return db.get(User, guest["user"]["id"])
