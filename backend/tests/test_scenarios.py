"""Replay scenario behaviour: the product's central claims, as tests.

These are the assertions a judge would want to check by hand. Each one encodes
the difference between "something moved" and "something deserves attention".

They run the real path - replay provider emitting raw payloads, normalization,
the unmodified engine, classification - so a regression anywhere in that chain
fails here.
"""

from __future__ import annotations

import pytest

from app.market.context import MoveClassification
from app.providers.replay import ReplayProvider
from app.replay.scenarios import SCENARIOS, get_scenario
from app.replay.universe import DEMO_WATCHLIST, UNIVERSE
from app.services.pipeline import run_cycle

LATE_SESSION_STEP = 20


def cycle_for(scenario_key: str, step: int = LATE_SESSION_STEP):
    scenario = get_scenario(scenario_key)
    baselines = {s: UNIVERSE[s] for s in DEMO_WATCHLIST}
    return run_cycle(ReplayProvider(scenario, step=step), baselines)


def result_for(scenario_key: str, symbol: str, step: int = LATE_SESSION_STEP):
    return cycle_for(scenario_key, step).by_symbol()[symbol]


# ---------------------------------------------------------------------------
# 1. Stock crashes while the market is stable
# ---------------------------------------------------------------------------


def test_crash_in_a_stable_market_is_critical_and_stock_specific() -> None:
    scored = result_for("crash_stable_market", "RELIANCE")
    assert scored.result.meaningful is True
    assert scored.result.final_score >= 75
    assert scored.classification == MoveClassification.STOCK_SPECIFIC


def test_crash_scenario_surfaces_exactly_one_stock() -> None:
    """A real event should stand out, not drag the whole list up with it."""
    ranked = cycle_for("crash_stable_market").ranked()
    assert len(ranked) == 1
    assert ranked[0].symbol == "RELIANCE"


# ---------------------------------------------------------------------------
# 2. The scenario that separates LUMEN from a percent-change sort
# ---------------------------------------------------------------------------


def test_market_wide_selloff_produces_no_meaningful_events() -> None:
    """Everything falls ~4% and LUMEN reports nothing. This is the point."""
    cycle = cycle_for("market_wide_selloff")
    assert cycle.ranked() == []


def test_market_wide_selloff_is_detected_as_broad_movement() -> None:
    cycle = cycle_for("market_wide_selloff")
    assert cycle.market.is_market_wide_move is True
    assert cycle.market.breadth_ratio >= 0.7
    assert cycle.market.median_return < -1.0
    assert "Broad market move" in cycle.market.label


def test_a_four_percent_fall_can_score_lower_than_a_two_percent_rise() -> None:
    """Size is not significance. The comparison a percent sort cannot make."""
    big_but_market_driven = result_for("market_wide_selloff", "HDFCBANK")
    small_but_corroborated = result_for("earnings_beat_volume", "TATAMOTORS")

    assert abs(big_but_market_driven.observation.daily_return) > abs(
        small_but_corroborated.observation.daily_return
    )
    assert (
        small_but_corroborated.result.final_score
        > big_but_market_driven.result.final_score
    )


def test_market_driven_fall_is_classified_market_wide() -> None:
    scored = result_for("market_wide_selloff", "HDFCBANK")
    assert scored.classification == MoveClassification.MARKET_WIDE


# ---------------------------------------------------------------------------
# 3. Small move, loud context
# ---------------------------------------------------------------------------


def test_small_move_with_volume_and_news_is_surfaced() -> None:
    scored = result_for("earnings_beat_volume", "TATAMOTORS")
    assert scored.result.meaningful is True
    assert abs(scored.observation.daily_return) < 3.0


def test_earnings_scenario_is_driven_by_volume_not_price() -> None:
    scored = result_for("earnings_beat_volume", "TATAMOTORS")
    breakdown = scored.result.breakdown
    assert breakdown["volume"] > breakdown["price_movement"]
    assert breakdown["news"] > 0


# ---------------------------------------------------------------------------
# 4. Gap up on news
# ---------------------------------------------------------------------------


def test_gap_scenario_registers_a_gap_signal() -> None:
    scored = result_for("gap_up_news", "TATASTEEL")
    assert scored.result.meaningful is True
    assert scored.result.breakdown["gap"] > 0
    assert scored.observation.gap_percent > 2.0


def test_gap_is_judged_against_the_stocks_own_gap_history() -> None:
    """A fixed percentage threshold would be wrong for every stock but one."""
    from app.intelligence.models import SignalType

    scored = result_for("gap_up_news", "TATASTEEL")
    gap = next(s for s in scored.result.signals if s.signal_type is SignalType.GAP)
    assert gap.detail["has_gap_history"] is True
    assert gap.detail["gap_sigma"] is not None


# ---------------------------------------------------------------------------
# 5. Sector-wide movement
# ---------------------------------------------------------------------------


def test_sector_move_is_classified_as_sector_wide_not_stock_specific() -> None:
    scored = result_for("sector_rotation", "INFY")
    assert scored.classification == MoveClassification.SECTOR_WIDE


def test_sector_move_scores_below_an_equivalent_stock_specific_move() -> None:
    sector_driven = result_for("sector_rotation", "INFY")
    stock_specific = result_for("crash_stable_market", "RELIANCE")
    assert abs(sector_driven.observation.daily_return) > 2.5
    assert sector_driven.result.final_score < stock_specific.result.final_score


# ---------------------------------------------------------------------------
# 6. Noise filtering
# ---------------------------------------------------------------------------


def test_quiet_session_produces_nothing() -> None:
    """A product that always finds something is not filtering anything."""
    cycle = cycle_for("quiet_session")
    assert cycle.ranked() == []
    assert cycle.market.is_market_wide_move is False


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_every_scenario_runs_without_errors(key: str) -> None:
    cycle = cycle_for(key)
    assert cycle.errors == []
    assert len(cycle.scored) == len(DEMO_WATCHLIST)


# ---------------------------------------------------------------------------
# Replay integrity
# ---------------------------------------------------------------------------


def test_replay_is_deterministic_across_runs() -> None:
    first = cycle_for("crash_stable_market")
    second = cycle_for("crash_stable_market")
    assert [s.result.final_score for s in first.scored] == [
        s.result.final_score for s in second.scored
    ]


def test_replay_uses_virtual_time_not_system_time() -> None:
    """Replay timestamps come from the scenario's session, not the clock."""
    provider = ReplayProvider(get_scenario("crash_stable_market"), step=8)
    raw = provider.get_quotes(["NSE_EQ|RELIANCE"])
    assert raw.as_of.date() == get_scenario("crash_stable_market").session_date
    assert raw.is_replay is True


def test_every_replay_response_is_flagged_as_replay() -> None:
    cycle = cycle_for("crash_stable_market")
    assert cycle.is_replay is True
    assert all(s.quote.is_replay for s in cycle.scored)


def test_event_evolves_rather_than_duplicating_across_steps() -> None:
    """Stepping through the crash must not create a new event per step."""
    from app.intelligence.events import ingest_stream

    scenario = get_scenario("crash_stable_market")
    baselines = {"RELIANCE": UNIVERSE["RELIANCE"]}
    results = []
    for step in range(6, 21):
        cycle = run_cycle(ReplayProvider(scenario, step=step), baselines)
        results.append(cycle.by_symbol()["RELIANCE"].result)

    events = ingest_stream(results)
    assert len(events) == 1
    assert events[0].observation_count > 1


def test_scenario_severity_escalates_as_the_shock_lands() -> None:
    early = result_for("crash_stable_market", "RELIANCE", step=4)
    late = result_for("crash_stable_market", "RELIANCE", step=20)
    assert late.result.final_score > early.result.final_score
