"""Data retention.

Market observations are kept for 90 days. That window is chosen to match what
the product actually needs: "what changed since you were away" reaches back
over holidays and long absences, and the day-by-day drilldown needs the
sessions in between. Beyond that the rows are storage cost, not product value.

Events and user behaviour are *not* purged on this schedule - dismissed events
must remain available as historical context, which is the entire reason
dismissal does not delete them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import MarketObservationRow, utcnow


def cutoff_for(settings: Settings | None = None, now: datetime | None = None) -> datetime:
    settings = settings or get_settings()
    return (now or utcnow()) - timedelta(days=settings.observation_retention_days)


def purge_expired_observations(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> int:
    """Delete observations older than the retention window. Returns the count."""
    cutoff = cutoff_for(settings, now)
    count = int(
        db.scalar(
            select(func.count())
            .select_from(MarketObservationRow)
            .where(MarketObservationRow.observed_at < cutoff)
        )
        or 0
    )
    if count:
        db.execute(
            delete(MarketObservationRow).where(MarketObservationRow.observed_at < cutoff)
        )
        db.flush()
    return count


def purge_replay_scope(db: Session, scope: str) -> int:
    """Drop one replay session's observations.

    Replay data is disposable by definition - it is synthetic, and keeping it
    beyond the demo would only risk it being mistaken for real history.
    """
    count = int(
        db.scalar(
            select(func.count())
            .select_from(MarketObservationRow)
            .where(MarketObservationRow.scope == scope)
        )
        or 0
    )
    if count:
        db.execute(delete(MarketObservationRow).where(MarketObservationRow.scope == scope))
        db.flush()
    return count


def retention_report(db: Session, settings: Settings | None = None) -> dict:
    """Current retention state, for operational visibility."""
    settings = settings or get_settings()
    total = int(
        db.scalar(select(func.count()).select_from(MarketObservationRow)) or 0
    )
    expired = int(
        db.scalar(
            select(func.count())
            .select_from(MarketObservationRow)
            .where(MarketObservationRow.observed_at < cutoff_for(settings))
        )
        or 0
    )
    return {
        "retention_days": settings.observation_retention_days,
        "observations": total,
        "expired_pending_purge": expired,
    }
