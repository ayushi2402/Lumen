"""Event grouping and evolution.

A stock that gaps down, falls hard, trades at four times normal volume and
underperforms its index has had **one thing happen to it**, not four. This
module turns a stream of scored observations into a small set of evolving
events, each carrying its signals as supporting evidence.

The two jobs here are grouping (many signals -> one event) and evolution
(many observations of the same move -> one event that updates, rather than a
new event every polling cycle).

All functions are pure. Events are treated as immutable: transitions return a
new ``MarketEvent`` rather than mutating in place, so an event's history is
never rewritten by accident and tests can compare before/after directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.models import (
    AttentionResult,
    Confidence,
    Direction,
    Severity,
    Signal,
)
from app.intelligence.scoring import severity_for


class EventStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    STALE = "stale"
    RESOLVED = "resolved"


class GroupingDecision(str, Enum):
    """Why an observation was or was not folded into an existing event."""

    CONTINUE = "continue"
    NEW_NO_MATCH = "new_no_match"
    NEW_WINDOW_EXPIRED = "new_window_expired"
    NEW_DIRECTION_REVERSED = "new_direction_reversed"
    NEW_MATERIAL_ESCALATION = "new_material_escalation"
    NEW_EVENT_CLOSED = "new_event_closed"


_SEVERITY_ADJECTIVE = {
    Severity.CRITICAL: "major",
    Severity.HIGH_ATTENTION: "significant",
    Severity.WORTH_WATCHING: "moderate",
    Severity.NOISE: "minor",
}

_DIRECTION_NOUN = {
    Direction.POSITIVE: "advance",
    Direction.NEGATIVE: "decline",
    Direction.MIXED: "mixed move",
    Direction.NEUTRAL: "activity",
}

_OPPOSED = {
    (Direction.POSITIVE, Direction.NEGATIVE),
    (Direction.NEGATIVE, Direction.POSITIVE),
}


class EventScorePoint(BaseModel):
    """One step in an event's life. Gives the frontend an evolution trace."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    score: float
    severity: Severity
    direction: Direction


class MarketEvent(BaseModel):
    """One underlying market movement, with its supporting evidence attached."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    symbol: str
    started_at: datetime
    last_updated_at: datetime

    direction: Direction
    current_score: float
    peak_score: float
    severity: Severity
    status: EventStatus

    headline: str
    supporting_signals: list[Signal] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: Confidence

    observation_count: int = 1
    history: list[EventScorePoint] = Field(default_factory=list)

    @property
    def is_open(self) -> bool:
        """Whether the event can still absorb new observations."""
        return self.status in {EventStatus.ACTIVE, EventStatus.ACKNOWLEDGED}


def build_event_id(symbol: str, started_at: datetime) -> str:
    """Deterministic id - no UUIDs, so tests and replays stay reproducible."""
    return f"{symbol}-{started_at.strftime('%Y%m%dT%H%M%S')}"


def build_headline(symbol: str, severity: Severity, direction: Direction) -> str:
    """A neutral description of what happened. Never advisory language."""
    return (
        f"{symbol} {_SEVERITY_ADJECTIVE[severity]} {_DIRECTION_NOUN[direction]}"
    )


def contributing_signals(result: AttentionResult) -> list[Signal]:
    """Available signals that actually contributed - the supporting evidence."""
    return [
        s
        for s in result.signals
        if s.is_available and (s.normalized_score or 0.0) > 0.0
    ]


# ---------------------------------------------------------------------------
# Grouping decisions
# ---------------------------------------------------------------------------


def decide_grouping(
    event: MarketEvent | None,
    result: AttentionResult,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> GroupingDecision:
    """Decide whether an observation continues an event or starts a new one.

    A new event is warranted when the *story* changes, not when the number
    does. Continuous drift in one direction is one event; a reversal, a long
    gap, or a material escalation past something the user already reviewed is
    a genuinely different thing to tell them about.
    """
    if event is None or event.symbol != result.symbol:
        return GroupingDecision.NEW_NO_MATCH
    if not event.is_open:
        return GroupingDecision.NEW_EVENT_CLOSED

    elapsed = result.timestamp - event.last_updated_at
    if elapsed > timedelta(minutes=config.event_continuation_window_minutes):
        return GroupingDecision.NEW_WINDOW_EXPIRED

    if (event.direction, result.direction) in _OPPOSED:
        return GroupingDecision.NEW_DIRECTION_REVERSED

    # An acknowledged event that escalates materially beyond what the user
    # reviewed deserves to resurface as a new event rather than mutate quietly.
    if (
        event.status is EventStatus.ACKNOWLEDGED
        and result.objective_score - event.peak_score
        >= config.event_material_change_points
    ):
        return GroupingDecision.NEW_MATERIAL_ESCALATION

    return GroupingDecision.CONTINUE


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def start_event(
    result: AttentionResult, config: IntelligenceConfig = DEFAULT_CONFIG
) -> MarketEvent:
    """Create a fresh event from a scored observation."""
    severity = severity_for(result.final_score, config)
    point = EventScorePoint(
        timestamp=result.timestamp,
        score=result.final_score,
        severity=severity,
        direction=result.direction,
    )
    return MarketEvent(
        event_id=build_event_id(result.symbol, result.timestamp),
        symbol=result.symbol,
        started_at=result.timestamp,
        last_updated_at=result.timestamp,
        direction=result.direction,
        current_score=result.final_score,
        peak_score=result.final_score,
        severity=severity,
        status=EventStatus.ACTIVE,
        headline=build_headline(result.symbol, severity, result.direction),
        supporting_signals=contributing_signals(result),
        breakdown=result.breakdown,
        evidence=result.evidence,
        confidence=result.confidence,
        observation_count=1,
        history=[point],
    )


def apply_observation(
    event: MarketEvent,
    result: AttentionResult,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> MarketEvent:
    """Fold a new observation into an existing event, returning a new event.

    ``peak_score`` is monotonic - it records how severe this got at its worst,
    which is what the user needs when reviewing something after the fact, even
    if the move has since faded.

    An event whose significance drops below the meaningfulness floor is marked
    ``RESOLVED``. An acknowledged event stays acknowledged: re-reading a
    quietly updating event should not push it back into the user's face.
    """
    severity = severity_for(result.final_score, config)
    point = EventScorePoint(
        timestamp=result.timestamp,
        score=result.final_score,
        severity=severity,
        direction=result.direction,
    )

    if result.objective_score < config.meaningful_min_score:
        status = EventStatus.RESOLVED
    elif event.status is EventStatus.ACKNOWLEDGED:
        status = EventStatus.ACKNOWLEDGED
    else:
        status = EventStatus.ACTIVE

    # A neutral reading does not erase an established direction; it simply
    # fails to add to it.
    direction = result.direction if result.direction is not Direction.NEUTRAL else event.direction

    return event.model_copy(
        update={
            "last_updated_at": result.timestamp,
            "direction": direction,
            "current_score": result.final_score,
            "peak_score": max(event.peak_score, result.final_score),
            "severity": severity,
            "status": status,
            "headline": build_headline(event.symbol, severity, direction),
            "supporting_signals": contributing_signals(result),
            "breakdown": result.breakdown,
            "evidence": result.evidence,
            "confidence": result.confidence,
            "observation_count": event.observation_count + 1,
            "history": [*event.history, point],
        }
    )


def acknowledge(event: MarketEvent) -> MarketEvent:
    """Mark an event reviewed. It stays available as historical context."""
    return event.model_copy(update={"status": EventStatus.ACKNOWLEDGED})


def mark_stale(
    events: list[MarketEvent],
    now: datetime,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> list[MarketEvent]:
    """Age out active events that have stopped updating.

    Staleness is not deletion. A stale event remains readable as history; it
    simply stops competing for attention against what is happening now.
    """
    cutoff = timedelta(minutes=config.event_stale_after_minutes)
    return [
        event.model_copy(update={"status": EventStatus.STALE})
        if event.status is EventStatus.ACTIVE and (now - event.last_updated_at) > cutoff
        else event
        for event in events
    ]


# ---------------------------------------------------------------------------
# Stream ingestion
# ---------------------------------------------------------------------------


def _latest_open_event(events: list[MarketEvent], symbol: str) -> MarketEvent | None:
    candidates = [e for e in events if e.symbol == symbol and e.is_open]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.last_updated_at)


def ingest_result(
    events: list[MarketEvent],
    result: AttentionResult,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> tuple[list[MarketEvent], MarketEvent | None, GroupingDecision]:
    """Apply one scored observation to a set of events.

    Returns the updated event list, the affected event (``None`` when the
    observation was not meaningful enough to raise one), and the grouping
    decision that was taken - the decision is returned rather than logged so
    that tests and the UI can both inspect *why* grouping went the way it did.
    """
    existing = _latest_open_event(events, result.symbol)
    decision = decide_grouping(existing, result, config)

    if decision is GroupingDecision.CONTINUE and existing is not None:
        updated = apply_observation(existing, result, config)
        return (
            [updated if e.event_id == existing.event_id else e for e in events],
            updated,
            decision,
        )

    # Only meaningful observations may open a new event. Unremarkable readings
    # that match nothing are correctly dropped rather than recorded as noise.
    if not result.meaningful:
        return events, None, decision

    created = start_event(result, config)
    return [*events, created], created, decision


def ingest_stream(
    results: list[AttentionResult], config: IntelligenceConfig = DEFAULT_CONFIG
) -> list[MarketEvent]:
    """Fold a chronological sequence of results into a set of events."""
    events: list[MarketEvent] = []
    for result in sorted(results, key=lambda r: r.timestamp):
        events, _, _ = ingest_result(events, result, config)
    return events
