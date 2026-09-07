"""Market-wide movement detection.

DESIGN DECISION
---------------
The spec asks for market-wide movement to have a "dynamic contextual effect on
significance". That effect is already implemented, deterministically, by the
engine: relative-to-NIFTY and relative-to-sector are two of the six scoring
families, and a stock that falls only as much as its index produces no excess
return and therefore contributes nothing from those families. That is exactly
how the market-wide selloff scenario ends up scored as noise.

So this module deliberately does **not** apply a second adjustment to the
score. Doing so would double-count the same phenomenon and would move the
source of truth out of the engine. What it adds instead is *classification and
context*: breadth across the universe, and a per-event label distinguishing a
stock-specific move from a market-wide or sector-wide one. The UI needs that
distinction; the score does not need a second correction.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.intelligence.models import AttentionResult, Direction, SignalType

# Fraction of the universe that must move together to call it market-wide.
BREADTH_THRESHOLD = 0.7
# Below this median absolute move, breadth is just drift, not a market event.
MIN_MEDIAN_MOVE = 0.75


class MoveClassification:
    STOCK_SPECIFIC = "stock_specific"
    SECTOR_WIDE = "sector_wide"
    MARKET_WIDE = "market_wide"


@dataclass(frozen=True)
class MarketContext:
    """Breadth across the observed universe at one moment."""

    benchmark_return: float | None
    advancing: int
    declining: int
    unchanged: int
    total: int
    breadth_ratio: float
    median_return: float
    dominant_direction: Direction
    is_market_wide_move: bool
    label: str

    def to_dict(self) -> dict:
        return {
            "benchmark_return": self.benchmark_return,
            "advancing": self.advancing,
            "declining": self.declining,
            "unchanged": self.unchanged,
            "total": self.total,
            "breadth_ratio": round(self.breadth_ratio, 4),
            "median_return": round(self.median_return, 4),
            "dominant_direction": self.dominant_direction.value,
            "is_market_wide_move": self.is_market_wide_move,
            "label": self.label,
        }


def compute_market_context(
    returns: dict[str, float], benchmark_return: float | None = None
) -> MarketContext:
    """Summarise how broadly the observed universe is moving together.

    Breadth alone is not enough - almost every session has a majority leaning
    one way. A market-wide move requires both broad participation and a median
    move large enough to matter.
    """
    values = [r for r in returns.values() if r is not None]
    total = len(values)
    if total == 0:
        return MarketContext(
            benchmark_return=benchmark_return,
            advancing=0, declining=0, unchanged=0, total=0,
            breadth_ratio=0.0, median_return=0.0,
            dominant_direction=Direction.NEUTRAL,
            is_market_wide_move=False,
            label="No market data available.",
        )

    advancing = sum(1 for r in values if r > 0.1)
    declining = sum(1 for r in values if r < -0.1)
    unchanged = total - advancing - declining

    ordered = sorted(values)
    median = ordered[total // 2] if total % 2 else (
        (ordered[total // 2 - 1] + ordered[total // 2]) / 2
    )

    if advancing > declining:
        dominant, leaders = Direction.POSITIVE, advancing
    elif declining > advancing:
        dominant, leaders = Direction.NEGATIVE, declining
    else:
        dominant, leaders = Direction.NEUTRAL, 0

    breadth = leaders / total
    is_wide = breadth >= BREADTH_THRESHOLD and abs(median) >= MIN_MEDIAN_MOVE

    return MarketContext(
        benchmark_return=benchmark_return,
        advancing=advancing,
        declining=declining,
        unchanged=unchanged,
        total=total,
        breadth_ratio=breadth,
        median_return=median,
        dominant_direction=dominant,
        is_market_wide_move=is_wide,
        label=_context_label(is_wide, dominant, breadth, median, advancing, declining, total),
    )


def _context_label(
    is_wide: bool, dominant: Direction, breadth: float, median: float,
    advancing: int, declining: int, total: int,
) -> str:
    if is_wide:
        word = "higher" if dominant is Direction.POSITIVE else "lower"
        return (
            f"Broad market move: {int(breadth * 100)}% of tracked stocks are "
            f"{word}, median {median:+.1f}%."
        )
    return f"Mixed market: {advancing} up, {declining} down of {total} tracked."


def classify_move(result: AttentionResult, context: MarketContext | None = None) -> str:
    """Label an event stock-specific, sector-wide or market-wide.

    Derived entirely from signals the engine already produced. A large price
    move whose benchmark-relative component is quiet is the market moving, not
    the stock; one that also matches its sector is a sector story.
    """
    signals = {s.signal_type: s for s in result.signals if s.is_available}

    def strength(kind: SignalType) -> float | None:
        signal = signals.get(kind)
        return None if signal is None else (signal.normalized_score or 0.0)

    benchmark = strength(SignalType.RELATIVE_TO_BENCHMARK)
    sector = strength(SignalType.RELATIVE_TO_SECTOR)
    price = strength(SignalType.PRICE_MOVEMENT) or 0.0

    # Without a benchmark comparison we cannot claim anything broader.
    if benchmark is None:
        return MoveClassification.STOCK_SPECIFIC

    moved = price > 0.15
    tracks_market = benchmark < 0.2

    if moved and tracks_market:
        if context is not None and context.is_market_wide_move:
            return MoveClassification.MARKET_WIDE
        return MoveClassification.MARKET_WIDE

    # Diverged from the index but moved with its sector.
    if moved and sector is not None and sector < 0.2 and benchmark >= 0.2:
        return MoveClassification.SECTOR_WIDE

    return MoveClassification.STOCK_SPECIFIC


def classification_note(classification: str) -> str:
    """One-line explanation of a classification, for the UI."""
    return {
        MoveClassification.MARKET_WIDE: (
            "This move tracked the broader market, so little of it is specific "
            "to this stock."
        ),
        MoveClassification.SECTOR_WIDE: (
            "This move ran with the stock's sector rather than being specific "
            "to the company."
        ),
        MoveClassification.STOCK_SPECIFIC: (
            "This move was specific to the stock rather than the market."
        ),
    }[classification]
