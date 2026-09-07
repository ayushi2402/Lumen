"""Upstox market-data provider.

STATUS: UNVERIFIED AGAINST THE LIVE API.
----------------------------------------
This machine has no Upstox account, client credentials or access token, so no
request in this module has ever been executed against the real service. The
endpoint paths, parameter names and response shapes follow the published
Upstox v2 REST contract, but they must be confirmed against the live API
before anyone relies on them.

What *is* verified is everything downstream: the replay provider emits the same
payload shape this one is written to return, so the normalization layer, the
engine and the API are exercised end to end by the test suite. Swapping this in
should be a configuration change rather than a code change - but treat the
first live call as an integration test, not a formality.

Operational note: Upstox access tokens expire daily and reissuing one requires
an interactive OAuth login. ``is_available()`` therefore reports availability
based on configuration, and a 401 is surfaced as ``UNAUTHORIZED`` so the
registry can fall back rather than presenting an outage as an empty market.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.providers.base import ProviderError, ProviderStatus, RawResponse

PROVIDER_NAME = "upstox"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UpstoxProvider:
    """Thin REST client. Returns raw payloads; performs no normalization."""

    name = PROVIDER_NAME
    is_replay = False

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # -- plumbing ----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.settings.upstox_access_token}",
        }

    def _request(self, path: str, params: dict[str, Any] | None = None) -> RawResponse:
        """Issue one GET and wrap the payload, mapping failures onto statuses."""
        if not self.settings.upstox_access_token:
            raise ProviderError(
                "No Upstox access token configured.", ProviderStatus.UNAUTHORIZED
            )

        url = f"{self.settings.upstox_api_base}{path}"
        client = self._client or httpx.Client(timeout=10.0)
        try:
            response = client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Upstox request failed: {exc}", ProviderStatus.UNAVAILABLE) from exc
        finally:
            if self._client is None:
                client.close()

        if response.status_code == 401:
            raise ProviderError(
                "Upstox rejected the access token (expired or invalid).",
                ProviderStatus.UNAUTHORIZED,
            )
        if response.status_code == 429:
            raise ProviderError("Upstox rate limit exceeded.", ProviderStatus.RATE_LIMITED)
        if response.status_code >= 400:
            raise ProviderError(
                f"Upstox returned HTTP {response.status_code}.", ProviderStatus.ERROR
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Upstox returned a non-JSON body.", ProviderStatus.ERROR) from exc

        fetched = _utcnow()
        return RawResponse(
            provider=self.name,
            payload=payload,
            fetched_at=fetched,
            # Upstox timestamps individual quotes; normalization prefers those
            # per-instrument values and falls back to fetch time.
            as_of=fetched,
            status=ProviderStatus.OK,
            is_replay=False,
            meta={"path": path},
        )

    # -- provider interface -------------------------------------------------
    def is_available(self) -> bool:
        """Configuration-level availability. Does not make a network call."""
        return bool(self.settings.upstox_access_token)

    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        """Batched quotes. One call for the union of all watched instruments."""
        if not instrument_keys:
            raise ProviderError("No instrument keys supplied.", ProviderStatus.ERROR)
        return self._request(
            "/market-quote/quotes", {"instrument_key": ",".join(instrument_keys)}
        )

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        """Daily candles used to build baselines.

        Rate-limited more tightly than quotes upstream, so this belongs in a
        seeding job and never in a request path.
        """
        return self._request(
            f"/historical-candle/{instrument_key}/{interval}"
            f"/{end.date().isoformat()}/{start.date().isoformat()}"
        )

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        return self._request(f"/historical-candle/intraday/{instrument_key}/{interval}")
