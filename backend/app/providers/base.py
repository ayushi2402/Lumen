"""Market-data provider interface.

Providers return **raw** payloads, exactly as the upstream API shaped them.
They are deliberately not asked to produce ``MarketObservation`` objects:
pushing normalization into every provider would duplicate the interesting
logic per vendor and make the engine's inputs depend on vendor quirks.

The translation from raw payload to engine input lives in one place,
``app.market.normalization``. Providers stay thin and boring; normalization
stays single and testable.

Raw payloads never leave the backend. The API layer serves normalized data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ProviderStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class ProviderError(RuntimeError):
    """A provider call failed. Carries a status so callers can pick a fallback."""

    def __init__(self, message: str, status: ProviderStatus = ProviderStatus.ERROR) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RawResponse:
    """An untouched provider payload plus the metadata freshness needs.

    ``as_of`` is the provider's own timestamp when it supplies one, falling
    back to fetch time. Trusting the provider's clock over ours is what makes
    a lagging feed report as delayed rather than as live.
    """

    provider: str
    payload: Any
    fetched_at: datetime
    as_of: datetime
    status: ProviderStatus = ProviderStatus.OK
    is_replay: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is ProviderStatus.OK


@runtime_checkable
class MarketDataProvider(Protocol):
    """What LUMEN needs from any market-data source.

    Kept small on purpose - quotes, historical candles, intraday candles. Every
    signal the engine computes is derivable from these three.
    """

    name: str
    is_replay: bool

    def is_available(self) -> bool:
        """Whether this provider can currently serve requests."""
        ...

    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        """Latest quote for a batch of instruments.

        Batched by contract: polling the union of all watchlisted instruments
        in one call is what keeps work O(instruments) rather than O(users).
        """
        ...

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        """Daily/weekly candles used to build baselines."""
        ...

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        """Current-session candles used for realized intraday volatility."""
        ...
