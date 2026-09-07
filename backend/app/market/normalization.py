"""Raw provider payloads to engine inputs.

This is the only place where a vendor's response shape is understood. Providers
stay thin and return raw payloads; the engine only ever sees a validated
``MarketObservation``. Adding a second provider means adding a parser here, not
touching signals, scoring or events.

The module is careful about one thing above all: **it never invents an input.**
If a baseline is too thin, it passes the real sample count through and lets the
engine decide the signal is unavailable. Nothing here substitutes a zero or a
plausible-looking default for missing history.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime

from app.intelligence.models import MarketObservation, NewsEvidence
from app.providers.base import RawResponse


@dataclass(frozen=True)
class QuoteSnapshot:
    """One instrument's quote, normalized out of a provider payload."""

    symbol: str
    last_price: float
    previous_close: float
    open_price: float | None
    high: float | None
    low: float | None
    volume: float | None
    as_of: datetime
    provider: str
    is_replay: bool

    @property
    def daily_return(self) -> float | None:
        if self.previous_close <= 0:
            return None
        return (self.last_price - self.previous_close) / self.previous_close * 100.0

    @property
    def gap_percent(self) -> float | None:
        """Opening gap vs the previous close, or ``None`` if no open is known."""
        if self.open_price is None or self.previous_close <= 0:
            return None
        return (self.open_price - self.previous_close) / self.previous_close * 100.0


@dataclass(frozen=True)
class ObservationContext:
    """Everything beyond the quote that the engine needs.

    Sample counts travel alongside every baseline on purpose - they are how the
    engine distinguishes "measured as normal" from "not enough history to say".
    """

    typical_daily_move: float | None = None
    typical_move_sample_days: int = 0
    historical_volatility: float | None = None
    volatility_sample_days: int = 0
    average_volume: float | None = None
    same_time_of_day_volume: float | None = None
    same_time_of_day_sample_days: int = 0
    intraday_volatility: float | None = None
    intraday_observation_count: int = 0
    historical_gap_stdev: float | None = None
    benchmark_return: float | None = None
    sector_return: float | None = None
    news: NewsEvidence | None = None


class NormalizationError(ValueError):
    """A payload could not be interpreted. Never silently swallowed."""


# ---------------------------------------------------------------------------
# Upstox-shaped quote payloads
# ---------------------------------------------------------------------------


def parse_quotes(raw: RawResponse) -> dict[str, QuoteSnapshot]:
    """Parse any supported provider's quote batch into snapshots by symbol.

    Vendor shapes are understood here and nowhere else. Two families are
    supported: Upstox/replay's ``{"data": {"NSE_EQ:SYM": {...}}}`` envelope,
    and the flat ``{"quotes": {"SYM": {...}}}`` form used by the yfinance and
    NSE providers.

    Entries that are malformed or missing a usable previous close are skipped
    rather than defaulted - a quote LUMEN cannot trust must not become a
    scored observation.
    """
    payload = raw.payload
    if not isinstance(payload, dict):
        raise NormalizationError("Quote payload is not an object.")

    if isinstance(payload.get("quotes"), dict):
        return _parse_flat_quotes(raw, payload["quotes"])

    data = payload.get("data")
    if not isinstance(data, dict):
        raise NormalizationError("Quote payload has no 'data' or 'quotes' object.")

    snapshots: dict[str, QuoteSnapshot] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        symbol = _symbol_from_key(key, entry)
        ohlc = entry.get("ohlc") or {}
        previous_close = _as_float(ohlc.get("close"))
        last_price = _as_float(entry.get("last_price"))
        if not previous_close or not last_price or previous_close <= 0 or last_price <= 0:
            continue

        snapshots[symbol] = QuoteSnapshot(
            symbol=symbol,
            last_price=last_price,
            previous_close=previous_close,
            open_price=_as_float(ohlc.get("open")),
            high=_as_float(ohlc.get("high")),
            low=_as_float(ohlc.get("low")),
            volume=_as_float(entry.get("volume")),
            as_of=_parse_time(entry.get("last_trade_time")) or raw.as_of,
            provider=raw.provider,
            is_replay=raw.is_replay,
        )
    return snapshots


def _parse_flat_quotes(raw: RawResponse, quotes: dict) -> dict[str, QuoteSnapshot]:
    """Parse the flat ``{"SYM": {...}}`` form used by yfinance and NSE.

    Each entry's own ``as_of`` is preferred over the batch timestamp, so a
    stale bar for one instrument is not disguised by a fresh batch.
    """
    snapshots: dict[str, QuoteSnapshot] = {}
    for symbol, entry in quotes.items():
        if not isinstance(entry, dict):
            continue
        last_price = _as_float(entry.get("last_price"))
        previous_close = _as_float(entry.get("previous_close"))
        if not last_price or not previous_close or last_price <= 0 or previous_close <= 0:
            continue

        snapshots[symbol.upper()] = QuoteSnapshot(
            symbol=symbol.upper(),
            last_price=last_price,
            previous_close=previous_close,
            open_price=_as_float(entry.get("open")),
            high=_as_float(entry.get("high")),
            low=_as_float(entry.get("low")),
            volume=_as_float(entry.get("volume")),
            as_of=_parse_time(entry.get("as_of")) or raw.as_of,
            provider=raw.provider,
            is_replay=raw.is_replay,
        )
    return snapshots


def _symbol_from_key(key: str, entry: dict) -> str:
    """Extract a plain symbol from 'NSE_EQ:RELIANCE' or an instrument token."""
    token = entry.get("instrument_token")
    if isinstance(token, str) and "|" in token:
        return token.split("|")[-1]
    return key.split(":")[-1].split("|")[-1]


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------


def parse_candles(raw: RawResponse) -> list[dict]:
    """Parse an Upstox candle array into dicts.

    Upstox returns positional arrays: [timestamp, open, high, low, close,
    volume, open_interest].
    """
    payload = raw.payload
    if not isinstance(payload, dict):
        raise NormalizationError("Candle payload is not an object.")

    # yfinance and the NSE provider return candles as dict rows at the top
    # level; Upstox and replay nest positional arrays under "data".
    if isinstance(payload.get("candles"), list):
        return _parse_dict_candles(payload["candles"])

    candles = (payload.get("data") or {}).get("candles")
    if not isinstance(candles, list):
        raise NormalizationError("Candle payload has no 'candles' array.")

    parsed: list[dict] = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        close = _as_float(row[4])
        if close is None:
            continue
        parsed.append(
            {
                "timestamp": _parse_time(row[0]),
                "open": _as_float(row[1]),
                "high": _as_float(row[2]),
                "low": _as_float(row[3]),
                "close": close,
                "volume": _as_float(row[5]),
            }
        )
    return parsed


def _parse_dict_candles(rows: list) -> list[dict]:
    """Parse already-keyed candle rows, normalizing timestamps to datetimes."""
    parsed: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _as_float(row.get("close"))
        if close is None:
            continue
        parsed.append(
            {
                "timestamp": _parse_time(row.get("timestamp")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "close": close,
                "volume": _as_float(row.get("volume")),
            }
        )
    return parsed


def _median_interval_minutes(candles: list[dict]) -> float | None:
    """Median spacing between candles, inferred from their timestamps."""
    stamps = [c["timestamp"] for c in candles if c.get("timestamp")]
    if len(stamps) < 2:
        return None
    stamps.sort()
    gaps = [
        (stamps[i] - stamps[i - 1]).total_seconds() / 60.0 for i in range(1, len(stamps))
    ]
    positive = [g for g in gaps if g > 0]
    return statistics.median(positive) if positive else None


def realized_volatility(
    candles: list[dict], session_minutes: int = 375
) -> tuple[float | None, int]:
    """Realized intraday volatility, scaled to a *daily-equivalent* percent.

    The scaling is the important part. Per-candle return dispersion is not
    comparable to daily historical volatility - 15-minute returns are far
    smaller than daily ones - so comparing them raw would make intraday
    volatility look permanently subdued and the volatility signal would never
    fire. Returns are therefore scaled by ``sqrt(periods per session)``, the
    standard square-root-of-time convention, putting both sides of the ratio
    in the same units.

    The candle interval is inferred from the timestamps rather than assumed,
    so 1-minute and 15-minute series both scale correctly.

    Returns ``(None, n)`` when there is too little data. The caller passes the
    count through and the engine reports insufficient history rather than a
    fabricated zero.
    """
    closes = [c["close"] for c in candles if c.get("close")]
    if len(closes) < 3:
        return None, len(closes)

    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if len(returns) < 2:
        return None, len(closes)

    per_period = statistics.pstdev(returns)
    interval = _median_interval_minutes(candles)
    if interval and interval > 0:
        periods_per_session = max(1.0, session_minutes / interval)
        per_period *= math.sqrt(periods_per_session)

    return round(per_period, 4), len(closes)


def daily_baselines(candles: list[dict]) -> dict:
    """Derive engine baselines from daily candles.

    This is how real Upstox historical data becomes the same baseline shape
    that ``replay.universe`` produces synthetically.
    """
    closes = [c["close"] for c in candles if c.get("close")]
    volumes = [c["volume"] for c in candles if c.get("volume")]
    opens = [(c.get("open"), c.get("close")) for c in candles]

    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    gaps = [
        (o - prev_close) / prev_close * 100.0
        for (o, _), (_, prev_close) in zip(opens[1:], opens[:-1])
        if o and prev_close
    ]

    return {
        "typical_daily_move": round(
            statistics.fmean(abs(r) for r in returns), 4
        ) if returns else None,
        "typical_move_sample_days": len(returns),
        "historical_volatility": round(statistics.pstdev(returns), 4)
        if len(returns) >= 2
        else None,
        "volatility_sample_days": len(returns),
        "average_volume": round(statistics.fmean(volumes), 2) if volumes else None,
        "historical_gap_stdev": round(statistics.pstdev(gaps), 4)
        if len(gaps) >= 2
        else None,
        "previous_close": closes[-1] if closes else None,
    }


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def to_observation(
    quote: QuoteSnapshot, context: ObservationContext
) -> MarketObservation:
    """Build the engine's input from a quote plus its context.

    Every field is either measured or explicitly ``None``. No defaults are
    invented here; that decision belongs to the engine, which has explicit
    unavailable states for it.
    """
    return MarketObservation(
        symbol=quote.symbol,
        timestamp=quote.as_of,
        price=quote.last_price,
        previous_price=quote.previous_close,
        volume=quote.volume,
        average_volume=context.average_volume,
        same_time_of_day_volume=context.same_time_of_day_volume,
        same_time_of_day_sample_days=context.same_time_of_day_sample_days,
        historical_volatility=context.historical_volatility,
        intraday_volatility=context.intraday_volatility,
        volatility_sample_days=context.volatility_sample_days,
        intraday_observation_count=context.intraday_observation_count,
        typical_daily_move=context.typical_daily_move,
        typical_move_sample_days=context.typical_move_sample_days,
        gap_percent=quote.gap_percent,
        historical_gap_stdev=context.historical_gap_stdev,
        benchmark_return=context.benchmark_return,
        sector_return=context.sector_return,
        news=context.news,
    )


def news_evidence_from(payload: dict | None) -> NewsEvidence | None:
    """Convert a stored/scenario news record into engine evidence.

    ``causal_link_established`` defaults to False and is only ever True when
    the source record explicitly says so.
    """
    if not payload:
        return None
    return NewsEvidence(
        present=True,
        relevance=float(payload.get("relevance", 0.0)),
        minutes_before_move=payload.get("minutes_before_move"),
        confidence=float(payload.get("confidence", 0.0)),
        headline=payload.get("headline"),
        causal_link_established=bool(payload.get("causal_link_established", False)),
    )
