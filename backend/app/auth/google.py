"""Google ID token verification.

The backend owns identity. The frontend obtains a Google ID token and posts it
here; LUMEN verifies that token itself against Google's published signing keys
and then issues its own session token. Google is an identity *provider*, not
LUMEN's session authority - which is why nothing downstream ever sees a Google
token again.

Verification is real, not decorative: RS256 signature against Google's JWKS,
plus issuer, audience and expiry checks. A token that fails any of these is
rejected rather than trusted on its claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt
from jwt import PyJWKClient

from app.config import Settings, get_settings

GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleAuthError(ValueError):
    """The supplied Google credential could not be verified."""


@dataclass(frozen=True)
class GoogleIdentity:
    """The verified claims LUMEN keeps.

    ``subject`` is Google's stable ``sub``. It is the identity anchor because
    an email address can be changed or reassigned and ``sub`` cannot.
    """

    subject: str
    email: str | None
    name: str | None
    picture: str | None
    email_verified: bool


class GoogleVerifier(Protocol):
    """Seam allowing tests to substitute verification without network access."""

    def verify(self, id_token: str) -> GoogleIdentity: ...


class LiveGoogleVerifier:
    """Verifies against Google's live JWKS endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._jwks: PyJWKClient | None = None

    def _client(self) -> PyJWKClient:
        # Created lazily and reused: PyJWKClient caches signing keys, so this
        # is not a network round trip per login.
        if self._jwks is None:
            self._jwks = PyJWKClient(GOOGLE_JWKS_URI, cache_keys=True)
        return self._jwks

    def verify(self, id_token: str) -> GoogleIdentity:
        if not self.settings.google_client_id:
            raise GoogleAuthError(
                "GOOGLE_CLIENT_ID is not configured; Google sign-in is unavailable."
            )
        try:
            signing_key = self._client().get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.google_client_id,
                issuer=list(GOOGLE_ISSUERS),
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"Invalid Google credential: {exc}") from exc
        except Exception as exc:  # network/JWKS failures
            raise GoogleAuthError(f"Could not verify Google credential: {exc}") from exc

        return _identity_from_claims(claims)


def _identity_from_claims(claims: dict) -> GoogleIdentity:
    subject = claims.get("sub")
    if not subject:
        raise GoogleAuthError("Google credential has no subject claim.")
    return GoogleIdentity(
        subject=str(subject),
        email=claims.get("email"),
        name=claims.get("name"),
        picture=claims.get("picture"),
        email_verified=bool(claims.get("email_verified", False)),
    )


_verifier: GoogleVerifier | None = None


def get_verifier() -> GoogleVerifier:
    """The active verifier. Overridable in tests via ``set_verifier``."""
    global _verifier
    if _verifier is None:
        _verifier = LiveGoogleVerifier()
    return _verifier


def set_verifier(verifier: GoogleVerifier | None) -> None:
    """Install a verifier (or reset to the live one with ``None``)."""
    global _verifier
    _verifier = verifier
