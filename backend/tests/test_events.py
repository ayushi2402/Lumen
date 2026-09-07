"""Event grouping, evolution and deduplication tests.

The behaviour under test: many signals and many observations of one movement
must collapse into ONE event, while a genuinely different story must not be
folded into an existing one.
"""

from __future__ import annotations

from datetime import timedelta

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.events import (
    EventStatus,
    GroupingDecision,
    acknowledge,
    ingest_result,
    ingest_stream,
    mark_stale,
    start_event,
)
from app.intelligence.models import Direction, Severity
from app.intelligence.scoring import score_observation
from app.services.fixtures import (
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_7_EVOLVING_DECLINE,
    SCENARIO_7B_REVERSAL,
    SCENARIO_7C_MIXED_RECOVERY,
)


def _results(observations):
    return [score_observation(obs) for obs in observations]


# ---------------------------------------------------------------------------
# Grouping: many signals -> one event
# ---------------------------------------------------------------------------


def test_many_signals_become_one_event_with_supporting_evidence() -> None:
    """The spec's RELIANCE case: five signals, one event."""
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    event = start_event(result)

    assert event.symbol == "RELIANCE"
    assert len(event.supporting_signals) >= 5
    assert event.direction is Direction.NEGATIVE


def test_event_headline_is_descriptive_and_not_advisory() -> None:
    event = start_event(score_observation(SCENARIO_1_CORROBORATED_DECLINE))
    assert event.headline == "RELIANCE major decline"
    for banned in ("buy", "sell", "hold"):
        assert banned not in event.headline.lower()


def test_event_carries_breakdown_and_confidence() -> None:
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    event = start_event(result)
    assert event.breakdown == result.breakdown
    assert event.confidence is result.confidence


# ---------------------------------------------------------------------------
# Scenario 7 - continuation
# ---------------------------------------------------------------------------


def test_continuing_move_stays_one_evolving_event() -> None:
    """-2% -> -3% -> -4% is one story, not three."""
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))

    assert len(events) == 1
    event = events[0]
    assert event.observation_count == 3
    assert event.status is EventStatus.ACTIVE
    assert len(event.history) == 3


def test_escalation_is_recorded_on_the_same_event() -> None:
    results = _results(SCENARIO_7_EVOLVING_DECLINE)
    events = ingest_stream(results)
    event = events[0]

    scores = [point.score for point in event.history]
    assert scores == sorted(scores), "significance should escalate across observations"
    assert event.current_score == results[-1].final_score
    assert event.peak_score == max(scores)
    assert event.severity is not Severity.NOISE


def test_peak_score_is_monotonic_when_a_move_fades() -> None:
    """Reviewing later, the user needs to know how bad it got at its worst."""
    escalating = _results(SCENARIO_7_EVOLVING_DECLINE)
    events = ingest_stream(escalating)
    peak = events[0].peak_score

    faded = escalating[0].model_copy(
        update={"timestamp": escalating[-1].timestamp + timedelta(minutes=20)}
    )
    events, updated, _ = ingest_result(events, faded)
    assert updated.current_score < peak
    assert updated.peak_score == peak


# ---------------------------------------------------------------------------
# Scenario 7 - reversal and unrelated movement
# ---------------------------------------------------------------------------


def test_reversal_creates_a_new_event() -> None:
    """When every directional signal flips, the story genuinely changed."""
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    reversal = score_observation(SCENARIO_7B_REVERSAL)
    assert reversal.direction is Direction.POSITIVE

    events, created, decision = ingest_result(events, reversal)
    assert decision is GroupingDecision.NEW_DIRECTION_REVERSED
    assert len(events) == 2
    assert created.direction is Direction.POSITIVE


def test_mixed_direction_is_not_treated_as_a_reversal() -> None:
    """Disagreeing signals are ambiguity, not opposition - do not split."""
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    mixed = score_observation(SCENARIO_7C_MIXED_RECOVERY)
    assert mixed.direction is Direction.MIXED

    events, _, decision = ingest_result(events, mixed)
    assert decision is GroupingDecision.CONTINUE
    assert len(events) == 1


def test_unrelated_symbol_creates_a_separate_event() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    events, created, decision = ingest_result(
        events, score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    )
    assert decision is GroupingDecision.NEW_NO_MATCH
    assert len(events) == 2
    assert {e.symbol for e in events} == {"INFY", "RELIANCE"}


def test_observation_after_the_window_starts_a_new_event() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    later = score_observation(SCENARIO_7_EVOLVING_DECLINE[-1]).model_copy(
        update={
            "timestamp": events[0].last_updated_at
            + timedelta(minutes=DEFAULT_CONFIG.event_continuation_window_minutes + 1)
        }
    )
    events, _, decision = ingest_result(events, later)
    assert decision is GroupingDecision.NEW_WINDOW_EXPIRED
    assert len(events) == 2


def test_continuation_window_is_configurable() -> None:
    impatient = IntelligenceConfig(event_continuation_window_minutes=5)
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE), impatient)
    assert len(events) == 3, "with a 5-minute window each 30-minute step is its own event"


# ---------------------------------------------------------------------------
# Insignificant observations
# ---------------------------------------------------------------------------


def test_insignificant_observation_creates_no_event() -> None:
    from app.services.fixtures import SCENARIO_2_MARKET_WIDE_SELLOFF

    events, created, _ = ingest_result([], score_observation(SCENARIO_2_MARKET_WIDE_SELLOFF))
    assert events == []
    assert created is None


def test_event_resolves_when_significance_falls_away() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    quiet = score_observation(
        SCENARIO_7_EVOLVING_DECLINE[-1].evolve(
            price=1_500.00,  # fully recovered: 0.00% on the day
            benchmark_return=0.0,
            sector_return=0.0,
            volume=5_000_000,  # exactly the baseline
            intraday_volatility=1.2,  # exactly the historical level
            gap_percent=0.0,
            news=None,
        )
    )
    events, updated, _ = ingest_result(events, quiet)
    assert updated.status is EventStatus.RESOLVED
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Lifecycle: acknowledgement and staleness
# ---------------------------------------------------------------------------


def test_acknowledged_event_remains_available_as_history() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    reviewed = acknowledge(events[0])
    assert reviewed.status is EventStatus.ACKNOWLEDGED
    assert reviewed.history == events[0].history, "acknowledging must not erase history"


def test_acknowledged_event_absorbs_minor_updates_quietly() -> None:
    events = [acknowledge(ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))[0])]
    minor = score_observation(SCENARIO_7_EVOLVING_DECLINE[-1]).model_copy(
        update={"timestamp": events[0].last_updated_at + timedelta(minutes=10)}
    )
    events, updated, decision = ingest_result(events, minor)
    assert decision is GroupingDecision.CONTINUE
    assert updated.status is EventStatus.ACKNOWLEDGED


def test_material_escalation_past_an_acknowledged_peak_raises_a_new_event() -> None:
    """Something the user dismissed getting materially worse deserves resurfacing."""
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE[:1]))
    events = [acknowledge(events[0])]

    escalated = score_observation(SCENARIO_7_EVOLVING_DECLINE[-1]).model_copy(
        update={"timestamp": events[0].last_updated_at + timedelta(minutes=10)}
    )
    assert escalated.objective_score - events[0].peak_score >= (
        DEFAULT_CONFIG.event_material_change_points
    )

    events, created, decision = ingest_result(events, escalated)
    assert decision is GroupingDecision.NEW_MATERIAL_ESCALATION
    assert len(events) == 2


def test_stale_events_age_out_but_are_not_deleted() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    later = events[0].last_updated_at + timedelta(
        minutes=DEFAULT_CONFIG.event_stale_after_minutes + 1
    )
    aged = mark_stale(events, later)

    assert aged[0].status is EventStatus.STALE
    assert len(aged) == 1, "staleness is not deletion"
    assert aged[0].history == events[0].history


def test_fresh_events_are_not_marked_stale() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    soon = events[0].last_updated_at + timedelta(minutes=5)
    assert mark_stale(events, soon)[0].status is EventStatus.ACTIVE


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_event_ids_are_deterministic() -> None:
    first = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    second = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE))
    assert [e.event_id for e in first] == [e.event_id for e in second]


def test_transitions_do_not_mutate_the_original_event() -> None:
    events = ingest_stream(_results(SCENARIO_7_EVOLVING_DECLINE[:1]))
    original = events[0]
    original_count = original.observation_count

    ingest_result(events, score_observation(SCENARIO_7_EVOLVING_DECLINE[1]))
    assert original.observation_count == original_count
