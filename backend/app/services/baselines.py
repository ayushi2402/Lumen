"""Historical baselines: the numbers that make "abnormal" measurable.

Volume can only be called abnormal against a normal; a move can only be called
unusual against that stock's own usual. Those baselines are computed once from
historical candles, stored, and read from the database - never fetched inside a
request.

Two sources, one shape:

* ``InstrumentBaseline`` from ``replay.universe`` (synthetic, deterministic).
* ``StoredBaseline`` here, built from real historical candles.

Both expose the same attributes and the same ``same_time_volume()`` method, so
the pipeline and the engine cannot tell them apart. That is what makes swapping
demo data for real data a configuration change rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Instrument, InstrumentDailyStats, IntradayVolumeBaseline
from app.market.calendar import SESSION_MINUTES
from app.market.normalization import daily_baselines
from app.replay.universe import UNIVERSE, InstrumentBaseline

# Buckets are 15 minutes wide, matching the intraday candle interval LUMEN uses.
BUCKET_MINUTES = 15
MIN_HISTORY_DAYS = 30
DEFAULT_HISTORY_DAYS = 120


@dataclass(frozen=True)
class StoredBaseline:
    """Baselines loaded from the database. Duck-types ``InstrumentBaseline``."""

    symbol: str
    name: str
    sector: str | None
    previous_close: float
    typical_daily_move: float | None
    typical_move_sample_days: int
    historical_volatility: float | None
    volatility_sample_days: int
    average_volume: float | None
    historical_gap_stdev: float | None
    provider_key: str
    # minute-of-session -> (average cumulative volume, sample days)
    intraday_buckets: dict[int, tuple[float, int]]

    def same_time_volume(self, minute_of_session: int, session_minutes: int) -> float | None:
        """Expected cumulative volume by this point of the session.

        Prefers a real measured bucket. Falls back to the same U-shaped
        intraday profile the synthetic universe uses, which is an
        approximation - and is why the engine is told the sample count
        separately, so it can declare a fallback baseline rather than
        pretending this is same-time-of-day data.
        """
        if self.intraday_buckets:
            bucket = self.nearest_bucket(minute_of_session)
            if bucket is not None:
                return bucket[0]
        if self.average_volume is None:
            return None
        fraction = min(1.0, max(0.0, minute_of_session / max(1, session_minutes)))
        shape = 0.5 * (fraction ** 0.65) + 0.5 * (fraction ** 1.8)
        return max(1.0, self.average_volume * shape)

    def nearest_bucket(self, minute_of_session: int) -> tuple[float, int] | None:
        if not self.intraday_buckets:
            return None
        key = min(self.intraday_buckets, key=lambda b: abs(b - minute_of_session))
        return self.intraday_buckets[key]

    def same_time_sample_days(self, minute_of_session: int) -> int:
        """Sessions backing this bucket. Zero means "no real baseline"."""
        bucket = self.nearest_bucket(minute_of_session)
        return bucket[1] if bucket else 0


def compute_from_candles(candles: list[dict]) -> dict:
    """Derive engine baselines from daily candles.

    Thin wrapper over ``normalization.daily_baselines`` so callers have one
    obvious entry point and the statistics live in one place.
    """
    return daily_baselines(candles)


def intraday_buckets_from_candles(
    candles: list[dict], bucket_minutes: int = BUCKET_MINUTES
) -> dict[int, float]:
    """Cumulative volume by minute-of-session from intraday candles.

    Volume is accumulated within each session, so a bucket answers "how much
    has normally traded by this time of day" rather than "how much trades in
    this fifteen minutes".
    """
    from app.market.calendar import minute_of_session

    per_session: dict[date, dict[int, float]] = {}
    for candle in candles:
        stamp = candle.get("timestamp")
        volume = candle.get("volume")
        if not isinstance(stamp, datetime) or volume is None:
            continue
        minute = minute_of_session(stamp)
        if minute is None:
            continue
        bucket = (minute // bucket_minutes) * bucket_minutes
        session = per_session.setdefault(stamp.date(), {})
        session[bucket] = session.get(bucket, 0.0) + float(volume)

    cumulative: dict[int, list[float]] = {}
    for buckets in per_session.values():
        running = 0.0
        for bucket in sorted(buckets):
            running += buckets[bucket]
            cumulative.setdefault(bucket, []).append(running)

    return {
        bucket: sum(values) / len(values)
        for bucket, values in cumulative.items()
        if values
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def upsert_daily_stats(
    db: Session, instrument: Instrument, stats: dict, as_of: date | None = None
) -> InstrumentDailyStats:
    """Write one instrument's daily baselines. Idempotent per (instrument, date)."""
    as_of = as_of or date.today()
    row = db.scalar(
        select(InstrumentDailyStats).where(
            InstrumentDailyStats.instrument_id == instrument.id,
            InstrumentDailyStats.as_of_date == as_of,
        )
    )
    if row is None:
        row = InstrumentDailyStats(instrument_id=instrument.id, as_of_date=as_of)
        db.add(row)

    row.typical_daily_move = stats.get("typical_daily_move")
    row.typical_move_sample_days = int(stats.get("typical_move_sample_days") or 0)
    row.historical_volatility = stats.get("historical_volatility")
    row.volatility_sample_days = int(stats.get("volatility_sample_days") or 0)
    row.average_volume = stats.get("average_volume")
    row.historical_gap_stdev = stats.get("historical_gap_stdev")
    row.previous_close = stats.get("previous_close")
    db.flush()
    return row


def upsert_intraday_baselines(
    db: Session, instrument: Instrument, buckets: dict[int, float], sample_days: int
) -> int:
    """Write same-time-of-day volume buckets. Idempotent per (instrument, bucket)."""
    existing = {
        row.minute_of_session: row
        for row in db.scalars(
            select(IntradayVolumeBaseline).where(
                IntradayVolumeBaseline.instrument_id == instrument.id
            )
        ).all()
    }
    for minute, average in buckets.items():
        row = existing.get(minute)
        if row is None:
            row = IntradayVolumeBaseline(
                instrument_id=instrument.id, minute_of_session=minute,
                average_volume=average, sample_days=sample_days,
            )
            db.add(row)
        else:
            row.average_volume = average
            row.sample_days = sample_days
    db.flush()
    return len(buckets)


def has_fresh_stats(
    db: Session, instrument: Instrument, max_age_days: int = 5
) -> bool:
    """Whether recent baselines already exist.

    Used to keep seeding idempotent and cheap: re-running the seed must not
    re-download history that is still current.
    """
    cutoff = date.today() - timedelta(days=max_age_days)
    return (
        db.scalar(
            select(InstrumentDailyStats.id).where(
                InstrumentDailyStats.instrument_id == instrument.id,
                InstrumentDailyStats.as_of_date >= cutoff,
            )
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_baselines(db: Session, symbols: list[str]) -> dict[str, object]:
    """Baselines for the requested symbols, preferring stored real data.

    Any symbol without usable stored history falls back to the synthetic
    universe, so the product keeps working before a seed has ever run. The
    fallback is honest: sample counts travel with every baseline, and the
    engine declines to use one that is too thin.
    """
    if not symbols:
        return {}

    upper = [s.upper() for s in symbols]
    instruments = {
        row.symbol: row
        for row in db.scalars(
            select(Instrument).where(Instrument.symbol.in_(upper))
        ).all()
    }
    if not instruments:
        return {s: UNIVERSE[s] for s in upper if s in UNIVERSE}

    ids = [i.id for i in instruments.values()]
    stats = {}
    for row in db.scalars(
        select(InstrumentDailyStats)
        .where(InstrumentDailyStats.instrument_id.in_(ids))
        .order_by(InstrumentDailyStats.as_of_date.desc())
    ).all():
        stats.setdefault(row.instrument_id, row)

    buckets: dict[int, dict[int, tuple[float, int]]] = {}
    for row in db.scalars(
        select(IntradayVolumeBaseline).where(
            IntradayVolumeBaseline.instrument_id.in_(ids)
        )
    ).all():
        buckets.setdefault(row.instrument_id, {})[row.minute_of_session] = (
            row.average_volume,
            row.sample_days,
        )

    result: dict[str, object] = {}
    for symbol in upper:
        instrument = instruments.get(symbol)
        stat = stats.get(instrument.id) if instrument else None

        if instrument is None or stat is None or not stat.previous_close:
            if symbol in UNIVERSE:
                result[symbol] = UNIVERSE[symbol]
            continue

        result[symbol] = StoredBaseline(
            symbol=symbol,
            name=instrument.name,
            sector=instrument.sector,
            previous_close=stat.previous_close,
            typical_daily_move=stat.typical_daily_move,
            typical_move_sample_days=stat.typical_move_sample_days,
            historical_volatility=stat.historical_volatility,
            volatility_sample_days=stat.volatility_sample_days,
            average_volume=stat.average_volume,
            historical_gap_stdev=stat.historical_gap_stdev,
            provider_key=instrument.provider_key or f"NSE_EQ|{symbol}",
            intraday_buckets=buckets.get(instrument.id, {}),
        )

    return result


def baseline_source(baseline: object) -> str:
    """Whether a baseline came from real history or the synthetic universe."""
    if isinstance(baseline, StoredBaseline):
        return "historical"
    if isinstance(baseline, InstrumentBaseline):
        return "synthetic"
    return "unknown"


def same_time_sample_days(baseline: object, minute_of_session: int) -> int:
    """Sessions backing a same-time-of-day volume baseline.

    Reported honestly: a stored baseline with no measured bucket returns 0, so
    the engine falls back to the full-day average and says so, rather than
    treating an approximation as a same-time-of-day measurement.
    """
    if isinstance(baseline, StoredBaseline):
        return baseline.same_time_sample_days(minute_of_session)
    # The synthetic universe models a full intraday profile by construction.
    return 20


__all__ = [
    "BUCKET_MINUTES",
    "DEFAULT_HISTORY_DAYS",
    "MIN_HISTORY_DAYS",
    "SESSION_MINUTES",
    "StoredBaseline",
    "baseline_source",
    "compute_from_candles",
    "has_fresh_stats",
    "intraday_buckets_from_candles",
    "load_baselines",
    "same_time_sample_days",
    "upsert_daily_stats",
    "upsert_intraday_baselines",
]
