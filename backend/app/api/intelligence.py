"""Development endpoints for the intelligence engine.

Transport only - all logic lives in ``app.services.demo`` and
``app.intelligence``. This router is explicitly a development aid and is not
connected to live market data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.demo import run_demo

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.get("/demo")
def intelligence_demo() -> dict[str, Any]:
    """Run the deterministic engine against controlled fixture scenarios.

    Development and testing only. Every number in the response is derived
    from synthetic fixtures, never from a market feed.
    """
    return run_demo()
