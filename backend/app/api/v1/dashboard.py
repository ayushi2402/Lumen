"""Dashboard routes: the aggregated homepage payload."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import User
from app.providers.registry import DataMode
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.schemas.api import DashboardOut
from app.services import dashboard as dashboard_service
from app.services import digest as digest_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=0, ge=0, le=25),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Everything the homepage renders, in one call.

    ``mode`` is explicit: replay data is only ever returned when it is asked
    for, and the response always states which mode produced it.
    """
    payload = dashboard_service.build_dashboard(
        db, user, mode=mode, scenario_key=scenario, step=step
    )
    # Records the visit. Deliberately does NOT advance the digest baseline -
    # a page refresh must not destroy the digest the user came back to read.
    digest_service.mark_app_opened(db, user)
    db.commit()
    return payload


@router.post("/digest/reviewed")
def mark_digest_reviewed(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Advance the "since you were away" baseline. Explicit action only."""
    digest_service.mark_digest_reviewed(db, user)
    db.commit()
    baseline = digest_service.resolve_baseline(user)
    return {"baseline": baseline.to_dict()}


@router.post("/digest/baseline")
def set_manual_baseline(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """"Start from here" - pin the digest baseline to this moment."""
    baseline = digest_service.set_manual_baseline(db, user)
    db.commit()
    return {"baseline": baseline.to_dict()}


@router.get("/digest")
def get_digest(
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """The digest on its own, including day-by-day drilldown when relevant."""
    from app.services import watchlists as watchlists_service
    from app.services import events as events_service
    from app.services import serializers

    scope = "live" if mode is DataMode.LIVE else f"replay:{scenario}"
    symbols = watchlists_service.watched_symbols(db, user)
    digest = digest_service.build_digest(db, user, symbols, scope=scope)
    states = events_service.states_for(db, user, [r.id for r in digest.events])
    db.commit()

    return {
        "baseline": digest.baseline.to_dict(),
        "summary": digest.summary,
        "sessions_missed": digest.sessions_missed,
        "is_multi_session": digest.is_multi_session,
        "top_events": [
            serializers.event_summary(r, states.get(r.id)) for r in digest.top_events
        ],
        "remainder_count": digest.remainder_count,
        "events": [
            serializers.event_summary(r, states.get(r.id)) for r in digest.events
        ],
        "days": [
            {
                "session_date": day.session_date.isoformat(),
                "headline": day.headline,
                "top_symbols": day.top_symbols,
                "event_count": len(day.events),
                "events": [
                    serializers.event_summary(r, states.get(r.id)) for r in day.events
                ],
            }
            for day in digest.days
        ],
    }
