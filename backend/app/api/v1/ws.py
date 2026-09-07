"""Realtime updates over WebSocket.

The rule this implements: **prices may update continuously, intelligence may
not.** Re-scoring on every tick would be wasteful, would make the UI flicker
between severity bands, and - once Groq is wired in - would mean an LLM call
per tick. So the two streams are decoupled:

* ``price`` frames go out on a short interval.
* ``intelligence`` frames go out only when a recompute actually changes event
  state, and never more often than ``intelligence_recompute_seconds``.

A fingerprint of the event set is compared between recomputes; identical state
sends nothing. The frontend therefore updates prices smoothly and only re-renders
attention cards when something genuinely changed.

Browsers cannot set headers on a WebSocket handshake, so the session token is
passed as a query parameter and validated with the same code path as the REST
dependency.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.auth.tokens import SessionTokenError, decode_token
from app.config import get_settings
from app.db.models import User
from app.db.session import get_session_factory
from app.providers.registry import DataMode, select_provider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.services import behavior, dashboard as dashboard_service
from app.services import events as events_service
from app.services import serializers
from app.services import watchlists as watchlists_service
from app.services.pipeline import run_cycle

router = APIRouter(tags=["realtime"])

CLOSE_UNAUTHORIZED = 4401
CLOSE_UNAVAILABLE = 4503


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _send(websocket: WebSocket, kind: str, payload: dict) -> None:
    await websocket.send_text(
        json.dumps({"type": kind, **payload}, default=_json_default)
    )


def _fingerprint(rows: list) -> str:
    """Identity of the current event state.

    Includes score and status so an escalation or an acknowledgement counts as
    a change, but excludes timestamps so an unchanged re-poll does not.
    """
    return "|".join(
        f"{r.id}:{r.severity}:{round(r.current_score, 1)}:{r.status}"
        for r in sorted(rows, key=lambda r: r.id)
    )


@router.websocket("/ws")
async def realtime(
    websocket: WebSocket,
    token: str = Query(...),
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=0, ge=0, le=25),
) -> None:
    """Stream prices continuously and intelligence only when it changes."""
    await websocket.accept()
    settings = get_settings()

    try:
        claims = decode_token(token)
    except SessionTokenError as exc:
        await _send(websocket, "error", {"message": str(exc)})
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    factory = get_session_factory()
    if factory is None:
        await _send(
            websocket, "error", {"message": "No database configured."}
        )
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    db = factory()
    try:
        user = db.get(User, claims.user_id)
        if user is None:
            await _send(websocket, "error", {"message": "Unknown user."})
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return

        symbols = watchlists_service.watched_symbols(db, user)
        baselines = dashboard_service.baselines_for(symbols, db)
        selection = select_provider(mode, scenario, step, settings)
        scope = "live" if mode is DataMode.LIVE else f"replay:{scenario}"

        await _send(
            websocket,
            "connected",
            {
                "symbols": symbols,
                "data_mode": selection.to_dict(),
                "price_interval_seconds": settings.ws_price_broadcast_seconds,
                "intelligence_interval_seconds": settings.intelligence_recompute_seconds,
            },
        )
        if not baselines:
            await _send(
                websocket, "warning", {"message": "No supported instruments to stream."}
            )

        last_fingerprint = ""
        ticks_since_intelligence = 0.0

        while True:
            cycle = run_cycle(selection.provider, baselines, include_intraday=False)

            await _send(
                websocket,
                "price",
                {
                    "observed_at": cycle.observed_at,
                    "is_replay": cycle.is_replay,
                    "quotes": {
                        s.symbol: serializers.price_payload(s.quote) for s in cycle.scored
                    },
                },
            )

            ticks_since_intelligence += settings.ws_price_broadcast_seconds
            if ticks_since_intelligence >= settings.intelligence_recompute_seconds:
                ticks_since_intelligence = 0.0

                rows = events_service.events_for_symbols(db, symbols, scope=scope, limit=25)
                fingerprint = _fingerprint(rows)

                # Only speak when something actually changed.
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    muted = behavior.muted_symbols(db, user)
                    states = events_service.states_for(db, user, [r.id for r in rows])
                    dismissed = events_service.dismissed_event_ids(db, user)
                    visible = [
                        r for r in rows
                        if r.id not in dismissed and r.instrument.symbol not in muted
                    ]
                    await _send(
                        websocket,
                        "intelligence",
                        {
                            "observed_at": cycle.observed_at,
                            "market": cycle.market.to_dict(),
                            "events": [
                                serializers.event_summary(r, states.get(r.id))
                                for r in visible
                            ],
                        },
                    )

            await asyncio.sleep(settings.ws_price_broadcast_seconds)

    except WebSocketDisconnect:
        return
    finally:
        db.close()
