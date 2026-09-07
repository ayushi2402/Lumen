"""LUMEN session tokens.

Once a Google credential is verified, LUMEN issues its own short-lived
session JWT and never touches the Google token again. Sessions are therefore
LUMEN's to manage: guest demo sessions work the same way with no Google
involvement at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app.config import Settings, get_settings

ALGORITHM = "HS256"
TOKEN_TYPE = "bearer"


class SessionTokenError(ValueError):
    """A session token was missing, malformed, or expired."""


@dataclass(frozen=True)
class SessionClaims:
    user_id: int
    is_guest: bool
    issued_at: datetime
    expires_at: datetime


def issue_token(
    user_id: int, is_guest: bool = False, settings: Settings | None = None
) -> tuple[str, datetime]:
    """Mint a session token. Returns ``(token, expires_at)``."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_token_ttl_hours)
    payload = {
        "sub": str(user_id),
        "guest": is_guest,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": "lumen",
    }
    token = jwt.encode(payload, settings.session_secret, algorithm=ALGORITHM)
    return token, expires.replace(tzinfo=None)


def decode_token(token: str, settings: Settings | None = None) -> SessionClaims:
    """Verify and decode a session token."""
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.session_secret,
            algorithms=[ALGORITHM],
            issuer="lumen",
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SessionTokenError(f"Invalid session token: {exc}") from exc

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionTokenError("Session token has no usable subject.") from exc

    return SessionClaims(
        user_id=user_id,
        is_guest=bool(claims.get("guest", False)),
        issued_at=datetime.fromtimestamp(claims["iat"], tz=timezone.utc).replace(tzinfo=None),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc).replace(tzinfo=None),
    )


def bearer_from_header(header: str | None) -> str:
    """Extract the token from an ``Authorization: Bearer ...`` header."""
    if not header:
        raise SessionTokenError("Missing Authorization header.")
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != TOKEN_TYPE:
        raise SessionTokenError("Authorization header must be 'Bearer <token>'.")
    return parts[1].strip()
