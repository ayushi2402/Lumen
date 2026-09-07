"""Runs the deterministic engine over fixture scenarios.

This is the business logic behind ``GET /intelligence/demo``. It lives here
rather than in the route handler so the endpoint stays a thin transport layer,
and so the same code path can be exercised directly by tests.

Development and demonstration only. No live market data is involved, and this
module must not become the path production data flows through.
"""

from __future__ import annotations

from typing import Any

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.events import MarketEvent, ingest_stream
from app.intelligence.explain import explain, explain_score
from app.intelligence.models import AttentionResult, Availability, MarketObservation
from app.intelligence.scoring import score_observation
from app.services.fixtures import (
    ALL_SINGLE_OBSERVATIONS,
    SCENARIO_7_EVOLVING_DECLINE,
    SCENARIO_DESCRIPTIONS,
)


def analyze(
    obs: MarketObservation,
    behavioral_relevance: float = 0.0,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> tuple[AttentionResult, str]:
    """Score one observation and render its deterministic explanation."""
    result = score_observation(obs, behavioral_relevance, config)
    return result, explain(result)


def _serialize_result(result: AttentionResult, explanation: str) -> dict[str, Any]:
    """Shape a result for the API.

    Unavailable signals are reported alongside available ones - the frontend
    needs to show what could *not* be measured just as much as what could.
    """
    return {
        "symbol": result.symbol,
        "scenario": SCENARIO_DESCRIPTIONS.get(result.symbol, ""),
        "timestamp": result.timestamp,
        "objective_score": result.objective_score,
        "personalization_adjustment": result.personalization_adjustment,
        "final_score": result.final_score,
        "severity": result.severity.value,
        "direction": result.direction.value,
        "meaningful": result.meaningful,
        "breakdown": result.breakdown,
        "confidence": result.confidence.value,
        "confidence_report": result.confidence_report.model_dump(),
        "explanation": explanation,
        "score_explanation": explain_score(result),
        "evidence": result.evidence,
        "signals": [
            {
                "type": s.signal_type.value,
                "family": s.family.value,
                "availability": s.availability.value,
                "value": s.value,
                "normalized_score": s.normalized_score,
                "direction": s.direction.value,
                "evidence": s.evidence,
                "confidence": s.confidence,
                "detail": s.detail,
            }
            for s in result.signals
        ],
        "unavailable_signals": [
            {"type": s.signal_type.value, "state": s.availability.value, "reason": s.evidence}
            for s in result.signals
            if s.availability is not Availability.AVAILABLE
        ],
    }


def _serialize_event(event: MarketEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "symbol": event.symbol,
        "headline": event.headline,
        "started_at": event.started_at,
        "last_updated_at": event.last_updated_at,
        "direction": event.direction.value,
        "current_score": event.current_score,
        "peak_score": event.peak_score,
        "severity": event.severity.value,
        "status": event.status.value,
        "confidence": event.confidence.value,
        "observation_count": event.observation_count,
        "supporting_signals": [
            {
                "type": s.signal_type.value,
                "value": s.value,
                "evidence": s.evidence,
            }
            for s in event.supporting_signals
        ],
        "history": [
            {
                "timestamp": p.timestamp,
                "score": p.score,
                "severity": p.severity.value,
            }
            for p in event.history
        ],
    }


def run_demo(config: IntelligenceConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    """Run every fixture scenario through the engine.

    Returns single-observation analyses ranked by final score, plus the
    evolving-event scenario folded into events, so both halves of the engine
    are visible from one endpoint.
    """
    analyses = []
    for obs in ALL_SINGLE_OBSERVATIONS:
        result, explanation = analyze(obs, config=config)
        analyses.append(_serialize_result(result, explanation))

    analyses.sort(key=lambda item: item["final_score"], reverse=True)

    evolving_results = [score_observation(obs, config=config) for obs in SCENARIO_7_EVOLVING_DECLINE]
    events = ingest_stream(evolving_results, config)

    meaningful = [a for a in analyses if a["meaningful"]]

    return {
        "disclaimer": (
            "Synthetic fixture data for development only. Not live market data, "
            "and not investment advice."
        ),
        "config": {
            "weights": config.weights.model_dump(),
            "meaningful_min_score": config.meaningful_min_score,
            "meaningful_min_independent_signals": config.meaningful_min_independent_signals,
            "personalization_max_adjustment": config.personalization_max_adjustment,
        },
        "needs_attention": [a["symbol"] for a in meaningful[:3]],
        "analyses": analyses,
        "evolving_event_scenario": {
            "description": (
                "INFY observed at -2%, -3% and -4%. The engine must produce one "
                "evolving event, not three."
            ),
            "observations": len(SCENARIO_7_EVOLVING_DECLINE),
            "events_created": len(events),
            "events": [_serialize_event(e) for e in events],
        },
    }
