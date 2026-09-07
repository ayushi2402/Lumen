"""FastAPI auth dependencies.

Every user-scoped route depends on ``current_user``. That is the single point
where identity is established, which is what makes "user data is isolated by
authenticated user" enforceable rather than aspirational: services take a
``User`` and filter by ``user.id``, and no route reads a user id from the
request body.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.auth.tokens import SessionTokenError, bearer_from_header, decode_token
from app.db.models import User
from app.db.session import DatabaseNotConfiguredError, get_session_factory

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_db_session() -> Iterator[Session]:
    """Database session, or a clear 503 when no database is configured."""
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No database configured. Set DATABASE_URL to enable "
                "watchlists, events and personalization."
            ),
        )
    session = factory()
    try:
        yield session
    finally:
        session.close()


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db_session),
) -> User:
    """Resolve the authenticated user from the bearer token."""
    try:
        token = bearer_from_header(authorization)
        claims = decode_token(token)
    except SessionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, claims.user_id)
    if user is None:
        raise UNAUTHENTICATED
    return user


def optional_user(
    authorization: str | None = Header(default=None),
) -> int | None:
    """User id when a valid token is present, otherwise ``None``.

    For endpoints that are public but personalize when signed in, such as the
    landing page and the scenario catalogue.
    """
    if not authorization:
        return None
    try:
        return decode_token(bearer_from_header(authorization)).user_id
    except SessionTokenError:
        return None


def require_debug_access(
    x_lumen_debug_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Guard the intelligence debug endpoint.

    Closed by default: with no ``DEBUG_API_TOKEN`` configured the endpoint is
    unavailable rather than open. An internals-inspection endpoint that
    defaults to public is a liability, so the failure mode is 'off'.
    """
    if not settings.debug_api_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Debug endpoint is not enabled.",
        )
    if not x_lumen_debug_token or x_lumen_debug_token != settings.debug_api_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid debug token.",
        )


__all__ = [
    "DatabaseNotConfiguredError",
    "current_user",
    "get_db_session",
    "optional_user",
    "require_debug_access",
]
