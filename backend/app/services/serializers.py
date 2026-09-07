"""Shared serialization from database rows to API payloads.

Kept in one module so an event looks the same on the dashboard, in a
watchlist, on a stock page and in a replay response. Nothing here exposes a
raw provider payload.
"""

from __future__ import annotations

from typing import Any

from app.db.models import MarketEventRow, UserEventState
from app.market.context import MoveClassification, classification_note

_FAMILY_LABEL = {
    "price_movement": "price movement",
    "relative_performance": "performance versus the market",
    "volume": "unusual volume",
    "volatility": "elevated volatility",
    "gap": "an opening gap",
    "news": "related news",
}


def primary_reason(row: MarketEventRow) -> str:
    """The single strongest reason this event exists.

    Uses the top-contributing family from the deterministic breakdown, so the
    headline reason and the score always agree with each other.
    """
    breakdown = row.breakdown or {}
    contributors = [(f, p) for f, p in breakdown.items() if p > 0]
    if not contributors:
        return row.headline

    family, _ = max(contributors, key=lambda item: item[1])
    evidence = row.evidence or []
    lead = evidence[0] if evidence else row.headline
    return f"{lead} Driven mainly by {_FAMILY_LABEL.get(family, family)}."


def why_it_matters(row: MarketEventRow) -> str:
    """One sentence on significance, never advice."""
    parts: list[str] = []
    if row.is_market_wide:
        parts.append(
            "This largely tracked the broader market rather than being specific "
            "to the stock."
        )
    else:
        parts.append("This move was specific to the stock rather than the market.")

    if row.confidence != "high":
        parts.append(
            f"Confidence is {row.confidence} because some supporting evidence "
            "was unavailable."
        )
    return " ".join(parts)


def classification_of(row: MarketEventRow) -> str:
    if not row.is_market_wide:
        return MoveClassification.STOCK_SPECIFIC
    return MoveClassification.MARKET_WIDE


def event_summary(
    row: MarketEventRow, state: UserEventState | None = None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "symbol": row.instrument.symbol,
        "name": row.instrument.name,
        "headline": row.headline,
        "severity": row.severity,
        "direction": row.direction,
        "score": round(row.current_score, 2),
        "peak_score": round(row.peak_score, 2),
        "confidence": row.confidence,
        "status": row.status,
        "classification": classification_of(row),
        "is_market_wide": row.is_market_wide,
        "primary_reason": primary_reason(row),
        "started_at": row.started_at,
        "last_updated_at": row.last_updated_at,
        "session_date": row.session_date.isoformat(),
        "observation_count": row.observation_count,
        "read": bool(state and state.read_at),
        "reviewed": bool(state and state.reviewed_at),
        "dismissed": bool(state and state.dismissed_at),
    }


def timeline_points(row: MarketEventRow) -> list[dict[str, Any]]:
    return [
        {
            "observed_at": point.observed_at,
            "score": round(point.score, 2),
            "severity": point.severity,
            "direction": point.direction,
            "price": point.price,
        }
        for point in row.timeline
    ]


def price_payload(quote) -> dict[str, Any]:
    """Price block from a normalized quote snapshot."""
    change = quote.last_price - quote.previous_close
    return {
        "price": round(quote.last_price, 2),
        "previous_close": round(quote.previous_close, 2),
        "change": round(change, 2),
        "change_percent": round(quote.daily_return or 0.0, 2),
        "volume": quote.volume,
    }


def market_context_note(row: MarketEventRow) -> str:
    return classification_note(classification_of(row))
