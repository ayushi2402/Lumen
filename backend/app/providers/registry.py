"""Provider selection, fallback chain and quote caching.

Upstox is no longer required for LUMEN to work. The default chain is:

    jugaad (NSE) -> yfinance -> replay

Each link is tried in order; the first that returns usable data wins, and the
selection records which one actually served the request so the UI can say so.

Two rules keep this honest:

* **Replay is never substituted for live data silently.** It is the last link
  only when explicitly permitted (``allow_replay_fallback``), and any response
  it serves carries ``is_replay`` and the "Demo / Replay" label all the way to
  the frontend.
* **A degraded live request says so.** If every live provider fails and replay
  is not permitted, the selection is marked degraded with the reason, rather
  than returning synthetic prices dressed as real ones.

A short TTL cache sits in front of quotes so a dashboard load, a watchlist view
and a stock page in the same minute cost one upstream call, not three. It is
in-process by design - Redis was explicitly out of scope, and a dict is the
right size for a single free-tier instance.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.config import Settings, get_settings
from app.providers.base import (
    MarketDataProvider,
    ProviderError,
    ProviderStatus,
    RawResponse,
)
from app.providers.jugaad_provider import JugaadDataProvider
from app.providers.replay import ReplayProvider
from app.providers.upstox import UpstoxProvider
from app.providers.yfinance_provider import YFinanceProvider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY, get_scenario


class DataMode(str, Enum):
    """Which kind of data the caller is asking for. Never inferred."""

    LIVE = "live"
    REPLAY = "replay"


REPLAY_LABEL = "Demo / Replay"
LIVE_LABEL = "Market data"
DEGRADED_LABEL = "Last known data - live feed unavailable"

_BUILDERS = {
    "jugaad": JugaadDataProvider,
    "yfinance": YFinanceProvider,
    "upstox": UpstoxProvider,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@dataclass
class QuoteCache:
    """Small TTL cache for quote batches, with a stale-serving escape hatch."""

    ttl_seconds: int = 45
    _entries: dict[str, tuple[datetime, RawResponse]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str, now: datetime | None = None) -> RawResponse | None:
        now = now or _utcnow()
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, response = entry
        if (now - stored_at).total_seconds() > self.ttl_seconds:
            return None
        return response

    def put(self, key: str, response: RawResponse, now: datetime | None = None) -> None:
        if response.ok:
            with self._lock:
                self._entries[key] = (now or _utcnow(), response)

    def get_stale(self, key: str) -> RawResponse | None:
        """Last known payload regardless of age, restamped as unavailable.

        ``as_of`` is preserved so freshness reports the real age; only the
        status is downgraded. This is the "expose last known data with a stale
        label" branch of the fallback policy.
        """
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        _, response = entry
        return RawResponse(
            provider=response.provider,
            payload=response.payload,
            fetched_at=_utcnow(),
            as_of=response.as_of,
            status=ProviderStatus.UNAVAILABLE,
            is_replay=response.is_replay,
            meta={**response.meta, "served_from": "last_known_cache", "stale": True},
        )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


QUOTE_CACHE = QuoteCache()

# Candles change far more slowly than quotes and are fetched per instrument,
# so without this a 13-stock dashboard would issue 13 separate downloads on
# every single request.
CANDLE_CACHE = QuoteCache(ttl_seconds=300)


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


class ProviderChain:
    """Tries several providers in order and reports which one answered."""

    is_replay = False

    def __init__(
        self,
        providers: list[MarketDataProvider],
        cache: QuoteCache | None = None,
        replay_fallback: ReplayProvider | None = None,
        candle_cache: QuoteCache | None = None,
    ) -> None:
        self.providers = providers
        self.cache = cache or QUOTE_CACHE
        self.candle_cache = candle_cache or CANDLE_CACHE
        self.replay_fallback = replay_fallback
        self.last_provider: str | None = None
        self.attempts: list[str] = []

    @property
    def name(self) -> str:
        return self.last_provider or (
            self.providers[0].name if self.providers else "none"
        )

    def is_available(self) -> bool:
        return any(p.is_available() for p in self.providers) or (
            self.replay_fallback is not None
        )

    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        cache_key = "quotes:" + ",".join(sorted(instrument_keys))
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.last_provider = cached.provider
            self.attempts = ["cache"]
            return cached

        self.attempts = []
        for provider in self.providers:
            if not provider.is_available():
                self.attempts.append(f"{provider.name}:unavailable")
                continue
            try:
                response = provider.get_quotes(instrument_keys)
            except ProviderError as exc:
                self.attempts.append(f"{provider.name}:{exc.status.value}")
                continue
            except Exception as exc:  # a third-party provider may raise anything
                self.attempts.append(f"{provider.name}:error({type(exc).__name__})")
                continue

            self.last_provider = provider.name
            self.cache.put(cache_key, response)
            return response

        # Every live provider failed. Prefer genuinely-old real data over
        # synthetic data: a stale real price is still a real price.
        stale = self.cache.get_stale(cache_key)
        if stale is not None:
            self.attempts.append("cache:stale")
            self.last_provider = stale.provider
            return stale

        if self.replay_fallback is not None:
            self.attempts.append("replay:fallback")
            self.last_provider = self.replay_fallback.name
            return self.replay_fallback.get_quotes(instrument_keys)

        raise ProviderError(
            f"No market data provider available (tried: {', '.join(self.attempts) or 'none'}).",
            ProviderStatus.UNAVAILABLE,
        )

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        return self._first_success(
            lambda p: p.get_historical_candles(instrument_key, interval, start, end),
            f"historical:{instrument_key}",
        )

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        return self._first_success(
            lambda p: p.get_intraday_candles(instrument_key, interval),
            f"intraday:{instrument_key}",
        )

    def _first_success(self, call, label: str) -> RawResponse:
        # Candle requests are per instrument, so caching them is what keeps a
        # multi-stock page from issuing one download per row on every load.
        cached = self.candle_cache.get(label)
        if cached is not None:
            self.last_provider = cached.provider
            return cached

        errors: list[str] = []
        for provider in self.providers:
            if not provider.is_available():
                continue
            try:
                response = call(provider)
                self.last_provider = provider.name
                self.candle_cache.put(label, response)
                return response
            except ProviderError as exc:
                errors.append(f"{provider.name}:{exc.status.value}")
            except Exception as exc:
                errors.append(f"{provider.name}:error({type(exc).__name__})")

        if self.replay_fallback is not None:
            return call(self.replay_fallback)
        raise ProviderError(
            f"No provider could serve {label} ({', '.join(errors) or 'none available'}).",
            ProviderStatus.UNAVAILABLE,
        )

    # Passed through so the pipeline can ask for market context when the
    # active provider happens to be replay.
    def benchmark_return(self) -> float | None:
        getter = getattr(self.replay_fallback, "benchmark_return", None)
        return getter() if callable(getter) and self.last_provider == "replay" else None


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSelection:
    provider: MarketDataProvider
    mode: DataMode
    is_replay: bool
    degraded: bool
    reason: str
    label: str
    chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        active = getattr(self.provider, "last_provider", None) or getattr(
            self.provider, "name", "unknown"
        )
        return {
            "provider": active,
            "mode": self.mode.value,
            "is_replay": self.is_replay,
            "degraded": self.degraded,
            "reason": self.reason,
            "label": self.label,
            "chain": self.chain,
            "attempts": list(getattr(self.provider, "attempts", []) or []),
        }


def build_chain(settings: Settings | None = None) -> list[MarketDataProvider]:
    """Instantiate the configured provider chain, skipping unusable links."""
    settings = settings or get_settings()
    providers: list[MarketDataProvider] = []

    for name in [n.strip().lower() for n in settings.market_provider_chain.split(",")]:
        builder = _BUILDERS.get(name)
        if builder is None:
            continue
        provider = builder(settings) if name == "upstox" else builder()
        # Upstox without a token is skipped rather than allowed to fail every
        # request - it must never be a required dependency.
        if not provider.is_available():
            continue
        providers.append(provider)

    return providers


def select_provider(
    mode: DataMode = DataMode.LIVE,
    scenario_key: str = DEFAULT_SCENARIO_KEY,
    step: int = 0,
    settings: Settings | None = None,
) -> ProviderSelection:
    """Pick a provider for the requested mode.

    ``mode`` comes from the caller and is never guessed. A replay request
    always gets replay data; a live request gets real data, genuinely-old real
    data, or - only if permitted - clearly-labelled replay.
    """
    settings = settings or get_settings()

    if mode is DataMode.REPLAY:
        provider = ReplayProvider(get_scenario(scenario_key), step=step)
        return ProviderSelection(
            provider=provider,
            mode=DataMode.REPLAY,
            is_replay=True,
            degraded=False,
            reason="Replay mode requested.",
            label=REPLAY_LABEL,
            chain=["replay"],
        )

    providers = build_chain(settings)
    replay_fallback = (
        ReplayProvider(get_scenario(scenario_key), step=step)
        if settings.allow_replay_fallback
        else None
    )
    chain = ProviderChain(providers, QUOTE_CACHE, replay_fallback)
    names = [p.name for p in providers]

    if not providers:
        if replay_fallback is not None:
            return ProviderSelection(
                provider=chain,
                mode=DataMode.LIVE,
                is_replay=True,
                degraded=True,
                reason=(
                    "No live market provider is available; showing demo data. "
                    "This is clearly labelled and is not live market data."
                ),
                label=REPLAY_LABEL,
                chain=["replay"],
            )
        return ProviderSelection(
            provider=chain,
            mode=DataMode.LIVE,
            is_replay=False,
            degraded=True,
            reason="No live market data provider is configured or reachable.",
            label=DEGRADED_LABEL,
            chain=[],
        )

    return ProviderSelection(
        provider=chain,
        mode=DataMode.LIVE,
        is_replay=False,
        degraded=False,
        reason=f"Live chain: {' -> '.join(names)}.",
        label=LIVE_LABEL,
        chain=names,
    )
