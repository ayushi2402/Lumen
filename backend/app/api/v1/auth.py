"""Authentication routes.

The flow is: the frontend obtains a Google ID token, posts it here, and the
backend verifies it and issues its own session token. LUMEN owns identity;
Google is only the identity provider. No Supabase Auth is involved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.auth.google import GoogleAuthError, get_verifier
from app.auth.tokens import issue_token
from app.config import Settings, get_settings
from app.db.models import User
from app.schemas.api import (
    GoogleLoginIn,
    ProfileIn,
    ProfileOut,
    SessionOut,
    StarterSetupIn,
    UserOut,
)
from app.services import catalog, users
from app.services import digest as digest_service
from app.services import watchlists as watchlists_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=SessionOut)
def login_with_google(
    body: GoogleLoginIn,
    db: Session = Depends(get_db_session),
) -> dict:
    """Verify a Google ID token and start a LUMEN session."""
    try:
        identity = get_verifier().verify(body.id_token)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    user, _ = users.get_or_create_from_google(db, identity)
    # Records the visit without advancing the digest baseline, so returning
    # users still see what changed while they were away.
    digest_service.mark_app_opened(db, user)
    token, expires = issue_token(user.id, is_guest=False)
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires,
        "user": users.user_payload(db, user),
    }


@router.post("/guest", response_model=SessionOut)
def start_guest_session(
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start a demo session with no account - the "Try LUMEN" path."""
    if not settings.allow_guest_sessions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Guest sessions are disabled."
        )

    user = users.create_guest(db)
    digest_service.mark_app_opened(db, user)
    token, expires = issue_token(user.id, is_guest=True)
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires,
        "user": users.user_payload(db, user),
    }


@router.get("/me", response_model=UserOut)
def me(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    return users.user_payload(db, user)


@router.get("/profile", response_model=ProfileOut)
def get_profile(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    return users.profile_payload(db, user)


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Save the optional investor profile. Skipping it entirely is fine."""
    users.upsert_profile(
        db,
        user,
        risk_appetite=body.risk_appetite,
        investment_style=body.investment_style,
        sectors=body.sectors,
    )
    db.commit()
    return users.profile_payload(db, user)


@router.get("/starter-suggestions")
def starter_suggestions(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Liquid, sector-spread suggestions for a user with no watchlist yet."""
    instruments = catalog.recommended_starter_instruments(db)
    return {
        "suggestions": [
            {"symbol": i.symbol, "name": i.name, "sector": i.sector} for i in instruments
        ]
    }


@router.post("/setup")
def complete_setup(
    body: StarterSetupIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Create the starter watchlist and establish the first digest baseline.

    Setting the baseline here is what makes the *next* visit able to say
    "since you were away" - without it the first return would have nothing to
    compare against.
    """
    watchlist = watchlists_service.ensure_starter_watchlist(db, user, body.symbols)
    if body.watchlist_name:
        watchlists_service.rename_watchlist(db, user, watchlist.id, body.watchlist_name)

    for symbol in body.symbols:
        try:
            watchlists_service.add_instrument(db, user, watchlist.id, symbol)
        except watchlists_service.WatchlistError:
            continue

    digest_service.mark_digest_reviewed(db, user)
    users.upsert_profile(db, user)
    db.commit()

    return {
        "watchlist_id": watchlist.id,
        "watchlist_name": watchlist.name,
        "symbols": watchlists_service.watched_symbols(db, user),
        "baseline_established": True,
    }
