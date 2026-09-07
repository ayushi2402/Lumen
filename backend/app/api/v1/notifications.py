"""Notification routes.

The inbox is authoritative. Browser delivery is recorded separately so an
alert raised while permission was denied is still visible here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.config import Settings, get_settings
from app.db.models import User
from app.services import notifications as service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _payload(n) -> dict:
    return {
        "id": n.id,
        "event_id": n.event_id,
        "title": n.title,
        "body": n.body,
        "severity": n.severity,
        "created_at": n.created_at.isoformat(),
        "read": n.read_at is not None,
        "delivered": n.delivered_at is not None,
    }


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    rows = service.list_notifications(db, user, limit=limit, unread_only=unread_only)
    return {
        "notifications": [_payload(n) for n in rows],
        "unread_count": service.unread_count(db, user),
        "min_score_for_notification": settings.notify_min_score,
        "note": "Only High Attention and Critical events generate notifications.",
    }


@router.get("/pending")
def pending(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Notifications the browser has not yet displayed."""
    return {"pending": [_payload(n) for n in service.pending_delivery(db, user)]}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    row = service.mark_read(db, user, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    db.commit()
    return _payload(row)


@router.post("/{notification_id}/delivered")
def mark_delivered(
    notification_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Confirm the browser actually displayed this notification."""
    row = service.mark_delivered(db, user, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    db.commit()
    return _payload(row)


@router.post("/read-all")
def mark_all_read(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    count = service.mark_all_read(db, user)
    db.commit()
    return {"marked_read": count}
