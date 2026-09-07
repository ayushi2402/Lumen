"""Notifications.

Only High Attention and Critical events generate a notification. That
threshold is the point: notifying on every meaningful event would train users
to ignore them, which costs more than the missed alert.

Notifications are persisted first and delivered second. The inbox is the
source of truth, so a denied browser permission degrades to an unread badge
rather than a lost alert - the backend never assumes the browser accepted
anything.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import MarketEventRow, Notification, User, utcnow
from app.services import behavior


def should_notify(
    row: MarketEventRow, settings: Settings | None = None
) -> bool:
    """Whether an event clears the notification bar."""
    settings = settings or get_settings()
    return row.current_score >= settings.notify_min_score


def create_for_event(
    db: Session,
    user: User,
    row: MarketEventRow,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Notification | None:
    """Create a notification if warranted. Idempotent per (user, event).

    Muted stocks are skipped: a mute means "stop alerting me about this", and
    honouring it only in the UI would defeat the purpose.
    """
    settings = settings or get_settings()
    if not should_notify(row, settings):
        return None

    if row.instrument.symbol in behavior.muted_symbols(db, user, now):
        return None

    existing = db.scalar(
        select(Notification).where(
            Notification.user_id == user.id, Notification.event_id == row.id
        )
    )
    if existing is not None:
        return existing

    notification = Notification(
        user_id=user.id,
        event_id=row.id,
        title=row.headline,
        body=row.deterministic_explanation or row.headline,
        severity=row.severity,
        created_at=now or utcnow(),
    )
    db.add(notification)
    db.flush()
    return notification


def list_notifications(
    db: Session, user: User, limit: int = 50, unread_only: bool = False
) -> list[Notification]:
    statement = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    return list(db.scalars(statement).all())


def unread_count(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        )
        or 0
    )


def mark_read(db: Session, user: User, notification_id: int) -> Notification | None:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if notification is None:
        return None
    notification.read_at = notification.read_at or utcnow()
    db.flush()
    return notification


def mark_all_read(db: Session, user: User) -> int:
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.read_at = now
    db.flush()
    return len(rows)


def mark_delivered(db: Session, user: User, notification_id: int) -> Notification | None:
    """Record that the browser actually displayed a notification.

    Separate from ``read_at`` so an undelivered alert (permission denied,
    tab closed) stays visible in the inbox instead of being assumed seen.
    """
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if notification is None:
        return None
    notification.delivered_at = notification.delivered_at or utcnow()
    db.flush()
    return notification


def pending_delivery(db: Session, user: User, limit: int = 10) -> list[Notification]:
    """Notifications created but never shown by a browser.

    The frontend polls this after the permission prompt resolves, so an alert
    raised while permission was pending is still delivered rather than lost.
    """
    return list(
        db.scalars(
            select(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.delivered_at.is_(None),
                Notification.read_at.is_(None),
            )
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).all()
    )
