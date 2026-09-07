"""Event routes: feed, detail, and per-user event state.

Event detail returns the explanation, signals, evidence, score breakdown,
confidence and timeline together, so the frontend never has to assemble
intelligence from multiple calls.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import MarketEventRow, User
from app.intelligence.models import AttentionResult, Confidence, Severity, Signal
from app.providers.registry import DataMode
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.schemas.api import EventDetailOut, EventSummaryOut, FeedbackIn
from app.services import behavior
from app.services import events as events_service
from app.services import serializers
from app.services import watchlists as watchlists_service
from app.services.explanation import explain_event, score_rationale

router = APIRouter(prefix="/events", tags=["events"])


def _scope(mode: DataMode, scenario: str) -> str:
    return "live" if mode is DataMode.LIVE else f"replay:{scenario}"


def _rebuild_result(row: MarketEventRow) -> AttentionResult:
    """Reconstruct the engine result stored with an event.

    Explanations and the score breakdown are regenerated from the persisted
    engine output rather than re-scored, so what the user reads always matches
    what was actually computed at the time.
    """
    signals = [Signal.model_validate(s) for s in (row.supporting_signals or [])]
    return AttentionResult(
        symbol=row.instrument.symbol,
        timestamp=row.last_updated_at,
        objective_score=row.objective_score,
        personalization_adjustment=round(row.current_score - row.objective_score, 2),
        final_score=row.current_score,
        severity=Severity(row.severity),
        direction=row.direction,
        meaningful=True,
        signals=signals,
        breakdown=row.breakdown or {},
        evidence=row.evidence or [],
        confidence=Confidence(row.confidence),
        confidence_report=row.confidence_report
        or {
            "level": row.confidence,
            "independent_signals": 0,
            "families_available": 0,
            "families_considered": 6,
            "coverage": 0.0,
            "contradictions": 0,
            "reasons": [],
        },
    )


@router.get("", response_model=list[EventSummaryOut])
def list_events(
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> list[dict]:
    """The meaningful-event feed, ranked by significance then recency."""
    symbols = watchlists_service.watched_symbols(db, user)
    rows = events_service.events_for_symbols(
        db, symbols, scope=_scope(mode, scenario), limit=limit
    )
    states = events_service.states_for(db, user, [r.id for r in rows])
    dismissed = events_service.dismissed_event_ids(db, user)
    muted = behavior.muted_symbols(db, user)

    visible = [
        r for r in rows
        if (include_dismissed or r.id not in dismissed) and r.instrument.symbol not in muted
    ]
    order = {"Critical": 3, "High Attention": 2, "Worth Watching": 1, "Noise": 0}
    visible.sort(key=lambda r: (-order.get(r.severity, 0), -r.last_updated_at.timestamp()))
    return [serializers.event_summary(r, states.get(r.id)) for r in visible]


@router.get("/history", response_model=list[EventSummaryOut])
def event_history(
    day: date | None = Query(default=None),
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> list[dict]:
    """Historical events, optionally for a single trading session.

    Reviewed and dismissed events are included: they stay available as
    context, which is the whole reason dismissal does not delete them.
    """
    symbols = watchlists_service.watched_symbols(db, user)
    scope = _scope(mode, scenario)
    rows = (
        events_service.events_on_day(db, symbols, day, scope=scope)
        if day
        else events_service.events_for_symbols(db, symbols, scope=scope, limit=200)
    )
    states = events_service.states_for(db, user, [r.id for r in rows])
    return [serializers.event_summary(r, states.get(r.id)) for r in rows]


@router.get("/{event_id}", response_model=EventDetailOut)
def get_event(
    event_id: int,
    explain_with_llm: bool = Query(default=True),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Full event detail: explanation, signals, evidence, score, timeline."""
    row = events_service.get_event(db, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    symbols = set(watchlists_service.watched_symbols(db, user))
    if row.instrument.symbol not in symbols:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This event is not on one of your watchlists.",
        )

    result = _rebuild_result(row)
    explanation = explain_event(result, use_llm=explain_with_llm)
    rationale = score_rationale(result)

    state = events_service.mark_read(db, user, row.id)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.VIEW,
        instrument_id=row.instrument_id, event_id=row.id,
    )
    db.commit()

    summary = serializers.event_summary(row, state)
    summary.update(
        {
            "explanation": explanation.to_dict(),
            "evidence": row.evidence or [],
            "signals": rationale["signals"],
            "score_detail": rationale,
            "timeline": serializers.timeline_points(row),
            "market_context_note": serializers.market_context_note(row),
            "price": None,
        }
    )
    return summary


@router.post("/{event_id}/why")
def open_why(
    event_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Record a "Why it matters" expansion - a strong interest signal."""
    row = events_service.get_event(db, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    events_service.record_why_opened(db, user, event_id)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.WHY_OPENED,
        instrument_id=row.instrument_id, event_id=event_id,
    )
    db.commit()
    return {"recorded": True}


@router.post("/{event_id}/breakdown")
def open_breakdown(
    event_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Record a "How LUMEN calculated this" expansion."""
    row = events_service.get_event(db, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    events_service.record_breakdown_opened(db, user, event_id)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.BREAKDOWN_OPENED,
        instrument_id=row.instrument_id, event_id=event_id,
    )
    db.commit()
    return {"recorded": True}


@router.post("/{event_id}/review")
def review_event(
    event_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Acknowledge an event. It leaves the priority surface, not the record."""
    row = events_service.get_event(db, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    events_service.mark_reviewed(db, user, event_id)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.ACKNOWLEDGE,
        instrument_id=row.instrument_id, event_id=event_id,
    )
    db.commit()
    return {"reviewed": True}


@router.post("/{event_id}/dismiss")
def dismiss_event(
    event_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Dismiss: off the homepage, retained in history, future ranking lowered."""
    row = events_service.get_event(db, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    events_service.dismiss(db, user, event_id)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.DISMISS,
        instrument_id=row.instrument_id, event_id=event_id,
    )
    db.commit()
    return {"dismissed": True, "retained_in_history": True}


@router.post("/feedback")
def submit_feedback(
    body: FeedbackIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Explicit feedback. Only "more like this" is supported for now."""
    if body.feedback != "more_like_this":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only 'more_like_this' feedback is supported.",
        )

    row = events_service.get_event(db, body.event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    events_service.set_feedback(db, user, body.event_id, body.feedback)
    behavior.record_interaction(
        db, user, behavior.InteractionKind.MORE_LIKE_THIS,
        instrument_id=row.instrument_id, event_id=body.event_id,
    )
    db.commit()
    return {"recorded": True}
