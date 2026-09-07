"""LUMEN backend application entrypoint.

Local:

    .venv/Scripts/python -m uvicorn app.main:app --reload

Production (Render):

    uvicorn app.main:app --host 0.0.0.0 --port $PORT

The application starts with no credentials of any kind. Upstox, Groq and
Google are all optional; without a database the product routes return a clear
503 while ``/health`` and the deterministic demo keep working. Nothing here
raises on missing configuration - a misconfigured deploy must boot and be
diagnosable through ``/health`` rather than crash-loop.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, intelligence
from app.api.v1 import api_router
from app.config import get_settings
from app.db.session import get_session_factory
from app.services.polling import POLLER

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("lumen")

settings = get_settings()


def seed_reference_data() -> None:
    """Populate the instrument catalogue if a database is configured.

    Idempotent, and a no-op without a database - startup must never fail
    because infrastructure is absent.
    """
    factory = get_session_factory()
    if factory is None:
        logger.info("No DATABASE_URL configured; skipping instrument seeding.")
        return

    from app.services.catalog import seed_instruments

    session = factory()
    try:
        created = seed_instruments(session)
        session.commit()
        if created:
            logger.info("Seeded %s instruments.", created)
    except Exception:
        session.rollback()
        logger.exception("Instrument seeding failed; continuing without it.")
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start reference data and the market poller; stop the poller cleanly."""
    for warning in settings.production_warnings():
        logger.warning("PRODUCTION CONFIG: %s", warning)

    seed_reference_data()
    POLLER.start()
    try:
        yield
    finally:
        await POLLER.stop()


app = FastAPI(
    lifespan=lifespan,
    title="LUMEN",
    description=(
        "Market-change intelligence. Deterministic signal detection and scoring; "
        "explanations describe established facts and never give investment advice."
    ),
    version="1.0.0",
    debug=settings.debug,
)

# Bearer tokens are used rather than cookies, so credentials need not be
# shared cross-origin. Deployed frontend origins are added via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(intelligence.router)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """Service banner."""
    return {
        "service": settings.app_name,
        "status": "ok",
        "api": settings.api_prefix,
        "docs": "/docs",
    }
