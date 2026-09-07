"""Signal-engine tests.

The recurring theme: missing data must never become a zero-valued signal.
Several tests exist specifically to fail if someone later "simplifies" an
unavailable state into a default.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.intelligence.config import DEFAULT_CONFIG
from app.intelligence.models import (
    Availability,
    Direction,
    MarketObservation,
    NewsEvidence,
    SignalType,
    VolumeBaseline,
)
from app.intelligence.signals import (
    abnormal_volume,
    absolute_price_movement,
    detect_all,
    gap_signal,
    historical_normalized_movement,
    news_signal,
    ramp,
    relative_to_benchmark,
    relative_to_sector,
    volatility_anomaly,
    volatility_normalized_movement,
)
from app.services.fixtures import (
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_2_MARKET_WIDE_SELLOFF,
    SCENARIO_5_NO_VOLATILITY_HISTORY,
    SCENARIO_6_FALLBACK_VOLUME_BASELINE,
    SCENARIO_6B_NO_VOLUME_BASELINE,
)

TS = datetime(2026, 3, 12, 11, 0, 0)


def _obs(**overrides) -> MarketObservation:
    base = {
        "symbol": "TEST",
        "timestamp": TS,
        "price": 100.0,
        "previous_price": 100.0,
    }
    return MarketObservation(**{**base, **overrides})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("magnitude", "expected"),
    [(0.0, 0.0), (0.5, 0.0), (5.0, 1.0), (9.9, 1.0), (2.75, 0.5)],
)
def test_ramp_clamps_between_floor_and_full_strength(magnitude, expected) -> None:
    assert ramp(magnitude, 0.5, 5.0) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# A. Absolute price movement
# ---------------------------------------------------------------------------


def test_daily_return_is_derived_from_prices() -> None:
    obs = _obs(price=96.0, previous_price=100.0)
    assert obs.daily_return == pytest.approx(-4.0)


def test_absolute_price_movement_direction_and_value() -> None:
    signal = absolute_price_movement(_obs(price=104.2, previous_price=100.0))
    assert signal.availability is Availability.AVAILABLE
    assert signal.value == pytest.approx(4.2)
    assert signal.direction is Direction.POSITIVE


def test_tiny_move_scores_zero_strength() -> None:
    """Sub-threshold drift is noise, not a weak event."""
    signal = absolute_price_movement(_obs(price=100.2, previous_price=100.0))
    assert signal.normalized_score == 0.0
    assert signal.direction is Direction.NEUTRAL


# ---------------------------------------------------------------------------
# B/C. Normalized movement - the core "is this unusual for THIS stock" logic
# ---------------------------------------------------------------------------


def test_same_move_scores_higher_for_a_calmer_stock() -> None:
    """A 3% move matters more in a stock that usually moves 0.5% than 3%."""
    calm = historical_normalized_movement(
        _obs(price=103.0, typical_daily_move=0.5, typical_move_sample_days=30)
    )
    jumpy = historical_normalized_movement(
        _obs(price=103.0, typical_daily_move=3.0, typical_move_sample_days=30)
    )
    assert calm.normalized_score > jumpy.normalized_score


def test_historical_normalized_movement_requires_enough_history() -> None:
    signal = historical_normalized_movement(
        _obs(price=104.0, typical_daily_move=1.0, typical_move_sample_days=3)
    )
    assert signal.availability is Availability.INSUFFICIENT_HISTORY
    assert signal.normalized_score is None


def test_volatility_normalized_movement_computes_sigma() -> None:
    signal = volatility_normalized_movement(
        _obs(price=104.0, historical_volatility=2.0, volatility_sample_days=30)
    )
    assert signal.value == pytest.approx(2.0)  # 4% move / 2% daily vol


def test_missing_volatility_history_is_not_zero_volatility() -> None:
    """Scenario 5. The distinction this whole design rests on."""
    signal = volatility_normalized_movement(_obs(price=104.0))
    assert signal.availability is Availability.INSUFFICIENT_HISTORY
    assert signal.value is None
    assert signal.normalized_score is None


# ---------------------------------------------------------------------------
# D/E. Relative performance
# ---------------------------------------------------------------------------


def test_relative_performance_is_excess_in_percentage_points() -> None:
    """The spec's worked example: -2.4% vs -0.4% is -2.0 pp."""
    signal = relative_to_benchmark(_obs(price=97.6, benchmark_return=-0.4))
    assert signal.value == pytest.approx(-2.0)
    assert signal.direction is Direction.NEGATIVE


def test_market_wide_move_produces_no_relative_signal() -> None:
    """Scenario 2: a 5% fall on a 4.8% down market is not a stock story."""
    signal = relative_to_benchmark(SCENARIO_2_MARKET_WIDE_SELLOFF)
    assert signal.availability is Availability.AVAILABLE
    assert signal.normalized_score == 0.0


def test_sector_relative_is_computed_independently() -> None:
    signal = relative_to_sector(_obs(price=97.0, benchmark_return=-0.4, sector_return=-2.0))
    assert signal.value == pytest.approx(-1.0)


def test_relative_signal_unavailable_without_reference() -> None:
    assert relative_to_benchmark(_obs(price=97.0)).availability is Availability.UNAVAILABLE


# ---------------------------------------------------------------------------
# F. Abnormal volume and baseline selection
# ---------------------------------------------------------------------------


def test_prefers_same_time_of_day_baseline() -> None:
    signal = abnormal_volume(SCENARIO_1_CORROBORATED_DECLINE)
    assert signal.detail["baseline_kind"] == VolumeBaseline.SAME_TIME_OF_DAY.value
    assert signal.detail["is_fallback_baseline"] is False
    assert signal.value == pytest.approx(2.8, abs=0.01)


def test_falls_back_explicitly_when_same_time_history_is_thin() -> None:
    """Scenario 6: the fallback must be declared, never silent."""
    signal = abnormal_volume(SCENARIO_6_FALLBACK_VOLUME_BASELINE)
    assert signal.detail["baseline_kind"] == VolumeBaseline.FULL_DAY_AVERAGE.value
    assert signal.detail["is_fallback_baseline"] is True
    # And it is trusted less than a proper baseline.
    assert signal.confidence == DEFAULT_CONFIG.confidence_fallback_baseline
    assert signal.confidence < DEFAULT_CONFIG.confidence_full_history


def test_never_fabricates_a_same_time_of_day_baseline() -> None:
    """A full-day average must not be passed off as same-time-of-day."""
    signal = abnormal_volume(SCENARIO_6_FALLBACK_VOLUME_BASELINE)
    assert signal.detail["baseline_kind"] != VolumeBaseline.SAME_TIME_OF_DAY.value


def test_no_baseline_at_all_is_insufficient_history_not_normal_volume() -> None:
    signal = abnormal_volume(SCENARIO_6B_NO_VOLUME_BASELINE)
    assert signal.availability is Availability.INSUFFICIENT_HISTORY
    assert signal.normalized_score is None


def test_volume_has_no_direction() -> None:
    """Volume corroborates that something happened, not which way."""
    signal = abnormal_volume(SCENARIO_1_CORROBORATED_DECLINE)
    assert signal.direction is Direction.NEUTRAL
    assert signal.is_directional is False


# ---------------------------------------------------------------------------
# G/H. Volatility anomaly
# ---------------------------------------------------------------------------


def test_volatility_anomaly_needs_enough_intraday_observations() -> None:
    signal = volatility_anomaly(
        _obs(
            price=104.0,
            historical_volatility=1.5,
            volatility_sample_days=30,
            intraday_volatility=3.0,
            intraday_observation_count=2,
        )
    )
    assert signal.availability is Availability.INSUFFICIENT_HISTORY


def test_volatility_anomaly_available_with_sufficient_data() -> None:
    signal = volatility_anomaly(SCENARIO_1_CORROBORATED_DECLINE)
    assert signal.availability is Availability.AVAILABLE
    assert signal.value > 1.0


def test_scenario_5_produces_no_volatility_signal_at_all() -> None:
    signals = {s.signal_type: s for s in detect_all(SCENARIO_5_NO_VOLATILITY_HISTORY)}
    for kind in (SignalType.VOLATILITY_ANOMALY, SignalType.VOLATILITY_NORMALIZED_MOVEMENT):
        assert signals[kind].availability is Availability.INSUFFICIENT_HISTORY
        assert signals[kind].value is None


# ---------------------------------------------------------------------------
# I. Gap
# ---------------------------------------------------------------------------


def test_gap_uses_history_when_available() -> None:
    signal = gap_signal(_obs(gap_percent=-1.8, historical_gap_stdev=0.7))
    assert signal.detail["has_gap_history"] is True
    assert signal.detail["gap_sigma"] == pytest.approx(2.571, abs=0.01)
    assert signal.direction is Direction.NEGATIVE


def test_gap_without_history_still_scores_on_absolute_size() -> None:
    signal = gap_signal(_obs(gap_percent=2.0))
    assert signal.availability is Availability.AVAILABLE
    assert signal.detail["has_gap_history"] is False
    assert signal.detail["gap_sigma"] is None
    # Lower confidence without a distribution to judge against.
    assert signal.confidence == DEFAULT_CONFIG.confidence_fallback_baseline


def test_missing_gap_is_unavailable() -> None:
    assert gap_signal(_obs()).availability is Availability.UNAVAILABLE


# ---------------------------------------------------------------------------
# J. News
# ---------------------------------------------------------------------------


def test_news_signal_carries_evidence_without_asserting_cause() -> None:
    signal = news_signal(SCENARIO_1_CORROBORATED_DECLINE)
    assert signal.availability is Availability.AVAILABLE
    assert signal.detail["causal_link_established"] is False
    assert signal.direction is Direction.NEUTRAL


def test_unknown_news_timing_reduces_strength() -> None:
    known = news_signal(
        _obs(news=NewsEvidence(present=True, relevance=1.0, minutes_before_move=5, confidence=0.9))
    )
    unknown = news_signal(
        _obs(news=NewsEvidence(present=True, relevance=1.0, confidence=0.9))
    )
    assert unknown.normalized_score < known.normalized_score
    assert unknown.detail["timing_known"] is False


def test_absent_news_is_unavailable_not_a_zero_signal() -> None:
    assert news_signal(_obs()).availability is Availability.UNAVAILABLE
    assert news_signal(_obs(news=NewsEvidence(present=False))).availability is (
        Availability.UNAVAILABLE
    )


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_detect_all_returns_every_signal_including_unavailable_ones() -> None:
    signals = detect_all(SCENARIO_5_NO_VOLATILITY_HISTORY)
    assert len(signals) == 9
    assert any(not s.is_available for s in signals)


def test_signal_detection_is_deterministic() -> None:
    """Same input, same output. No clocks, no randomness, no I/O."""
    first = detect_all(SCENARIO_1_CORROBORATED_DECLINE)
    second = detect_all(SCENARIO_1_CORROBORATED_DECLINE)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]
