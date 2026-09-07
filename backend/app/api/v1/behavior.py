"""Behaviour tracking routes.

Interactions are recorded here and aggregated into affinity, which becomes the
``behavioral_relevance`` input the engine caps. Nothing on this router can
change a score directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import User
from app.schemas.api import InteractionIn, MuteIn
from app.services import behavior as behavior_service
from app.services import catalog
from app.services import watchlists as watchlists_service

router = APIRouter(prefix="/behavior", tags=["behavior"])


@router.post("/interactions")
def record_interaction(
    body: InteractionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Record one interaction: view, click, why-open, dismissal, feedback."""
    instrument_id = None
    if body.symbol:
        instrument = catalog.get_by_symbol(db, body.symbol)
        if instrument is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol '{body.symbol}'.")
        instrument_id = instrument.id

    try:
        behavior_service.record_interaction(
            db, user, body.kind,
            instrument_id=instrument_id, event_id=body.event_id, context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()
    return {"recorded": True}


@router.get("/relevance")
def get_relevance(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Current per-symbol behavioural relevance, for transparency.

    Exposed so personalization is inspectable rather than a black box. The
    cap that limits its influence lives in the engine, and is reported here.
    """
    from app.intelligence.config import DEFAULT_CONFIG

    membership = watchlists_service.membership_settings(db, user)
    relevance = behavior_service.behavioral_relevance(db, user, membership)
    return {
        "relevance": {k: round(v, 2) for k, v in sorted(relevance.items())},
        "membership": membership,
        "max_score_adjustment": DEFAULT_CONFIG.personalization_max_adjustment,
        "min_objective_score_for_adjustment": (
            DEFAULT_CONFIG.personalization_min_objective_score
        ),
        "note": (
            "Behavioural relevance only reorders events that are already "
            "objectively significant. It cannot promote noise."
        ),
    }


@router.post("/mute")
def mute_stock(
    body: MuteIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Temporarily mute a stock. Always time-boxed."""
    instrument = catalog.get_by_symbol(db, body.symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{body.symbol}'.")

    mute = behavior_service.mute_instrument(db, user, instrument.id, body.hours)
    db.commit()
    return {
        "symbol": instrument.symbol,
        "muted_until": mute.muted_until.isoformat(),
        "hours": body.hours,
        "note": "Muted stocks are excluded from attention and alerts until this expires.",
    }


@router.delete("/mute/{symbol}")
def unmute_stock(
    symbol: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    instrument = catalog.get_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    behavior_service.unmute_instrument(db, user, instrument.id)
    db.commit()
    return {"symbol": instrument.symbol, "muted": False}


@router.get("/mutes")
def list_mutes(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    return {"muted": sorted(behavior_service.muted_symbols(db, user))}
