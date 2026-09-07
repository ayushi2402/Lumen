"""Deterministic explanation tests.

The critical assertions here are negative ones: the explainer must not claim
causation it cannot support, and must not emit advice.
"""

from __future__ import annotations

from app.intelligence.explain import explain, explain_score
from app.intelligence.models import NewsEvidence
from app.intelligence.scoring import score_observation
from app.services.fixtures import (
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_5_NO_VOLATILITY_HISTORY,
    SCENARIO_6_FALLBACK_VOLUME_BASELINE,
)


def _explain(obs) -> str:
    return explain(score_observation(obs))


def test_explanation_states_the_facts_it_was_given() -> None:
    text = _explain(SCENARIO_1_CORROBORATED_DECLINE)
    assert "RELIANCE fell 4.2%" in text
    assert "underperforming NIFTY by 3.9 percentage points" in text
    assert "2.8x its normal volume" in text


def test_explanation_uses_associative_language_without_causal_evidence() -> None:
    text = _explain(SCENARIO_1_CORROBORATED_DECLINE)
    assert "coincides with" in text
    assert "A causal link has not been established." in text
    for causal in ("because", "caused by", "due to", "as a result of", "driven by news"):
        assert causal not in text.lower()


def test_explanation_may_state_cause_only_with_explicit_causal_evidence() -> None:
    established = SCENARIO_1_CORROBORATED_DECLINE.evolve(
        news=NewsEvidence(
            present=True,
            relevance=0.9,
            minutes_before_move=25,
            confidence=0.9,
            headline="Company confirms guidance withdrawal",
            causal_link_established=True,
        )
    )
    text = _explain(established)
    assert "attributed to" in text
    assert "coincides with" not in text


def test_explanation_communicates_uncertainty_when_evidence_is_missing() -> None:
    text = _explain(SCENARIO_5_NO_VOLATILITY_HISTORY)
    assert "indicative rather than complete" in text


def test_explanation_declares_a_fallback_volume_baseline() -> None:
    """The user must not be told 'normal volume' when it was a coarser baseline."""
    text = _explain(SCENARIO_6_FALLBACK_VOLUME_BASELINE)
    assert "average daily volume" in text


def test_explanation_never_gives_investment_advice() -> None:
    for obs in (SCENARIO_1_CORROBORATED_DECLINE, SCENARIO_5_NO_VOLATILITY_HISTORY):
        result = score_observation(obs)
        combined = f"{explain(result)} {explain_score(result)}".lower()
        for banned in ("buy", "sell", "hold", "recommend", "should ", "target price"):
            assert banned not in combined


def test_explanation_mentions_no_numbers_that_were_not_computed() -> None:
    """Every figure quoted must trace back to an available signal."""
    result = score_observation(SCENARIO_5_NO_VOLATILITY_HISTORY)
    text = explain(result)
    # Volatility was unavailable, so no volatility claim may appear.
    assert "volatility" not in text.lower()


def test_score_explanation_reports_drivers_and_confidence() -> None:
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    text = explain_score(result)
    assert "Attention score" in text
    assert result.severity.value in text
    assert "Confidence:" in text


def test_explanation_is_deterministic() -> None:
    result = score_observation(SCENARIO_1_CORROBORATED_DECLINE)
    assert explain(result) == explain(result)
