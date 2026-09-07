"""Background market polling.

A single asyncio task inside the FastAPI process. No separate worker, no Redis
and no scheduler library - all three were out of scope, and a free-tier
instance cannot host them anyway.

Three properties matter more than the polling itself:

* **Calendar-aware.** It polls on the 45-second cadence only while the market
  is open, and drops to a long idle tick otherwise. Hammering a scraped NSE
  endpoint all night is how an unofficial provider gets blocked.
* **Clean shutdown.** The task is cancelled and awaited during lifespan
  teardown, so a redeploy does not leave a half-finished cycle holding a
  database session.
* **Not depended upon.** Render's free tier sleeps, so this loop is assumed to
  be *absent* most of the time. Every API request refreshes what it needs on
  its own, and the loop is an optimisation that keeps state warm - never a
  prerequisite for correctness.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.models import Instrument, Watchlist, WatchlistItem, utcnow
from app.db.session import get_session_factory
from app.market.calendar import MarketStatus, market_status
from app.providers.registry import DataMode, select_provider
from app.services import baselines as baselines_service
from app.services import events as events_service
from app.services import retention
from app.services.pipeline import run_cycle

logger = logging.getLogger("lumen.polling")


def watched_universe(db) -> list[str]:
    """Every distinct symbol any user watches.

    The union across all users, not per user: polling and scoring are
    O(instruments), so a symbol on fifty watchlists is fetched once.
    """
    rows = db.scalars(
        select(Instrument.symbol)
        .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .distinct()
    ).all()
    return sorted(rows)


def poll_once(settings: Settings | None = None, now: datetime | None = None) -> dict:
    """Run one polling cycle. Safe to call directly; used by tests.

    Scores the shared universe with **no personalization** - behavioural
    relevance is per-user and is applied when a user actually requests their
    dashboard. What is stored here is the objective picture.
    """
    settings = settings or get_settings()
    now = now or utcnow()

    factory = get_session_factory()
    if factory is None:
        return {"status": "skipped", "reason": "no database configured"}

    db = factory()
    try:
        symbols = watched_universe(db)
        if not symbols:
            return {"status": "idle", "reason": "no watched instruments"}

        baselines = baselines_service.load_baselines(db, symbols)
        if not baselines:
            return {"status": "idle", "reason": "no baselines available"}

        selection = select_provider(DataMode.LIVE, settings=settings)
        if selection.is_replay:
            # Never write synthetic prices into the live scope - a demo must
            # not contaminate the record of what really happened.
            return {"status": "skipped", "reason": "no live provider; not writing replay data to live scope"}

        cycle = run_cycle(selection.provider, baselines, include_intraday=False)
        instruments = {
            row.symbol: row
            for row in db.scalars(
                select(Instrument).where(Instrument.symbol.in_(list(baselines)))
            ).all()
        }

        events = 0
        for scored in cycle.scored:
            instrument = instruments.get(scored.symbol)
            if instrument is None:
                continue
            row, _ = events_service.ingest_scored(
                db, scored, instrument, scope="live", is_replay=False
            )
            if row is not None:
                events += 1

        events_service.expire_stale(db, scope="live", now=cycle.observed_at)
        db.commit()

        return {
            "status": "ok",
            "provider": cycle.provider,
            "symbols": len(cycle.scored),
            "events_touched": events,
            "observed_at": cycle.observed_at.isoformat(),
            "errors": cycle.errors[:3],
        }
    except Exception as exc:
        db.rollback()
        logger.exception("Polling cycle failed")
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()


def _interval_for(status: MarketStatus, settings: Settings) -> int:
    """Poll fast during a session, slowly otherwise."""
    if status is MarketStatus.OPEN:
        return max(15, settings.polling_interval_seconds)
    return max(60, settings.polling_idle_interval_seconds)


class MarketPoller:
    """Owns the background task's lifecycle."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.last_result: dict | None = None
        self.last_run_at: datetime | None = None
        self.cycles = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        logger.info("Market poller started.")
        cycles_since_purge = 0
        try:
            while not self._stopping.is_set():
                status = market_status(utcnow())
                if status is MarketStatus.OPEN:
                    # Blocking provider I/O must not stall the event loop.
                    result = await asyncio.to_thread(poll_once, self.settings)
                    self.last_result = result
                    self.last_run_at = utcnow()
                    self.cycles += 1

                    cycles_since_purge += 1
                    if cycles_since_purge >= 200:
                        cycles_since_purge = 0
                        await asyncio.to_thread(self._purge)
                else:
                    self.last_result = {"status": "idle", "reason": status.value}

                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=_interval_for(status, self.settings)
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.info("Market poller cancelled.")
            raise
        finally:
            logger.info("Market poller stopped after %s cycles.", self.cycles)

    def _purge(self) -> None:
        factory = get_session_factory()
        if factory is None:
            return
        db = factory()
        try:
            removed = retention.purge_expired_observations(db, self.settings)
            db.commit()
            if removed:
                logger.info("Purged %s expired observations.", removed)
        except Exception:
            db.rollback()
            logger.exception("Retention purge failed")
        finally:
            db.close()

    def start(self) -> None:
        if not self.settings.enable_background_polling:
            logger.info("Background polling disabled by configuration.")
            return
        if get_session_factory() is None:
            logger.info("No database configured; background polling not started.")
            return
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="lumen-market-poller")

    async def stop(self) -> None:
        """Signal, cancel and await the task so shutdown is actually clean."""
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def status(self) -> dict:
        return {
            "running": self.running,
            "enabled": self.settings.enable_background_polling,
            "cycles": self.cycles,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_result": self.last_result,
        }


POLLER = MarketPoller()
