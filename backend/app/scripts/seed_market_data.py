"""Seed historical baselines for the supported universe.

    python -m app.scripts.seed_market_data
    python -m app.scripts.seed_market_data --symbols RELIANCE,INFY --days 180
    python -m app.scripts.seed_market_data --force

Downloads daily history for each instrument, derives the statistics the
intelligence engine needs, and stores them. Without this the engine falls back
to the synthetic universe - it still runs, but "abnormal" is measured against
invented normals rather than real ones.

**Idempotent.** Instruments with baselines newer than ``--max-age-days`` are
skipped, so re-running after a partial failure only fetches what is missing.
Nothing large is written to the repository; everything lands in PostgreSQL.

Providers are tried in the configured chain order, so this works with NSE data
where reachable and Yahoo data everywhere else.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Instrument, utcnow
from app.db.session import get_session_factory
from app.market.normalization import parse_candles
from app.providers.base import ProviderError
from app.providers.registry import DataMode, select_provider
from app.replay.universe import SYMBOLS
from app.services import baselines as baselines_service
from app.services import catalog

logger = logging.getLogger("lumen.seed")

# Enough sessions for stable volatility, typical-move and gap statistics.
DEFAULT_DAYS = 180
# Courtesy delay between instruments - NSE rate-limits aggressive scraping.
THROTTLE_SECONDS = 0.4


def seed_instrument(
    db: Session,
    instrument: Instrument,
    provider,
    days: int,
    force: bool,
    max_age_days: int,
) -> tuple[bool, str]:
    """Seed one instrument. Returns ``(changed, message)``."""
    if not force and baselines_service.has_fresh_stats(db, instrument, max_age_days):
        return False, "already fresh"

    end = utcnow()
    start = end - timedelta(days=days)
    key = instrument.provider_key or f"NSE_EQ|{instrument.symbol}"

    try:
        raw = provider.get_historical_candles(key, "day", start, end)
        candles = parse_candles(raw)
    except (ProviderError, ValueError) as exc:
        return False, f"no history ({exc})"

    if len(candles) < baselines_service.MIN_HISTORY_DAYS:
        # Too little history is worse than none: it would produce baselines the
        # engine treats as usable when they are not.
        return False, f"insufficient history ({len(candles)} sessions)"

    stats = baselines_service.compute_from_candles(candles)
    if not stats.get("previous_close"):
        return False, "history had no usable closes"

    baselines_service.upsert_daily_stats(db, instrument, stats)

    # Intraday buckets are best-effort: providers that cannot serve intraday
    # history simply leave the same-time-of-day baseline unmeasured, and the
    # engine then declares a fallback rather than inventing one.
    try:
        intraday_raw = provider.get_intraday_candles(key, "15minute")
        buckets = baselines_service.intraday_buckets_from_candles(
            parse_candles(intraday_raw)
        )
        if buckets:
            baselines_service.upsert_intraday_baselines(db, instrument, buckets, 1)
    except (ProviderError, ValueError):
        pass

    return True, f"{len(candles)} sessions"


def run(
    symbols: list[str] | None = None,
    days: int = DEFAULT_DAYS,
    force: bool = False,
    max_age_days: int = 5,
    throttle: float = THROTTLE_SECONDS,
) -> dict:
    """Seed the universe. Returns a summary suitable for logging."""
    settings = get_settings()
    factory = get_session_factory()
    if factory is None:
        raise SystemExit(
            "DATABASE_URL is not set. Point it at your Supabase Postgres "
            "connection string and run the migrations first:\n"
            "  python -m alembic upgrade head"
        )

    selection = select_provider(DataMode.LIVE, settings=settings)
    if selection.is_replay or not selection.chain:
        raise SystemExit(
            "No live market provider is available, so there is no real history "
            "to seed. Check network access and MARKET_PROVIDER_CHAIN."
        )

    logger.info("Seeding via provider chain: %s", " -> ".join(selection.chain))
    session = factory()
    seeded, skipped, failed = 0, 0, []

    try:
        catalog.seed_instruments(session)
        session.commit()

        wanted = [s.upper() for s in (symbols or list(SYMBOLS))]
        instruments = catalog.get_many(session, wanted)

        for index, symbol in enumerate(wanted, start=1):
            instrument = instruments.get(symbol)
            if instrument is None:
                failed.append(f"{symbol}: not in catalogue")
                continue

            try:
                changed, message = seed_instrument(
                    session, instrument, selection.provider, days, force, max_age_days
                )
            except Exception as exc:  # one bad symbol must not abort the run
                session.rollback()
                failed.append(f"{symbol}: {type(exc).__name__}")
                logger.warning("[%s/%s] %s failed: %s", index, len(wanted), symbol, exc)
                continue

            if changed:
                seeded += 1
                session.commit()
            else:
                skipped += 1
            logger.info("[%s/%s] %s: %s", index, len(wanted), symbol, message)

            if throttle and changed:
                time.sleep(throttle)

        session.commit()
    finally:
        session.close()

    summary = {
        "provider_chain": selection.chain,
        "seeded": seeded,
        "skipped": skipped,
        "failed": failed,
        "requested": len(symbols or SYMBOLS),
    }
    logger.info(
        "Done. seeded=%s skipped=%s failed=%s", seeded, skipped, len(failed)
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed LUMEN historical baselines into PostgreSQL."
    )
    parser.add_argument("--symbols", help="Comma-separated symbols (default: all).")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument(
        "--force", action="store_true", help="Re-seed even if baselines are fresh."
    )
    parser.add_argument("--max-age-days", type=int, default=5)
    parser.add_argument("--throttle", type=float, default=THROTTLE_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )
    summary = run(
        symbols=symbols,
        days=args.days,
        force=args.force,
        max_age_days=args.max_age_days,
        throttle=args.throttle,
    )
    return 0 if summary["seeded"] or summary["skipped"] else 1


if __name__ == "__main__":
    sys.exit(main())
