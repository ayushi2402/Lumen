"""Deterministic signal detection.

Every function here is pure: it takes a ``MarketObservation`` plus config and
returns a ``Signal``. No I/O, no database, no LLM, no global state, no clock
reads. The same inputs always produce the same output, which is what makes the
engine testable without a market, a network, or a token.

The central discipline of this module: **missing data never becomes a zero.**
A stock with no volatility history does not have zero volatility, and a stock
with no same-time-of-day volume baseline does not have normal volume. Those
cases return ``UNAVAILABLE`` or ``INSUFFICIENT_HISTORY`` so that scoring and
confidence can treat "no evidence" differently from "evidence of nothing".
"""

from __future__ import annotations

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.models import (
    Availability,
    Direction,
    MarketObservation,
    Signal,
    SignalType,
    VolumeBaseline,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def ramp(magnitude: float, floor: float, full_strength: float) -> float:
    """Map a magnitude onto 0-1 with a noise floor and a saturation point.

    Below ``floor`` the signal is indistinguishable from routine market
    behaviour and scores 0. At or above ``full_strength`` it scores 1.0. In
    between it ramps linearly - chosen over a smooth curve because a straight
    line is explainable to a user in one sentence.
    """
    if full_strength <= floor:
        return 1.0 if magnitude >= full_strength else 0.0
    if magnitude <= floor:
        return 0.0
    return min(1.0, (magnitude - floor) / (full_strength - floor))


def direction_of(value: float, deadzone: float = 0.0) -> Direction:
    """Sign of a value as a Direction, with an optional neutral deadzone."""
    if value > deadzone:
        return Direction.POSITIVE
    if value < -deadzone:
        return Direction.NEGATIVE
    return Direction.NEUTRAL


def _unavailable(
    signal_type: SignalType,
    reason: str,
    availability: Availability = Availability.UNAVAILABLE,
) -> Signal:
    """Build an explicitly empty signal. Never carries a value or a score."""
    return Signal(
        signal_type=signal_type,
        availability=availability,
        value=None,
        normalized_score=None,
        direction=Direction.NEUTRAL,
        evidence=reason,
        confidence=0.0,
        detail={"reason": reason},
    )


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


# ---------------------------------------------------------------------------
# A. Absolute price movement
# ---------------------------------------------------------------------------


def absolute_price_movement(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Raw percentage move. The least informative signal, deliberately weighted low."""
    daily_return = obs.daily_return
    if daily_return is None:
        return _unavailable(SignalType.PRICE_MOVEMENT, "No price data available.")

    strength = ramp(
        abs(daily_return),
        config.min_price_move_percent,
        config.price_move_full_strength,
    )
    verb = "rose" if daily_return >= 0 else "fell"
    return Signal(
        signal_type=SignalType.PRICE_MOVEMENT,
        availability=Availability.AVAILABLE,
        value=round(daily_return, 4),
        normalized_score=round(strength, 4),
        direction=direction_of(daily_return, config.min_price_move_percent),
        evidence=f"{obs.symbol} {verb} {abs(daily_return):.2f}%.",
        confidence=config.confidence_full_history,
        detail={"price": obs.price, "previous_price": obs.previous_price},
    )


# ---------------------------------------------------------------------------
# B. Historical-normalized movement
# ---------------------------------------------------------------------------


def historical_normalized_movement(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Today's move as a multiple of this stock's own typical daily move.

    This is what lets a 2% move in a placid large-cap outrank a 2% move in a
    stock that swings 3% every day.
    """
    signal_type = SignalType.HISTORICAL_NORMALIZED_MOVEMENT
    if obs.daily_return is None:
        return _unavailable(signal_type, "No price data available.")
    if obs.typical_daily_move is None:
        return _unavailable(
            signal_type,
            "No typical-movement history available.",
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.typical_move_sample_days < config.min_typical_move_sample_days:
        return _unavailable(
            signal_type,
            (
                f"Only {obs.typical_move_sample_days} sessions of movement history "
                f"({config.min_typical_move_sample_days} required)."
            ),
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.typical_daily_move <= 0:
        return _unavailable(
            signal_type,
            "Typical daily movement is zero; ratio undefined.",
            Availability.INSUFFICIENT_HISTORY,
        )

    ratio = abs(obs.daily_return) / obs.typical_daily_move
    strength = ramp(ratio, 1.0, config.historical_ratio_full_strength)
    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(ratio, 4),
        normalized_score=round(strength, 4),
        direction=direction_of(obs.daily_return, config.min_price_move_percent),
        evidence=(
            f"The move is {ratio:.1f}x {obs.symbol}'s typical daily movement "
            f"of {obs.typical_daily_move:.2f}%."
        ),
        confidence=config.confidence_full_history,
        detail={
            "typical_daily_move": obs.typical_daily_move,
            "sample_days": obs.typical_move_sample_days,
        },
    )


# ---------------------------------------------------------------------------
# C. Volatility-normalized movement
# ---------------------------------------------------------------------------


def volatility_normalized_movement(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """The move expressed in standard deviations of historical daily returns."""
    signal_type = SignalType.VOLATILITY_NORMALIZED_MOVEMENT
    if obs.daily_return is None:
        return _unavailable(signal_type, "No price data available.")
    if obs.historical_volatility is None:
        return _unavailable(
            signal_type,
            "No volatility history available.",
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.volatility_sample_days < config.min_volatility_sample_days:
        return _unavailable(
            signal_type,
            (
                f"Only {obs.volatility_sample_days} sessions of volatility history "
                f"({config.min_volatility_sample_days} required)."
            ),
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.historical_volatility <= 0:
        return _unavailable(
            signal_type,
            "Historical volatility is zero; z-score undefined.",
            Availability.INSUFFICIENT_HISTORY,
        )

    sigma = abs(obs.daily_return) / obs.historical_volatility
    strength = ramp(sigma, 1.0, config.volatility_sigma_full_strength)
    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(sigma, 4),
        normalized_score=round(strength, 4),
        direction=direction_of(obs.daily_return, config.min_price_move_percent),
        evidence=(
            f"That is a {sigma:.1f}-sigma move against a historical daily "
            f"volatility of {obs.historical_volatility:.2f}%."
        ),
        confidence=config.confidence_full_history,
        detail={
            "historical_volatility": obs.historical_volatility,
            "sample_days": obs.volatility_sample_days,
        },
    )


# ---------------------------------------------------------------------------
# D / E. Relative performance
# ---------------------------------------------------------------------------


def _relative_performance(
    obs: MarketObservation,
    reference_return: float | None,
    signal_type: SignalType,
    reference_label: str,
    config: IntelligenceConfig,
) -> Signal:
    """Excess return over a reference, in percentage points."""
    if obs.daily_return is None:
        return _unavailable(signal_type, "No price data available.")
    if reference_return is None:
        return _unavailable(signal_type, f"No {reference_label} data available.")

    excess = obs.daily_return - reference_return
    strength = ramp(abs(excess), config.min_relative_pp, config.relative_pp_full_strength)
    verb = "outperforming" if excess >= 0 else "underperforming"
    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(excess, 4),
        normalized_score=round(strength, 4),
        direction=direction_of(excess, config.min_relative_pp),
        evidence=(
            f"{verb.capitalize()} {reference_label} by {abs(excess):.1f} "
            f"percentage points ({reference_label} {_fmt_pct(reference_return)})."
        ),
        confidence=config.confidence_full_history,
        detail={"reference": reference_label, "reference_return": reference_return},
    )


def relative_to_benchmark(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Excess return vs NIFTY. A 5% fall on a 4.8% down day is not a stock story."""
    return _relative_performance(
        obs, obs.benchmark_return, SignalType.RELATIVE_TO_BENCHMARK, "NIFTY", config
    )


def relative_to_sector(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Excess return vs sector, computed independently of the benchmark."""
    return _relative_performance(
        obs, obs.sector_return, SignalType.RELATIVE_TO_SECTOR, "its sector", config
    )


# ---------------------------------------------------------------------------
# F. Abnormal volume
# ---------------------------------------------------------------------------


def abnormal_volume(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Volume against the best baseline actually available.

    Baseline selection is explicit and always recorded:

    1. Same-time-of-day average, when enough sessions back it. Preferred,
       because volume is heavily shaped by time of session.
    2. Full-day average, as a declared fallback carrying reduced confidence.
    3. Neither - the signal is unavailable. A same-time-of-day baseline is
       never synthesised from a full-day average.
    """
    signal_type = SignalType.ABNORMAL_VOLUME
    if obs.volume is None:
        return _unavailable(signal_type, "No volume data available.")

    baseline_value: float | None = None
    baseline_kind = VolumeBaseline.NONE
    confidence = 0.0

    has_same_time = (
        obs.same_time_of_day_volume is not None
        and obs.same_time_of_day_sample_days >= config.min_same_time_volume_sample_days
    )
    if has_same_time:
        baseline_value = obs.same_time_of_day_volume
        baseline_kind = VolumeBaseline.SAME_TIME_OF_DAY
        confidence = config.confidence_full_history
    elif obs.average_volume is not None:
        baseline_value = obs.average_volume
        baseline_kind = VolumeBaseline.FULL_DAY_AVERAGE
        confidence = config.confidence_fallback_baseline

    if baseline_value is None or baseline_value <= 0:
        return _unavailable(
            signal_type,
            "No usable volume baseline available.",
            Availability.INSUFFICIENT_HISTORY,
        )

    ratio = obs.volume / baseline_value
    strength = ramp(ratio, config.min_volume_ratio, config.volume_ratio_full_strength)

    baseline_phrase = (
        "its normal volume for this point of the session"
        if baseline_kind is VolumeBaseline.SAME_TIME_OF_DAY
        else "its average daily volume (full-day fallback baseline)"
    )
    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(ratio, 4),
        # Volume has magnitude but no direction - it corroborates a move
        # without indicating which way the move went.
        direction=Direction.NEUTRAL,
        normalized_score=round(strength, 4),
        evidence=f"Trading at {ratio:.1f}x {baseline_phrase}.",
        confidence=confidence,
        detail={
            "baseline_kind": baseline_kind.value,
            "baseline_value": baseline_value,
            "is_fallback_baseline": baseline_kind is VolumeBaseline.FULL_DAY_AVERAGE,
            "same_time_of_day_sample_days": obs.same_time_of_day_sample_days,
        },
    )


# ---------------------------------------------------------------------------
# G / H. Volatility anomaly
# ---------------------------------------------------------------------------


def volatility_anomaly(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Today's realized intraday volatility against its historical level.

    Requires both a historical baseline and enough intraday observations to
    have realized anything. With too few ticks the honest answer is
    "insufficient history", not "volatility is normal".
    """
    signal_type = SignalType.VOLATILITY_ANOMALY
    if obs.historical_volatility is None:
        return _unavailable(
            signal_type,
            "No volatility history available.",
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.volatility_sample_days < config.min_volatility_sample_days:
        return _unavailable(
            signal_type,
            (
                f"Only {obs.volatility_sample_days} sessions of volatility history "
                f"({config.min_volatility_sample_days} required)."
            ),
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.intraday_volatility is None:
        return _unavailable(
            signal_type,
            "No intraday volatility computed.",
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.intraday_observation_count < config.min_intraday_observations:
        return _unavailable(
            signal_type,
            (
                f"Only {obs.intraday_observation_count} intraday observations "
                f"({config.min_intraday_observations} required)."
            ),
            Availability.INSUFFICIENT_HISTORY,
        )
    if obs.historical_volatility <= 0:
        return _unavailable(
            signal_type,
            "Historical volatility is zero; ratio undefined.",
            Availability.INSUFFICIENT_HISTORY,
        )

    ratio = obs.intraday_volatility / obs.historical_volatility
    strength = ramp(
        ratio, config.min_volatility_ratio, config.volatility_ratio_full_strength
    )
    descriptor = "elevated" if ratio >= 1.0 else "subdued"
    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(ratio, 4),
        normalized_score=round(strength, 4),
        direction=Direction.NEUTRAL,
        evidence=(
            f"Intraday volatility is {descriptor} at {ratio:.1f}x its historical level."
        ),
        confidence=config.confidence_full_history,
        detail={
            "intraday_volatility": obs.intraday_volatility,
            "historical_volatility": obs.historical_volatility,
            "intraday_observation_count": obs.intraday_observation_count,
        },
    )


# ---------------------------------------------------------------------------
# I. Gap detection
# ---------------------------------------------------------------------------


def gap_signal(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Opening gap vs the previous relevant close.

    When a history of gap sizes exists, the gap is additionally judged against
    that distribution and the stronger of the two readings is used - a 1.5%
    gap is routine for some stocks and remarkable for others.
    """
    signal_type = SignalType.GAP
    if obs.gap_percent is None:
        return _unavailable(signal_type, "No opening gap data available.")

    magnitude = abs(obs.gap_percent)
    absolute_strength = ramp(
        magnitude, config.min_gap_percent, config.gap_percent_full_strength
    )

    sigma: float | None = None
    strength = absolute_strength
    if obs.historical_gap_stdev is not None and obs.historical_gap_stdev > 0:
        sigma = magnitude / obs.historical_gap_stdev
        strength = max(absolute_strength, ramp(sigma, 1.0, config.gap_sigma_full_strength))

    word = "up" if obs.gap_percent >= 0 else "down"
    evidence = f"Gapped {word} {magnitude:.2f}% at the open."
    if sigma is not None:
        evidence += f" That is {sigma:.1f}x its typical opening gap."

    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(obs.gap_percent, 4),
        normalized_score=round(strength, 4),
        direction=direction_of(obs.gap_percent, config.min_gap_percent),
        evidence=evidence,
        confidence=(
            config.confidence_full_history
            if sigma is not None
            else config.confidence_fallback_baseline
        ),
        detail={
            "gap_percent": obs.gap_percent,
            "gap_sigma": round(sigma, 4) if sigma is not None else None,
            "has_gap_history": sigma is not None,
        },
    )


# ---------------------------------------------------------------------------
# J. News / event support
# ---------------------------------------------------------------------------


def news_signal(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> Signal:
    """Relevance of associated news. Ingestion is not implemented.

    This function only consumes structured evidence someone else established.
    It never asserts that news *caused* a move - ``causal_link_established`` is
    carried through untouched for the explanation layer to respect.
    """
    signal_type = SignalType.NEWS_EVENT
    if obs.news is None:
        return _unavailable(signal_type, "No news data available.")
    if not obs.news.present:
        return _unavailable(signal_type, "No relevant news identified.")

    strength = obs.news.relevance
    timing_known = obs.news.minutes_before_move is not None
    if not timing_known:
        strength *= config.news_unknown_timing_penalty

    if timing_known:
        timing_phrase = f"published {obs.news.minutes_before_move} minutes before the move"
    else:
        timing_phrase = "with unknown timing relative to the move"

    return Signal(
        signal_type=signal_type,
        availability=Availability.AVAILABLE,
        value=round(obs.news.relevance, 4),
        normalized_score=round(min(1.0, strength), 4),
        direction=Direction.NEUTRAL,
        evidence=f"Relevant news {timing_phrase}.",
        confidence=obs.news.confidence,
        detail={
            "headline": obs.news.headline,
            "relevance": obs.news.relevance,
            "minutes_before_move": obs.news.minutes_before_move,
            "timing_known": timing_known,
            "causal_link_established": obs.news.causal_link_established,
        },
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

SIGNAL_FUNCTIONS = (
    absolute_price_movement,
    historical_normalized_movement,
    volatility_normalized_movement,
    relative_to_benchmark,
    relative_to_sector,
    abnormal_volume,
    volatility_anomaly,
    gap_signal,
    news_signal,
)


def detect_all(
    obs: MarketObservation, config: IntelligenceConfig = DEFAULT_CONFIG
) -> list[Signal]:
    """Run every signal detector. Unavailable signals are returned, not dropped.

    Keeping the empty ones is the point: scoring and confidence both need to
    know what could not be measured.
    """
    return [fn(obs, config) for fn in SIGNAL_FUNCTIONS]
