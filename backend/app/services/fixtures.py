"""Synthetic market scenarios for testing and the development demo endpoint.

These are hand-built, not random. Each scenario isolates one behaviour the
engine must get right, and the numbers were chosen to make that behaviour
observable - a randomly generated fixture proves nothing when a test fails.

**This is not market data.** Nothing here is fetched, cached, or presented as
real. It exists so the engine can be exercised without a broker token, without
a database, and outside NSE trading hours.

Prices and volumes are plausible for NSE large-caps; the symbol names are real
instruments but every number attached to them is invented for testing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.intelligence.models import MarketObservation, NewsEvidence

# A fixed session date keeps every fixture and test fully deterministic.
SESSION_DATE = datetime(2026, 3, 12, 11, 0, 0)


def _at(hour: int, minute: int) -> datetime:
    return SESSION_DATE.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Scenario 1 - fully corroborated decline
# Large negative move, stable index, abnormal volume, relevant news.
# Expectation: High Attention or Critical, high confidence.
# ---------------------------------------------------------------------------
SCENARIO_1_CORROBORATED_DECLINE = MarketObservation(
    symbol="RELIANCE",
    timestamp=_at(11, 0),
    price=1_245.30,
    previous_price=1_300.00,  # -4.21%
    volume=18_600_000,
    average_volume=7_400_000,
    same_time_of_day_volume=6_642_857,  # ~2.8x
    same_time_of_day_sample_days=20,
    historical_volatility=1.5,
    intraday_volatility=2.6,
    volatility_sample_days=30,
    intraday_observation_count=22,
    typical_daily_move=1.2,
    typical_move_sample_days=30,
    gap_percent=-1.8,
    historical_gap_stdev=0.7,
    benchmark_return=-0.30,
    sector_return=-0.80,
    news=NewsEvidence(
        present=True,
        relevance=0.90,
        minutes_before_move=25,
        confidence=0.85,
        headline="Regulator opens review into refining margins",
        causal_link_established=False,
    ),
)


# ---------------------------------------------------------------------------
# Scenario 2 - big move that is really a market move
# Stock -5.0%, NIFTY -4.8%. Nothing stock-specific happened.
# Expectation: NOT meaningful. This is the scenario that separates LUMEN from
# a percent-change sort.
# ---------------------------------------------------------------------------
SCENARIO_2_MARKET_WIDE_SELLOFF = MarketObservation(
    symbol="HDFCBANK",
    timestamp=_at(11, 0),
    price=1_596.00,
    previous_price=1_680.00,  # -5.00%
    volume=9_350_000,
    average_volume=8_500_000,
    same_time_of_day_volume=8_500_000,  # ~1.1x - unremarkable
    same_time_of_day_sample_days=20,
    historical_volatility=1.8,
    intraday_volatility=2.2,
    volatility_sample_days=30,
    intraday_observation_count=22,
    typical_daily_move=1.4,
    typical_move_sample_days=30,
    gap_percent=-0.40,
    historical_gap_stdev=0.60,
    benchmark_return=-4.80,
    sector_return=-4.60,
    news=None,
)


# ---------------------------------------------------------------------------
# Scenario 3 - small move that still matters
# +1.5% on 4x volume with major news. Price alone would hide this entirely.
# ---------------------------------------------------------------------------
SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT = MarketObservation(
    symbol="TATAMOTORS",
    timestamp=_at(11, 0),
    price=1_015.00,
    previous_price=1_000.00,  # +1.50%
    volume=32_000_000,
    average_volume=8_000_000,
    same_time_of_day_volume=8_000_000,  # 4.0x
    same_time_of_day_sample_days=20,
    historical_volatility=1.1,
    intraday_volatility=2.0,
    volatility_sample_days=30,
    intraday_observation_count=22,
    typical_daily_move=0.9,
    typical_move_sample_days=30,
    gap_percent=0.30,
    historical_gap_stdev=0.50,
    benchmark_return=0.10,
    sector_return=0.20,
    news=NewsEvidence(
        present=True,
        relevance=0.95,
        minutes_before_move=10,
        confidence=0.90,
        headline="Board approves demerger of commercial vehicle unit",
        causal_link_established=False,
    ),
)


# ---------------------------------------------------------------------------
# Scenario 4 - large but uncorroborated move
# Same -4.2% as scenario 1, but nothing else supports it.
# Expectation: materially lower score than scenario 1.
# ---------------------------------------------------------------------------
SCENARIO_4_UNCORROBORATED_MOVE = MarketObservation(
    symbol="WIPRO",
    timestamp=_at(11, 0),
    price=249.00,
    previous_price=260.00,  # -4.23%
    volume=6_000_000,
    average_volume=6_000_000,
    same_time_of_day_volume=6_000_000,  # 1.0x - normal
    same_time_of_day_sample_days=20,
    historical_volatility=1.5,
    intraday_volatility=1.5,  # unchanged
    volatility_sample_days=30,
    intraday_observation_count=22,
    typical_daily_move=1.2,
    typical_move_sample_days=30,
    gap_percent=None,
    historical_gap_stdev=None,
    benchmark_return=-4.00,
    sector_return=-4.10,
    news=None,
)


# ---------------------------------------------------------------------------
# Scenario 5 - missing volatility history
# Identical to scenario 1 minus all volatility inputs.
# Expectation: no volatility signal is fabricated; confidence drops.
# ---------------------------------------------------------------------------
SCENARIO_5_NO_VOLATILITY_HISTORY = SCENARIO_1_CORROBORATED_DECLINE.evolve(
    symbol="ADANIPORTS",
    historical_volatility=None,
    intraday_volatility=None,
    volatility_sample_days=0,
    intraday_observation_count=0,
)


# ---------------------------------------------------------------------------
# Scenario 6 - insufficient same-time-of-day volume history
# The preferred baseline exists but only 2 sessions back it, below the
# configured minimum. Expectation: explicit fallback, never a silent one.
# ---------------------------------------------------------------------------
SCENARIO_6_FALLBACK_VOLUME_BASELINE = SCENARIO_1_CORROBORATED_DECLINE.evolve(
    symbol="SBIN",
    same_time_of_day_sample_days=2,
)

# The same case with no baseline at all - the signal must be unavailable
# rather than defaulting to "normal volume".
SCENARIO_6B_NO_VOLUME_BASELINE = SCENARIO_1_CORROBORATED_DECLINE.evolve(
    symbol="AXISBANK",
    same_time_of_day_volume=None,
    same_time_of_day_sample_days=0,
    average_volume=None,
)


# ---------------------------------------------------------------------------
# Scenario 7 - one move, observed three times
# -2% at 10:00, -3% at 10:30, -4% at 11:00. Expectation: ONE evolving event.
# ---------------------------------------------------------------------------
def _infosys_step(
    minute_offset: int,
    price: float,
    volume_ratio: float,
    benchmark: float,
    sector: float,
    intraday_vol: float,
    gap_percent: float = -0.90,
) -> MarketObservation:
    baseline_volume = 5_000_000.0
    return MarketObservation(
        symbol="INFY",
        timestamp=_at(10, 0) + timedelta(minutes=minute_offset),
        price=price,
        previous_price=1_500.00,
        volume=baseline_volume * volume_ratio,
        average_volume=baseline_volume,
        same_time_of_day_volume=baseline_volume,
        same_time_of_day_sample_days=20,
        historical_volatility=1.2,
        intraday_volatility=intraday_vol,
        volatility_sample_days=30,
        intraday_observation_count=18,
        typical_daily_move=0.9,
        typical_move_sample_days=30,
        gap_percent=gap_percent,
        historical_gap_stdev=0.50,
        benchmark_return=benchmark,
        sector_return=sector,
        news=NewsEvidence(
            present=True,
            relevance=0.70,
            minutes_before_move=15,
            confidence=0.75,
            headline="Guidance cut flagged at analyst briefing",
            causal_link_established=False,
        ),
    )


SCENARIO_7_EVOLVING_DECLINE = [
    _infosys_step(0, 1_470.00, 1.8, -0.20, -0.30, 1.9),  # -2.0%
    _infosys_step(30, 1_455.00, 2.3, -0.25, -0.40, 2.2),  # -3.0%
    _infosys_step(60, 1_440.00, 2.9, -0.30, -0.50, 2.6),  # -4.0%
]

# A sharp reversal on the same symbol: every directional signal now points the
# other way, including the opening gap. Expectation: a NEW event, not a
# continuation - the story genuinely changed.
SCENARIO_7B_REVERSAL = _infosys_step(
    90, 1_545.00, 2.7, -0.30, -0.40, 2.6, gap_percent=0.90
)  # +3.0%

# The same rally, but still carrying the morning's gap DOWN. Directional
# signals disagree, so the direction is MIXED rather than reversed. Mixed is
# not treated as opposition - a partially recovering move is still the same
# story, and splitting it would double-report one event.
SCENARIO_7C_MIXED_RECOVERY = _infosys_step(
    90, 1_545.00, 2.7, -0.30, -0.40, 2.6, gap_percent=-0.90
)


ALL_SINGLE_OBSERVATIONS = [
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_2_MARKET_WIDE_SELLOFF,
    SCENARIO_3_QUIET_PRICE_LOUD_CONTEXT,
    SCENARIO_4_UNCORROBORATED_MOVE,
    SCENARIO_5_NO_VOLATILITY_HISTORY,
    SCENARIO_6_FALLBACK_VOLUME_BASELINE,
]

SCENARIO_DESCRIPTIONS: dict[str, str] = {
    "RELIANCE": "Fully corroborated decline: large move, stable index, heavy volume, relevant news.",
    "HDFCBANK": "Market-wide selloff: a 5% fall that is not a stock-specific event.",
    "TATAMOTORS": "Small price move with loud context: 4x volume and major news.",
    "WIPRO": "Large but uncorroborated move: normal volume, in line with the market.",
    "ADANIPORTS": "Missing volatility history: no volatility signal is fabricated.",
    "SBIN": "Insufficient same-time-of-day volume history: fallback baseline declared.",
    "INFY": "One decline observed three times: a single evolving event.",
}
