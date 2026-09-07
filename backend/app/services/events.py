"""Event persistence and evolution.

The grouping rules are **not** reimplemented here. This module reconstructs the
engine's ``MarketEvent`` from a database row, calls the engine's own
``decide_grouping`` / ``apply_observation`` / ``start_event``, and writes the
result back. The engine stays the single source of truth for when two
observations are the same story; the database only remembers what it decided.

That is what keeps "one evolving event per underlying event" true in
production and in tests at the same time: both run the same code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.events import (
    EventScorePoint,
    EventStatus,
    GroupingDecision,
    MarketEvent,
    apply_observation,
    decide_grouping,
    start_event,
)
from app.intelligence.explain import explain
from app.intelligence.models import Confidence, Direction, Severity, Signal
from app.market.calendar import session_date_for
from app.market.context import MoveClassification
from app.db.models import (
    EventTimelinePoint,
    Instrument,
    MarketEventRow,
    User,
    UserEventState,
    utcnow,
)
from app.services.pipeline import ScoredObservation

OPEN_STATUSES = (EventStatus.ACTIVE.value, EventStatus.ACKNOWLEDGED.value)


# ---------------------------------------------------------------------------
# Row <-> engine conversion
# ---------------------------------------------------------------------------


def _to_engine_event(row: MarketEventRow, symbol: str) -> MarketEvent:
    """Rebuild the engine's event object from a stored row."""
    signals = [Signal.model_validate(s) for s in (row.supporting_signals or [])]
    history = [
        EventScorePoint(
            timestamp=point.observed_at,
            score=point.score,
            severity=Severity(point.severity),
            direction=Direction(point.direction),
        )
        for point in row.timeline
    ]
    return MarketEvent(
        event_id=row.event_key,
        symbol=symbol,
        started_at=row.started_at,
        last_updated_at=row.last_updated_at,
        direction=Direction(row.direction),
        current_score=row.current_score,
        peak_score=row.peak_score,
        severity=Severity(row.severity),
        status=EventStatus(row.status),
        headline=row.headline,
        supporting_signals=signals,
        breakdown=row.breakdown or {},
        evidence=row.evidence or [],
        confidence=Confidence(row.confidence),
        observation_count=row.observation_count,
        history=history,
    )


def _write_engine_event(
    row: MarketEventRow, event: MarketEvent, scored: ScoredObservation
) -> None:
    """Copy engine state onto a row. The engine's values win, always."""
    row.event_key = event.event_id
    row.started_at = event.started_at
    row.last_updated_at = event.last_updated_at
    row.direction = event.direction.value
    row.current_score = event.current_score
    row.peak_score = event.peak_score
    row.objective_score = scored.result.objective_score
    row.severity = event.severity.value
    row.status = event.status.value
    row.confidence = event.confidence.value
    row.headline = event.headline
    row.breakdown = event.breakdown
    row.evidence = event.evidence
    row.supporting_signals = [s.model_dump(mode="json") for s in event.supporting_signals]
    row.confidence_report = scored.result.confidence_report.model_dump(mode="json")
    row.observation_count = event.observation_count
    row.is_market_wide = scored.classification in (
        MoveClassification.MARKET_WIDE,
        MoveClassification.SECTOR_WIDE,
    )
    # The deterministic explanation is written on every update so the fallback
    # is always current, independent of whether an LLM ever runs.
    row.deterministic_explanation = explain(scored.result)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def _latest_open_row(
    db: Session, instrument_id: int, scope: str
) -> MarketEventRow | None:
    return db.scalar(
        select(MarketEventRow)
        .where(
            MarketEventRow.instrument_id == instrument_id,
            MarketEventRow.scope == scope,
            MarketEventRow.status.in_(OPEN_STATUSES),
        )
        .options(selectinload(MarketEventRow.timeline))
        .order_by(MarketEventRow.last_updated_at.desc())
        .limit(1)
    )


def ingest_scored(
    db: Session,
    scored: ScoredObservation,
    instrument: Instrument,
    scope: str = "live",
    is_replay: bool = False,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> tuple[MarketEventRow | None, GroupingDecision]:
    """Fold one scored observation into the event store.

    Returns the affected row and the engine's grouping decision. A result that
    is not meaningful and matches no open event produces no row at all - noise
    is not recorded as an event.
    """
    existing_row = _latest_open_row(db, instrument.id, scope)
    existing_event = (
        _to_engine_event(existing_row, instrument.symbol) if existing_row else None
    )

    decision = decide_grouping(existing_event, scored.result, config)

    if decision is GroupingDecision.CONTINUE and existing_row and existing_event:
        updated = apply_observation(existing_event, scored.result, config)
        _write_engine_event(existing_row, updated, scored)
        _append_timeline(db, existing_row, scored)
        db.flush()
        return existing_row, decision

    if not scored.result.meaningful:
        return None, decision

    created = start_event(scored.result, config)

    # Event keys are deterministic (symbol + start time), so the same
    # observation scored twice - a replay step and a dashboard refresh at the
    # same virtual instant, or a re-run after an event was resolved - yields
    # the same key. Update the existing row instead of inserting a duplicate,
    # which would violate the (event_key, scope) uniqueness constraint.
    duplicate = db.scalar(
        select(MarketEventRow)
        .where(
            MarketEventRow.event_key == created.event_id,
            MarketEventRow.scope == scope,
        )
        .options(selectinload(MarketEventRow.timeline))
    )
    if duplicate is not None:
        reopened = apply_observation(
            _to_engine_event(duplicate, instrument.symbol), scored.result, config
        )
        _write_engine_event(duplicate, reopened, scored)
        _append_timeline(db, duplicate, scored)
        db.flush()
        return duplicate, decision
    row = MarketEventRow(
        event_key=created.event_id,
        scope=scope,
        instrument_id=instrument.id,
        session_date=session_date_for(scored.result.timestamp),
        is_replay=is_replay,
        # Placeholders immediately overwritten by _write_engine_event.
        started_at=created.started_at,
        last_updated_at=created.last_updated_at,
        direction=created.direction.value,
        current_score=created.current_score,
        peak_score=created.peak_score,
        objective_score=scored.result.objective_score,
        severity=created.severity.value,
        status=created.status.value,
        confidence=created.confidence.value,
        headline=created.headline,
    )
    db.add(row)
    _write_engine_event(row, created, scored)
    db.flush()
    _append_timeline(db, row, scored)
    db.flush()
    return row, decision


def _append_timeline(
    db: Session, row: MarketEventRow, scored: ScoredObservation
) -> None:
    """Record one step of the event's evolution, avoiding duplicate stamps."""
    already = any(p.observed_at == scored.result.timestamp for p in row.timeline)
    if already:
        return
    db.add(
        EventTimelinePoint(
            event_id=row.id,
            observed_at=scored.result.timestamp,
            score=scored.result.final_score,
            severity=scored.result.severity.value,
            direction=scored.result.direction.value,
            price=scored.quote.last_price,
        )
    )


def expire_stale(
    db: Session, scope: str = "live", now: datetime | None = None,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> int:
    """Age out events that stopped updating. Staleness is not deletion."""
    now = now or utcnow()
    cutoff = now - timedelta(minutes=config.event_stale_after_minutes)
    rows = db.scalars(
        select(MarketEventRow).where(
            MarketEventRow.scope == scope,
            MarketEventRow.status == EventStatus.ACTIVE.value,
            MarketEventRow.last_updated_at < cutoff,
        )
    ).all()
    for row in rows:
        row.status = EventStatus.STALE.value
    db.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def events_for_symbols(
    db: Session,
    symbols: list[str],
    scope: str = "live",
    since: datetime | None = None,
    limit: int = 100,
) -> list[MarketEventRow]:
    """Events for a set of symbols, most recently updated first."""
    if not symbols:
        return []
    statement = (
        select(MarketEventRow)
        .join(Instrument, Instrument.id == MarketEventRow.instrument_id)
        .where(Instrument.symbol.in_(symbols), MarketEventRow.scope == scope)
        .options(
            selectinload(MarketEventRow.timeline),
            selectinload(MarketEventRow.instrument),
        )
        .order_by(MarketEventRow.last_updated_at.desc())
        .limit(limit)
    )
    if since is not None:
        statement = statement.where(MarketEventRow.last_updated_at >= since)
    return list(db.scalars(statement).all())


def events_on_day(
    db: Session, symbols: list[str], day: date, scope: str = "live"
) -> list[MarketEventRow]:
    """Events belonging to one trading session, for day-by-day drilldown."""
    if not symbols:
        return []
    return list(
        db.scalars(
            select(MarketEventRow)
            .join(Instrument, Instrument.id == MarketEventRow.instrument_id)
            .where(
                Instrument.symbol.in_(symbols),
                MarketEventRow.scope == scope,
                MarketEventRow.session_date == day,
            )
            .options(
                selectinload(MarketEventRow.timeline),
                selectinload(MarketEventRow.instrument),
            )
            .order_by(MarketEventRow.current_score.desc())
        ).all()
    )


def get_event(db: Session, event_id: int, scope: str | None = None) -> MarketEventRow | None:
    statement = (
        select(MarketEventRow)
        .where(MarketEventRow.id == event_id)
        .options(
            selectinload(MarketEventRow.timeline),
            selectinload(MarketEventRow.instrument),
        )
    )
    if scope is not None:
        statement = statement.where(MarketEventRow.scope == scope)
    return db.scalar(statement)


# ---------------------------------------------------------------------------
# Per-user state
# ---------------------------------------------------------------------------


def get_or_create_state(db: Session, user: User, event_id: int) -> UserEventState:
    state = db.scalar(
        select(UserEventState).where(
            UserEventState.user_id == user.id, UserEventState.event_id == event_id
        )
    )
    if state is None:
        state = UserEventState(user_id=user.id, event_id=event_id)
        db.add(state)
        db.flush()
    return state


def mark_read(db: Session, user: User, event_id: int) -> UserEventState:
    state = get_or_create_state(db, user, event_id)
    state.read_at = state.read_at or utcnow()
    db.flush()
    return state


def mark_reviewed(db: Session, user: User, event_id: int) -> UserEventState:
    """Acknowledge an event. It leaves the priority surface, not the record."""
    state = get_or_create_state(db, user, event_id)
    now = utcnow()
    state.read_at = state.read_at or now
    state.reviewed_at = now
    db.flush()
    return state


def dismiss(db: Session, user: User, event_id: int) -> UserEventState:
    """Dismiss an event: off the homepage, still in history, ranking lowered."""
    state = get_or_create_state(db, user, event_id)
    state.dismissed_at = utcnow()
    db.flush()
    return state


def record_why_opened(db: Session, user: User, event_id: int) -> UserEventState:
    state = get_or_create_state(db, user, event_id)
    state.why_opened_count += 1
    state.read_at = state.read_at or utcnow()
    db.flush()
    return state


def record_breakdown_opened(db: Session, user: User, event_id: int) -> UserEventState:
    state = get_or_create_state(db, user, event_id)
    state.breakdown_opened_count += 1
    db.flush()
    return state


def set_feedback(db: Session, user: User, event_id: int, feedback: str) -> UserEventState:
    state = get_or_create_state(db, user, event_id)
    state.feedback = feedback
    db.flush()
    return state


def states_for(db: Session, user: User, event_ids: list[int]) -> dict[int, UserEventState]:
    if not event_ids:
        return {}
    rows = db.scalars(
        select(UserEventState).where(
            UserEventState.user_id == user.id, UserEventState.event_id.in_(event_ids)
        )
    ).all()
    return {row.event_id: row for row in rows}


def dismissed_event_ids(db: Session, user: User) -> set[int]:
    rows = db.scalars(
        select(UserEventState.event_id).where(
            UserEventState.user_id == user.id, UserEventState.dismissed_at.is_not(None)
        )
    ).all()
    return set(rows)
