"""Deterministic template explanations.

No LLM is involved. This module converts finalized structured evidence into
prose, and it is what LUMEN falls back to whenever the AI layer is
unavailable - which is why it must be correct on its own terms, not a
placeholder.

Two rules govern every sentence produced here:

1. **Only facts present in the structured input.** Every number is read from a
   signal that the deterministic engine already computed. Nothing is inferred,
   rounded into a different claim, or embellished.

2. **Never assert causation without explicit causal evidence.** Co-occurrence
   in time is not cause. Unless ``causal_link_established`` is set on the news
   evidence, the language stays associative: "coincides with", "occurred
   alongside", "may be related to". This is a correctness constraint, not a
   stylistic preference - claiming a cause LUMEN cannot support is the single
   most damaging thing the product could do.
"""

from __future__ import annotations

from app.intelligence.models import (
    AttentionResult,
    Confidence,
    Direction,
    Signal,
    SignalType,
)

_CONFIDENCE_CAVEAT = {
    Confidence.HIGH: "",
    Confidence.MEDIUM: (
        "Some supporting context was unavailable, so treat this reading as "
        "indicative rather than complete."
    ),
    Confidence.LOW: (
        "This is based on limited evidence and should be treated with caution."
    ),
}


def _by_type(result: AttentionResult) -> dict[SignalType, Signal]:
    return {
        s.signal_type: s
        for s in result.signals
        if s.is_available and (s.normalized_score or 0.0) > 0.0
    }


def _movement_clause(signals: dict[SignalType, Signal], symbol: str) -> str:
    price = signals.get(SignalType.PRICE_MOVEMENT)
    if price is None or price.value is None:
        return f"{symbol} showed no significant price movement"
    verb = "rose" if price.value >= 0 else "fell"
    return f"{symbol} {verb} {abs(price.value):.1f}%"


def _relative_clause(signals: dict[SignalType, Signal]) -> str | None:
    signal = signals.get(SignalType.RELATIVE_TO_BENCHMARK)
    if signal is None or signal.value is None:
        return None
    verb = "outperforming" if signal.value >= 0 else "underperforming"
    return f"{verb} NIFTY by {abs(signal.value):.1f} percentage points"


def _sector_clause(signals: dict[SignalType, Signal]) -> str | None:
    signal = signals.get(SignalType.RELATIVE_TO_SECTOR)
    if signal is None or signal.value is None:
        return None
    verb = "ahead of" if signal.value >= 0 else "behind"
    return f"{abs(signal.value):.1f} percentage points {verb} its sector"


def _volume_clause(signals: dict[SignalType, Signal]) -> str | None:
    signal = signals.get(SignalType.ABNORMAL_VOLUME)
    if signal is None or signal.value is None:
        return None
    # The baseline actually used is stated, so a fallback is never passed off
    # as a same-time-of-day comparison.
    qualifier = (
        "its average daily volume"
        if signal.detail.get("is_fallback_baseline")
        else "its normal volume"
    )
    return f"while trading at {signal.value:.1f}x {qualifier}"


def _gap_clause(signals: dict[SignalType, Signal]) -> str | None:
    signal = signals.get(SignalType.GAP)
    if signal is None or signal.value is None:
        return None
    word = "up" if signal.value >= 0 else "down"
    return f"The move followed a {abs(signal.value):.1f}% gap {word} at the open."


def _volatility_clause(signals: dict[SignalType, Signal]) -> str | None:
    signal = signals.get(SignalType.VOLATILITY_ANOMALY)
    if signal is None or signal.value is None:
        return None
    descriptor = "elevated" if signal.value >= 1.0 else "subdued"
    return (
        f"Intraday volatility has been {descriptor} at {signal.value:.1f}x "
        "its historical level."
    )


def _news_clause(signals: dict[SignalType, Signal]) -> str | None:
    """Associative by default. Causal only with explicit causal evidence."""
    signal = signals.get(SignalType.NEWS_EVENT)
    if signal is None:
        return None

    minutes = signal.detail.get("minutes_before_move")
    headline = signal.detail.get("headline")
    causal = bool(signal.detail.get("causal_link_established"))

    if minutes is not None:
        timing = f"published {minutes} minutes beforehand"
    else:
        timing = "whose timing relative to the move is not established"

    # The only branch permitted to state a cause, and only because a source
    # established it upstream.
    verb = "is attributed to" if causal else "coincides with"
    sentence = f"The move {verb} relevant news {timing}."
    if headline:
        sentence += f' Reported: "{headline}".'
    if not causal:
        sentence += " A causal link has not been established."
    return sentence


def explain(result: AttentionResult) -> str:
    """Render a finalized ``AttentionResult`` as a concise factual explanation.

    Produces something like:

        "RELIANCE fell 4.2%, underperforming NIFTY by 3.5 percentage points,
        while trading at 2.8x its normal volume. The move coincides with
        relevant news published 25 minutes beforehand. A causal link has not
        been established."
    """
    signals = _by_type(result)

    if not signals:
        return (
            f"No measurable signals were available for {result.symbol} at this time."
        )

    # --- Opening sentence: movement, relative context, volume ---------------
    parts = [_movement_clause(signals, result.symbol)]
    relative = _relative_clause(signals) or _sector_clause(signals)
    if relative:
        parts.append(relative)
    volume = _volume_clause(signals)
    if volume:
        parts.append(volume)
    sentences = [", ".join(parts) + "."]

    # --- Supporting sentences ----------------------------------------------
    for clause in (_gap_clause(signals), _volatility_clause(signals), _news_clause(signals)):
        if clause:
            sentences.append(clause)

    # --- Uncertainty --------------------------------------------------------
    caveat = _CONFIDENCE_CAVEAT[result.confidence]
    if caveat:
        sentences.append(caveat)

    return " ".join(sentences)


def explain_score(result: AttentionResult) -> str:
    """One line describing how the score was reached. Never advisory.

    Deliberately contains no recommendation: it reports attention and
    direction as separate facts and stops there.
    """
    contributors = sorted(
        ((family, points) for family, points in result.breakdown.items() if points > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    if not contributors:
        return f"Attention score {result.final_score:.0f} ({result.severity.value})."

    top = ", ".join(f"{family.replace('_', ' ')} {points:.0f}" for family, points in contributors)
    direction_note = (
        "" if result.direction is Direction.NEUTRAL else f" Direction: {result.direction.value}."
    )
    return (
        f"Attention score {result.final_score:.0f} ({result.severity.value}), "
        f"driven by {top}.{direction_note} "
        f"Confidence: {result.confidence.value}."
    )
