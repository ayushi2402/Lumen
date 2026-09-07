"""Scoring, severity, confidence and personalization tests.

The scenario tests here are the ones that actually define the product: they
assert that LUMEN behaves differently from a percent-change sort.

Where the spec gave a firm expectation (scenario 2 must not be critical), the
assertion is absolute. Where it gave a directional expectation (scenario 3 is
"potentially" high attention), the assertion is comparative - a test that
pins an arbitrary number would break on any future weight tuning without
indicating a real regression.
"""

from __future__ import annotations

import pytest

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig, ScoringWeights
from app.intelligence.models import Confidence, Direction, Severity, SignalFamily
from app.intelligence.scoring import (
    aggregate_direction,
    personalize,
    score_breakdown,
    score_observation,
    severity_for,
)
from app.intelligence.signals import detect_all
from app.services.fixtures import (
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_2_MARKET_WIDE_SELLOFF,
    SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT,
    SCENARIO_4_UNCORROBORATED_MOVE,
    SCENARIO_5_NO_VOLATILITY_HISTORY,
    SCENARIO_6_FALLBACK_VOLUME_BASELINE,
)


# ---------------------------------------------------------------------------
# Weights and severity bands
# ---------------------------------------------------------------------------


def test_weights_sum_to_one_hundred() -> None:
    assert DEFAULT_CONFIG.weights.total == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, Severity.NOISE),
        (39.9, Severity.NOISE),
        (40, Severity.WORTH_WATCHING),
        (59.9, Severity.WORTH_WATCHING),
        (60, Severity.HIGH_ATTENTION),
        (79.9, Severity.HIGH_ATTENTION),
        (80, Severity.CRITICAL),
        (100, Severity.CRITICAL),
    ],
)
def test_severity_bands(score, expected) -> None:
    assert severity_for(score) is expected


def test_no_single_family_can_reach_high_attention_alone() -> None:
    """Structural guarantee: every weight is below the High Attention floor."""
    for weight in DEFAULT_CONFIG.weights.as_map().values():
        assert weight < DEFAULT_CONFIG.severity_high_attention


def test_correlated_price_signals_do_not_stack() -> None:
    """Three views of one move must average within their family, not sum."""
    signals = detect_all(SCENARIO_1_CORROBORATED_DECLINE)
    breakdown = score_breakdown(signals)
    assert breakdown[SignalFamily.PRICE_MOVEMENT.value] <= DEFAULT_CONFIG.weights.price_movement


def test_breakdown_covers_every_family_and_sums_to_the_score() -> None:
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert set(result.breakdown) == {f.value for f in SignalFamily}
    assert sum(result.breakdown.values()) == pytest.approx(result.objective_score, abs=0.05)


# ---------------------------------------------------------------------------
# Scenario 1 - fully corroborated
# ---------------------------------------------------------------------------


def test_scenario_1_is_high_attention_or_critical() -> None:
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert result.meaningful is True
    assert result.severity in {Severity.HIGH_ATTENTION, Severity.CRITICAL}
    assert result.direction is Direction.NEGATIVE
    assert result.confidence is Confidence.HIGH


# ---------------------------------------------------------------------------
# Scenario 2 - the one that proves LUMEN is not a percent-change sort
# ---------------------------------------------------------------------------


def test_scenario_2_market_wide_fall_is_not_meaningful() -> None:
    """A 5% fall on a 4.8% down market is the market, not the stock."""
    result = score_observation(SCENARIO_2_MARKET_WIDE_SELLOFF)
    assert result.severity is not Severity.CRITICAL
    assert result.meaningful is False


def test_scenario_2_scores_below_a_corroborated_move_of_similar_size() -> None:
    market_wide = score_observation(SCENARIO_2_MARKET_WIDE_SELLOFF)
    corroborated = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert market_wide.objective_score < corroborated.objective_score


def test_scenario_2_has_low_confidence_from_single_signal() -> None:
    result = score_observation(SCENARIO_2_MARKET_WIDE_SELLOFF)
    assert result.confidence_report.independent_signals == 1
    assert result.confidence is Confidence.LOW


# ---------------------------------------------------------------------------
# Scenario 3 - small move, loud context
# ---------------------------------------------------------------------------


def test_scenario_3_small_move_is_still_meaningful() -> None:
    """+1.5% on 4x volume with major news must not be filtered out."""
    result = score_observation(SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT)
    assert result.meaningful is True
    assert result.severity is not Severity.NOISE


def test_scenario_3_beats_a_much_larger_uncorroborated_move() -> None:
    """A +1.5% move with evidence outranks a -4.2% move without it."""
    small_but_supported = score_observation(SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT)
    large_but_bare = score_observation(SCENARIO_4_UNCORROBORATED_MOVE)
    assert small_but_supported.objective_score > large_but_bare.objective_score


def test_scenario_3_is_driven_by_volume_and_news_not_price() -> None:
    result = score_observation(SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT)
    assert result.breakdown["volume"] > result.breakdown["price_movement"]
    assert result.breakdown["news"] > result.breakdown["price_movement"]


# ---------------------------------------------------------------------------
# Scenario 4 - large but uncorroborated
# ---------------------------------------------------------------------------


def test_scenario_4_scores_far_below_the_same_move_with_corroboration() -> None:
    """Identical -4.2% move; only the supporting evidence differs."""
    bare = score_observation(SCENARIO_4_UNCORROBORATED_MOVE)
    supported = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert bare.objective_score < supported.objective_score
    assert bare.meaningful is False


def test_meaningfulness_requires_more_than_one_independent_family() -> None:
    result = score_observation(SCENARIO_4_UNCORROBORATED_MOVE)
    assert result.confidence_report.independent_signals < (
        DEFAULT_CONFIG.meaningful_min_independent_signals
    )
    assert result.meaningful is False


# ---------------------------------------------------------------------------
# Scenario 5 - missing evidence lowers confidence, not just score
# ---------------------------------------------------------------------------


def test_scenario_5_scores_high_but_only_medium_confidence() -> None:
    """The spec's case: a strong reading with incomplete evidence."""
    result = score_observation(SCENARIO_5_NO_VOLATILITY_HISTORY)
    assert result.severity in {Severity.HIGH_ATTENTION, Severity.CRITICAL}
    assert result.confidence is Confidence.MEDIUM
    assert result.confidence_report.coverage < 1.0


def test_scenario_5_volatility_family_contributes_zero_without_fabrication() -> None:
    result = score_observation(SCENARIO_5_NO_VOLATILITY_HISTORY)
    assert result.breakdown["volatility"] == 0.0
    assert any("volatility" in reason for reason in result.confidence_report.reasons)


def test_missing_evidence_lowers_the_score_rather_than_being_rescaled_away() -> None:
    """No renormalization: unverified moves must not score like verified ones."""
    complete = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    incomplete = score_observation(SCENARIO_5_NO_VOLATILITY_HISTORY)
    assert incomplete.objective_score < complete.objective_score


# ---------------------------------------------------------------------------
# Scenario 6 - fallback baseline is visible downstream
# ---------------------------------------------------------------------------


def test_scenario_6_fallback_baseline_caps_confidence() -> None:
    result = score_observation(SCENARIO_6_FALLBACK_VOLUME_BASELINE)
    assert result.confidence is not Confidence.HIGH
    assert any("fallback" in reason.lower() for reason in result.confidence_report.reasons)


# ---------------------------------------------------------------------------
# Direction vs significance
# ---------------------------------------------------------------------------


def test_direction_is_independent_of_score() -> None:
    """A large advance and a large decline are equally significant."""
    down = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    # evolve() re-derives daily_return from the new price; model_copy would
    # not, leaving a negative return attached to a higher price.
    up = score_observation(
        SCENARIO_1_CORROBORATED_DECLINE.evolve(
            price=1_354.70,  # +4.21%, mirroring the decline exactly
            benchmark_return=0.30,
            sector_return=0.80,
            gap_percent=1.8,
        )
    )
    assert up.direction is Direction.POSITIVE
    assert down.direction is Direction.NEGATIVE
    assert up.objective_score == pytest.approx(down.objective_score, abs=1.0)


def test_no_recommendation_fields_exist_anywhere_in_the_result() -> None:
    """LUMEN must never emit BUY/SELL/HOLD."""
    payload = score_observation(SCENARIO_1_CORROBORATED_DECLINE).model_dump_json().lower()
    for banned in ("buy", "sell", '"hold"', "recommend", "target price"):
        assert banned not in payload


def test_disagreeing_directional_signals_produce_mixed() -> None:
    signals = detect_all(SCENARIO_1_CORROBORATED_DECLINE.evolve(gap_percent=2.5))
    assert aggregate_direction(signals) is Direction.MIXED


# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------


def test_personalization_is_capped() -> None:
    adjustment, _ = personalize(80.0, behavioral_relevance=100.0)
    assert adjustment <= DEFAULT_CONFIG.personalization_max_adjustment


def test_personalization_improves_ranking_without_changing_objective_score() -> None:
    """Scenario 8."""
    obs = SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT
    plain = score_observation(obs, behavioral_relevance=0.0)
    personalized = score_observation(obs, behavioral_relevance=90.0)

    assert personalized.objective_score == plain.objective_score
    assert personalized.final_score > plain.final_score
    assert personalized.personalization_adjustment <= (
        DEFAULT_CONFIG.personalization_max_adjustment
    )


def test_personalization_cannot_promote_noise() -> None:
    """Maximum behavioural interest in market noise still leaves it noise."""
    result = score_observation(SCENARIO_2_MARKET_WIDE_SELLOFF, behavioral_relevance=100.0)
    assert result.personalization_adjustment == 0.0
    assert result.severity is Severity.NOISE
    assert result.meaningful is False


def test_personalization_cannot_bridge_more_than_one_severity_band() -> None:
    """The cap is smaller than any band's width, so it can nudge, never leap."""
    band_width = DEFAULT_CONFIG.severity_critical - DEFAULT_CONFIG.severity_high_attention
    assert DEFAULT_CONFIG.personalization_max_adjustment < band_width


def test_personalization_relevance_is_clamped_to_valid_range() -> None:
    assert personalize(80.0, -50.0)[0] == 0.0
    assert personalize(80.0, 999.0)[0] == DEFAULT_CONFIG.personalization_max_adjustment


def test_meaningfulness_uses_objective_score_not_personalized_score() -> None:
    """Behaviour must not manufacture meaningfulness."""
    just_below = SCENARIO_2_MARKET_WIDE_SELLOFF
    result = score_observation(just_below, behavioral_relevance=100.0)
    assert result.meaningful is False


# ---------------------------------------------------------------------------
# Configurability
# ---------------------------------------------------------------------------


def test_weights_are_tunable_without_touching_engine_code() -> None:
    volume_heavy = IntelligenceConfig(
        weights=ScoringWeights(
            price_movement=10, relative_performance=10, volume=60, volatility=10, gap=5, news=5
        )
    )
    default = score_observation(SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT)
    tuned = score_observation(SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT, config=volume_heavy)
    assert tuned.objective_score != default.objective_score


def test_threshold_is_configurable() -> None:
    strict = IntelligenceConfig(meaningful_min_score=95.0)
    assert score_observation(SCENARIO_1_CORROBORATED_DECLINE, config=strict).meaningful is False


def test_scoring_is_deterministic() -> None:
    first = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    second = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert first.model_dump() == second.model_dump()
