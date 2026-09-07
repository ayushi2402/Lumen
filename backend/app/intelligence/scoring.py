"""Attention scoring: turning signals into a 0-100 score with a severity band.

Design decisions worth stating explicitly, because they are what stop this
from being "sort by percent change":

1. **Scoring is per family, not per signal.** Absolute movement, historical-
   normalized movement and volatility-normalized movement are three views of
   one phenomenon. Averaging within a family stops correlated signals from
   stacking into an inflated score.

2. **No renormalization over missing families.** A family with no data
   contributes zero points and the score is *not* rescaled to compensate.
   Rescaling would mean an unverifiable move scores the same as a fully
   corroborated one, which is exactly the inference LUMEN must not make.
   Missing evidence lowers the score *and* is reported through confidence.

3. **Meaningfulness needs corroboration, not magnitude.** The gate requires
   both a minimum score and a minimum number of independent families, so no
   single spectacular reading can manufacture an event on its own.

4. **Direction and significance never touch.** Direction is computed only from
   directional signals; the score is computed only from magnitudes. Nothing
   here maps a direction onto an action.
"""

from __future__ import annotations

from statistics import mean

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.models import (
    AttentionResult,
    Confidence,
    ConfidenceReport,
    Direction,
    MarketObservation,
    Severity,
    Signal,
    SignalFamily,
)
from app.intelligence.signals import detect_all

_OPPOSITES = {Direction.POSITIVE: Direction.NEGATIVE, Direction.NEGATIVE: Direction.POSITIVE}


# ---------------------------------------------------------------------------
# Family aggregation
# ---------------------------------------------------------------------------


def family_strengths(signals: list[Signal]) -> dict[SignalFamily, float]:
    """Mean strength of the available signals within each family.

    Only available signals participate. A family with nothing available is
    absent from the result rather than present with a zero, so callers can
    distinguish "measured as unremarkable" from "not measured".
    """
    grouped: dict[SignalFamily, list[float]] = {}
    for signal in signals:
        if signal.is_available and signal.normalized_score is not None:
            grouped.setdefault(signal.family, []).append(signal.normalized_score)
    return {family: mean(scores) for family, scores in grouped.items()}


def score_breakdown(
    signals: list[Signal], config: IntelligenceConfig = DEFAULT_CONFIG
) -> dict[str, float]:
    """Points contributed by each family. Powers "How LUMEN calculated this".

    Every family appears, including those contributing zero, so the frontend
    can render a complete picture rather than a selective one.
    """
    strengths = family_strengths(signals)
    weights = config.weights.as_map()
    return {
        family.value: round(weights[family] * strengths.get(family, 0.0), 2)
        for family in weights
    }


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def aggregate_direction(signals: list[Signal]) -> Direction:
    """Consensus direction across contributing directional signals.

    Volume and volatility are excluded - they corroborate that something
    happened without saying which way. Genuine disagreement yields ``MIXED``
    rather than a silent majority vote.
    """
    directions = [
        s.direction
        for s in signals
        if s.is_available
        and s.is_directional
        and s.direction is not Direction.NEUTRAL
        and (s.normalized_score or 0.0) > 0.0
    ]
    if not directions:
        return Direction.NEUTRAL
    unique = set(directions)
    if len(unique) == 1:
        return directions[0]
    return Direction.MIXED


def count_contradictions(signals: list[Signal], direction: Direction) -> int:
    """Directional signals pointing against the aggregate direction."""
    opposite = _OPPOSITES.get(direction)
    if opposite is None:
        return 0
    return sum(
        1
        for s in signals
        if s.is_available and s.is_directional and s.direction is opposite
    )


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def assess_confidence(
    signals: list[Signal],
    direction: Direction,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> ConfidenceReport:
    """How well-established the picture is - independent of how big it is.

    Considers breadth of independent corroboration, how much of the intended
    evidence base was actually available, the quality of the signals that were
    (a fallback volume baseline counts for less), and outright contradictions.
    """
    strengths = family_strengths(signals)
    contributing = {f: s for f, s in strengths.items() if s > 0.0}
    independent = len(contributing)

    families_considered = len(config.weights.as_map())
    families_available = len(strengths)
    coverage = families_available / families_considered if families_considered else 0.0

    quality = [
        s.confidence for s in signals if s.is_available and (s.normalized_score or 0) > 0
    ]
    avg_quality = mean(quality) if quality else 0.0
    contradictions = count_contradictions(signals, direction)
    used_fallback_baseline = any(
        s.detail.get("is_fallback_baseline")
        for s in signals
        if s.is_available and (s.normalized_score or 0) > 0
    )

    reasons: list[str] = []
    if independent >= config.confidence_high_min_signals:
        reasons.append(f"{independent} independent signal families agree.")
    else:
        reasons.append(f"Only {independent} independent signal family(ies) contributed.")

    missing = [f.value for f in config.weights.as_map() if f not in strengths]
    if missing:
        reasons.append(f"No data for: {', '.join(sorted(missing))}.")
    if contradictions:
        reasons.append(f"{contradictions} signal(s) point the other way.")
    if used_fallback_baseline:
        reasons.append("A fallback volume baseline was used instead of same-time-of-day.")

    if (
        independent <= config.confidence_low_max_signals
        or coverage < config.confidence_low_max_coverage
    ):
        level = Confidence.LOW
    elif (
        independent >= config.confidence_high_min_signals
        and coverage >= config.confidence_high_min_coverage
        and avg_quality >= config.confidence_high_min_avg
        and contradictions == 0
    ):
        level = Confidence.HIGH
    else:
        level = Confidence.MEDIUM

    # Contradictory evidence can never be described as high confidence.
    if contradictions and level is Confidence.HIGH:
        level = Confidence.MEDIUM

    # Nor can a reading resting on a declared fallback baseline. Recording
    # which baseline was used only matters if it changes something downstream.
    if used_fallback_baseline and level is Confidence.HIGH:
        level = Confidence.MEDIUM

    return ConfidenceReport(
        level=level,
        independent_signals=independent,
        families_available=families_available,
        families_considered=families_considered,
        coverage=round(coverage, 4),
        contradictions=contradictions,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


def severity_for(score: float, config: IntelligenceConfig = DEFAULT_CONFIG) -> Severity:
    """Map a 0-100 score onto its band. Bands are inclusive at the lower bound."""
    if score >= config.severity_critical:
        return Severity.CRITICAL
    if score >= config.severity_high_attention:
        return Severity.HIGH_ATTENTION
    if score >= config.severity_worth_watching:
        return Severity.WORTH_WATCHING
    return Severity.NOISE


# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------


def personalize(
    objective_score: float,
    behavioral_relevance: float,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> tuple[float, float]:
    """Apply a capped behavioural adjustment. Returns ``(adjustment, final_score)``.

    Objective market significance is the foundation and is never rewritten.
    Two guardrails enforce that:

    * The adjustment is hard-capped at ``personalization_max_adjustment``.
    * Below ``personalization_min_objective_score`` no adjustment applies at
      all, so heavy user interest cannot lift market noise toward a higher
      severity band.

    ``behavioral_relevance`` is a 0-100 summary of prior interaction produced
    elsewhere. This is deliberately arithmetic, not a learned model.
    """
    relevance = max(0.0, min(100.0, behavioral_relevance))
    if objective_score < config.personalization_min_objective_score:
        return 0.0, round(objective_score, 2)

    adjustment = config.personalization_max_adjustment * (relevance / 100.0)
    final = max(0.0, min(100.0, objective_score + adjustment))
    return round(adjustment, 2), round(final, 2)


# ---------------------------------------------------------------------------
# Top-level scoring
# ---------------------------------------------------------------------------


def score_signals(
    symbol: str,
    timestamp,
    signals: list[Signal],
    behavioral_relevance: float = 0.0,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> AttentionResult:
    """Combine detected signals into a complete, explainable verdict."""
    breakdown = score_breakdown(signals, config)
    objective = round(min(100.0, max(0.0, sum(breakdown.values()))), 2)

    direction = aggregate_direction(signals)
    confidence_report = assess_confidence(signals, direction, config)

    adjustment, final = personalize(objective, behavioral_relevance, config)

    meaningful = (
        objective >= config.meaningful_min_score
        and confidence_report.independent_signals
        >= config.meaningful_min_independent_signals
    )

    evidence = [
        s.evidence for s in signals if s.is_available and (s.normalized_score or 0) > 0
    ]

    return AttentionResult(
        symbol=symbol,
        timestamp=timestamp,
        objective_score=objective,
        personalization_adjustment=adjustment,
        final_score=final,
        severity=severity_for(final, config),
        direction=direction,
        meaningful=meaningful,
        signals=signals,
        breakdown=breakdown,
        evidence=evidence,
        confidence=confidence_report.level,
        confidence_report=confidence_report,
    )


def score_observation(
    obs: MarketObservation,
    behavioral_relevance: float = 0.0,
    config: IntelligenceConfig = DEFAULT_CONFIG,
) -> AttentionResult:
    """Detect every signal for an observation and score it. The engine entrypoint."""
    return score_signals(
        symbol=obs.symbol,
        timestamp=obs.timestamp,
        signals=detect_all(obs, config),
        behavioral_relevance=behavioral_relevance,
        config=config,
    )
