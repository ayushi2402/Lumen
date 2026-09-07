"""Health endpoint. Transport only - no business logic.

Must answer without Upstox, Google, Groq, market data or even a database, so
it is usable as a platform health check on a cold instance. It reports
capability *booleans* and never a credential value.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db.session import is_database_available
from app.services.polling import POLLER

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database_configured: bool
    capabilities: dict[str, bool]
    market_provider_chain: list[str]
    replay_available: bool
    background_polling: dict
    warnings: list[str]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus configuration state.

    Deliberately reports ``ok`` even when optional integrations are missing:
    the service genuinely is healthy without them, and a platform health check
    must not restart a working instance because Groq is unconfigured. Real
    misconfiguration surfaces through ``warnings``.
    """
    settings: Settings = get_settings()
    chain = [c.strip() for c in settings.market_provider_chain.split(",") if c.strip()]

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version="1.0.0",
        environment=settings.environment,
        database_configured=is_database_available(),
        capabilities=settings.capability_report(),
        market_provider_chain=chain,
        # Replay always works, which is why the demo needs no market access.
        replay_available=True,
        background_polling=POLLER.status(),
        warnings=settings.production_warnings(),
    )
