"""API smoke tests.

These run with no database, no Upstox token and no Groq key - which is the
property being tested as much as the response bodies themselves.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "lumen-backend"


def test_health_reports_capabilities_without_leaking_credentials() -> None:
    body = client.get("/health").json()
    capabilities = body["capabilities"]
    # New integrations may be added; these must always be reported.
    assert {"database", "redis", "upstox", "groq"} <= set(capabilities)
    # Capability flags must be booleans - never the credential values.
    assert all(isinstance(v, bool) for v in capabilities.values())


def test_app_starts_without_database_configured() -> None:
    """The engine and health endpoint must not depend on Postgres."""
    assert client.get("/health").json()["database_configured"] in {True, False}


def test_root_banner() -> None:
    assert client.get("/").json()["status"] == "ok"


def test_intelligence_demo_returns_ranked_analyses() -> None:
    response = client.get("/intelligence/demo")
    assert response.status_code == 200
    body = response.json()

    assert body["analyses"], "demo must return scored scenarios"
    scores = [a["final_score"] for a in body["analyses"]]
    assert scores == sorted(scores, reverse=True), "analyses must be ranked"

    assert len(body["needs_attention"]) <= 3, "homepage surfaces at most 3"
    assert "not investment advice" in body["disclaimer"]


def test_demo_reports_one_evolving_event() -> None:
    body = client.get("/intelligence/demo").json()
    evolving = body["evolving_event_scenario"]
    assert evolving["observations"] == 3
    assert evolving["events_created"] == 1


def test_demo_exposes_unavailable_signals() -> None:
    """Missing data must be reported, not hidden."""
    body = client.get("/intelligence/demo").json()
    adaniports = next(a for a in body["analyses"] if a["symbol"] == "ADANIPORTS")
    unavailable = {s["type"] for s in adaniports["unavailable_signals"]}
    assert "volatility_anomaly" in unavailable
