"""User provisioning and profile.

Identity is anchored on Google's ``sub`` rather than email, because an email
address can be changed or reassigned and ``sub`` cannot.

Guest users exist so "Try LUMEN" works with no account at all. They are real
rows with real watchlists and real personalization - the demo is the product,
not a separate mock path - but they carry no Google identity and can be
cleaned up independently.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.google import GoogleIdentity
from app.db.models import User, UserProfile, utcnow
from app.services import watchlists as watchlists_service


def get_or_create_from_google(db: Session, identity: GoogleIdentity) -> tuple[User, bool]:
    """Find or create the user behind a verified Google identity."""
    user = db.scalar(select(User).where(User.google_sub == identity.subject))
    created = False

    if user is None:
        user = User(
            google_sub=identity.subject,
            email=identity.email,
            display_name=identity.name,
            picture_url=identity.picture,
            is_guest=False,
        )
        db.add(user)
        created = True
    else:
        # Refresh mutable profile fields; never overwrite the subject.
        user.email = identity.email or user.email
        user.display_name = identity.name or user.display_name
        user.picture_url = identity.picture or user.picture_url

    db.flush()
    return user, created


def create_guest(db: Session) -> User:
    """A throwaway identity for the demo, with the curated watchlist attached."""
    user = User(
        google_sub=None,
        email=None,
        display_name=f"Guest {secrets.token_hex(3)}",
        is_guest=True,
        onboarded_at=utcnow(),
    )
    db.add(user)
    db.flush()
    watchlists_service.create_demo_watchlist(db, user)
    return user


def get_profile(db: Session, user: User) -> UserProfile | None:
    return db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))


def upsert_profile(
    db: Session,
    user: User,
    risk_appetite: str | None = None,
    investment_style: str | None = None,
    sectors: list[str] | None = None,
) -> UserProfile:
    """Save the optional investor profile. Every field may stay empty."""
    profile = get_profile(db, user)
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    if risk_appetite is not None:
        profile.risk_appetite = risk_appetite
    if investment_style is not None:
        profile.investment_style = investment_style
    if sectors is not None:
        profile.sectors = sectors

    user.onboarded_at = user.onboarded_at or utcnow()
    db.flush()
    return profile


def user_payload(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "picture_url": user.picture_url,
        "is_guest": user.is_guest,
        "onboarded": user.onboarded_at is not None,
        "has_watchlist": bool(watchlists_service.list_watchlists(db, user)),
    }


def profile_payload(db: Session, user: User) -> dict:
    profile = get_profile(db, user)
    return {
        "risk_appetite": profile.risk_appetite if profile else None,
        "investment_style": profile.investment_style if profile else None,
        "sectors": (profile.sectors or []) if profile else [],
        "onboarded": user.onboarded_at is not None,
    }
