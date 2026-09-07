"""Provider chain, adapters, seeding and polling.

These run entirely offline. The live-network behaviour of the NSE and yfinance
adapters was verified manually during development (both returned real data and
agreed on the same closing price); what is tested here is the plumbing around
them - fallback order, degradation, caching, and the guarantee that Upstox is
never required.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.market.normalization import parse_candles, parse_quotes
from app.providers.base import ProviderError, ProviderStatus, RawResponse
from app.providers.jugaad_provider import JugaadDataProvider
from app.providers.registry import (
    QuoteCache,
    DataMode,
    ProviderChain,
    build_chain,
    select_provider,
)
from app.providers.replay import ReplayProvider
from app.providers.upstox import UpstoxProvider
from app.providers.yfinance_provider import (
    YFinanceProvider,
    from_yahoo_symbol,
    to_yahoo_symbol,
)
from app.replay.scenarios import get_scenario

NOW = datetime(2026, 3, 12, 4, 0)


class _StubProvider:
    """A provider whose behaviour the test dictates."""

    is_replay = False

    def __init__(self, name: str, available: bool = True, fail: bool = False) -> None:
        self.name = name
        self._available = available
        self._fail = fail
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def get_quotes(self, instrument_keys):
        self.calls += 1
        if self._fail:
            raise ProviderError(f"{self.name} down", ProviderStatus.UNAVAILABLE)
        return RawResponse(
            provider=self.name,
            payload={"quotes": {"RELIANCE": {"last_price": 100.0, "previous_close": 98.0}}},
            fetched_at=NOW,
            as_of=NOW,
        )

    def get_historical_candles(self, instrument_key, interval, start, end):
        if self._fail:
            raise ProviderError(f"{self.name} down", ProviderStatus.UNAVAILABLE)
        return RawResponse(
            provider=self.name,
            payload={"candles": [{"timestamp": NOW.isoformat(), "close": 100.0}]},
            fetched_at=NOW,
            as_of=NOW,
        )

    def get_intraday_candles(self, instrument_key, interval):
        raise ProviderError("no intraday", ProviderStatus.UNAVAILABLE)


# ---------------------------------------------------------------------------
# Adapters instantiate and map symbols
# ---------------------------------------------------------------------------


def test_yfinance_provider_is_installed_and_instantiable() -> None:
    provider = YFinanceProvider()
    assert provider.is_available() is True
    assert provider.is_replay is False


def test_jugaad_provider_is_installed_and_instantiable() -> None:
    provider = JugaadDataProvider()
    assert provider.is_available() is True
    assert provider.is_replay is False


def test_yahoo_symbol_mapping_round_trips() -> None:
    assert to_yahoo_symbol("RELIANCE") == "RELIANCE.NS"
    assert to_yahoo_symbol("NIFTY50") == "^NSEI"
    assert from_yahoo_symbol("TCS.NS") == "TCS"
    assert from_yahoo_symbol("^NSEI") == "NIFTY50"


def test_nse_provider_declines_intraday_rather_than_faking_it() -> None:
    """No data is better than invented data; the engine handles unavailable."""
    with pytest.raises(ProviderError):
        JugaadDataProvider().get_intraday_candles("NSE_EQ|RELIANCE", "15minute")


def test_upstox_without_a_token_is_unavailable_not_broken() -> None:
    provider = UpstoxProvider(Settings(upstox_access_token=None))
    assert provider.is_available() is False


# ---------------------------------------------------------------------------
# Chain behaviour
# ---------------------------------------------------------------------------


def test_chain_uses_the_first_working_provider() -> None:
    first = _StubProvider("first")
    second = _StubProvider("second")
    chain = ProviderChain([first, second], QuoteCache(ttl_seconds=0))

    response = chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert response.provider == "first"
    assert second.calls == 0


def test_chain_falls_through_to_the_next_provider_on_failure() -> None:
    broken = _StubProvider("broken", fail=True)
    working = _StubProvider("working")
    chain = ProviderChain([broken, working], QuoteCache(ttl_seconds=0))

    response = chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert response.provider == "working"
    assert any("broken" in attempt for attempt in chain.attempts)


def test_chain_skips_unavailable_providers() -> None:
    offline = _StubProvider("offline", available=False)
    working = _StubProvider("working")
    chain = ProviderChain([offline, working], QuoteCache(ttl_seconds=0))

    assert chain.get_quotes(["NSE_EQ|RELIANCE"]).provider == "working"
    assert offline.calls == 0


def test_chain_prefers_stale_real_data_over_synthetic_data() -> None:
    """A stale real price is still a real price; replay is the last resort."""
    cache = QuoteCache(ttl_seconds=60)
    working = _StubProvider("working")
    chain = ProviderChain([working], cache, ReplayProvider(get_scenario("quiet_session")))

    first = chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert first.provider == "working"

    # The provider dies; the cached entry ages out of the fresh window.
    working._fail = True
    cache.ttl_seconds = 0
    stale = chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert stale.provider == "working"
    assert stale.meta.get("stale") is True
    assert stale.is_replay is False


def test_chain_falls_back_to_replay_only_as_a_last_resort() -> None:
    broken = _StubProvider("broken", fail=True)
    replay = ReplayProvider(get_scenario("crash_stable_market"), step=5)
    chain = ProviderChain([broken], QuoteCache(ttl_seconds=0), replay)

    response = chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert response.is_replay is True
    assert "replay:fallback" in chain.attempts


def test_chain_without_replay_raises_rather_than_inventing_data() -> None:
    chain = ProviderChain([_StubProvider("broken", fail=True)], QuoteCache(ttl_seconds=0))
    with pytest.raises(ProviderError):
        chain.get_quotes(["NSE_EQ|RELIANCE"])


def test_chain_caches_within_the_ttl() -> None:
    provider = _StubProvider("cached")
    chain = ProviderChain([provider], QuoteCache(ttl_seconds=300))

    chain.get_quotes(["NSE_EQ|RELIANCE"])
    chain.get_quotes(["NSE_EQ|RELIANCE"])
    assert provider.calls == 1, "a repeat request inside the TTL must not refetch"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_chain_does_not_include_upstox() -> None:
    """The locked requirement, asserted directly."""
    assert "upstox" not in Settings().market_provider_chain


def test_build_chain_honours_configuration_order() -> None:
    chain = build_chain(Settings(market_provider_chain="yfinance,jugaad"))
    assert [p.name for p in chain] == ["yfinance", "jugaad"]


def test_build_chain_ignores_unknown_provider_names() -> None:
    chain = build_chain(Settings(market_provider_chain="nonsense,yfinance"))
    assert [p.name for p in chain] == ["yfinance"]


def test_replay_mode_never_consults_a_live_provider() -> None:
    selection = select_provider(DataMode.REPLAY, "crash_stable_market", 5)
    assert isinstance(selection.provider, ReplayProvider)
    assert selection.label == "Demo / Replay"


# ---------------------------------------------------------------------------
# Payload parsing for the new providers
# ---------------------------------------------------------------------------


def test_flat_quote_payloads_parse() -> None:
    raw = RawResponse(
        provider="yfinance",
        payload={
            "quotes": {
                "RELIANCE": {
                    "last_price": 1322.0,
                    "previous_close": 1302.5,
                    "open": 1305.0,
                    "volume": 13031534.0,
                    "as_of": NOW.isoformat(),
                }
            }
        },
        fetched_at=NOW,
        as_of=NOW,
    )
    quote = parse_quotes(raw)["RELIANCE"]
    assert quote.last_price == 1322.0
    assert quote.daily_return == pytest.approx(1.497, abs=0.01)
    assert quote.gap_percent == pytest.approx(0.192, abs=0.01)


def test_flat_quotes_skip_entries_without_a_previous_close() -> None:
    raw = RawResponse(
        provider="yfinance",
        payload={"quotes": {"BAD": {"last_price": 100.0}}},
        fetched_at=NOW,
        as_of=NOW,
    )
    assert parse_quotes(raw) == {}


def test_dict_candle_rows_parse() -> None:
    raw = RawResponse(
        provider="jugaad",
        payload={
            "candles": [
                {"timestamp": NOW.isoformat(), "open": 1, "high": 2, "low": 0.5,
                 "close": 1.5, "volume": 100},
                {"nonsense": True},
            ]
        },
        fetched_at=NOW,
        as_of=NOW,
    )
    candles = parse_candles(raw)
    assert len(candles) == 1
    assert candles[0]["close"] == 1.5
    assert isinstance(candles[0]["timestamp"], datetime)


# ---------------------------------------------------------------------------
# Seeding and polling
# ---------------------------------------------------------------------------


def test_seed_computes_and_stores_baselines(db) -> None:
    """The seed path, exercised with a stub provider instead of the network."""
    from app.scripts.seed_market_data import seed_instrument
    from app.services import baselines as baselines_service
    from app.services import catalog

    class _HistoryProvider(_StubProvider):
        def get_historical_candles(self, instrument_key, interval, start, end):
            base = datetime(2026, 1, 5)
            candles = [
                {
                    "timestamp": (base + timedelta(days=i)).isoformat(),
                    "open": 100 + i * 0.4,
                    "high": 101 + i * 0.4,
                    "low": 99 + i * 0.4,
                    "close": 100 + i * 0.5 + (1.5 if i % 3 == 0 else -1.0),
                    "volume": 1_000_000 + i * 5_000,
                }
                for i in range(60)
            ]
            return RawResponse(
                provider="stub", payload={"candles": candles}, fetched_at=NOW, as_of=NOW
            )

    instrument = catalog.get_by_symbol(db, "RELIANCE")
    changed, message = seed_instrument(
        db, instrument, _HistoryProvider("stub"), days=180, force=True, max_age_days=5
    )
    db.commit()

    assert changed is True
    assert "60 sessions" in message

    stored = baselines_service.load_baselines(db, ["RELIANCE"])["RELIANCE"]
    assert baselines_service.baseline_source(stored) == "historical"
    assert stored.historical_volatility is not None
    assert stored.volatility_sample_days >= 30


def test_seed_is_idempotent(db) -> None:
    """Re-running must not re-download history that is still current."""
    from app.scripts.seed_market_data import seed_instrument
    from app.services import catalog

    instrument = catalog.get_by_symbol(db, "INFY")
    provider = _StubProvider("stub")

    from app.services import baselines as baselines_service

    baselines_service.upsert_daily_stats(
        db, instrument,
        {"typical_daily_move": 1.0, "typical_move_sample_days": 40,
         "historical_volatility": 1.4, "volatility_sample_days": 40,
         "average_volume": 1e6, "historical_gap_stdev": 0.5, "previous_close": 1500.0},
    )
    db.commit()

    changed, message = seed_instrument(
        db, instrument, provider, days=180, force=False, max_age_days=5
    )
    assert changed is False
    assert message == "already fresh"


def test_baselines_fall_back_to_the_synthetic_universe_before_seeding(db) -> None:
    """The product works before a seed has ever run."""
    from app.services import baselines as baselines_service

    loaded = baselines_service.load_baselines(db, ["TATAMOTORS"])
    assert baselines_service.baseline_source(loaded["TATAMOTORS"]) == "synthetic"


def test_polling_is_safe_without_watched_instruments(db) -> None:
    from app.services.polling import poll_once

    result = poll_once()
    # No database is configured in the test process, so this must degrade
    # rather than raise - the loop can never take the app down.
    assert result["status"] in {"skipped", "idle", "ok", "error"}


def test_poller_reports_status_without_being_started() -> None:
    from app.services.polling import MarketPoller

    poller = MarketPoller(Settings(enable_background_polling=False))
    status = poller.status()
    assert status["running"] is False
    assert status["cycles"] == 0


def test_poller_start_is_a_noop_when_disabled() -> None:
    from app.services.polling import MarketPoller

    poller = MarketPoller(Settings(enable_background_polling=False))
    poller.start()
    assert poller.running is False


# ---------------------------------------------------------------------------
# Production configuration
# ---------------------------------------------------------------------------


def test_production_flags_missing_secrets_and_sqlite() -> None:
    warnings = Settings(
        environment="production", database_url="sqlite:///./x.db", debug=True
    ).production_warnings()
    joined = " ".join(warnings)
    assert "SESSION_SECRET" in joined
    assert "SQLite must not be used in production" in joined
    assert "DEBUG" in joined


def test_development_configuration_produces_no_warnings() -> None:
    assert Settings(environment="development").production_warnings() == []


def test_cors_always_allows_local_development() -> None:
    origins = Settings(cors_origins="https://lumen.vercel.app").allowed_origins
    assert "http://localhost:3000" in origins
    assert "https://lumen.vercel.app" in origins


def test_nse_declines_large_quote_batches_so_the_chain_can_batch() -> None:
    """NSE scrapes one symbol at a time; a 13-symbol dashboard must not wait.

    Declining is deliberate: the registry then falls through to yfinance,
    which fetches the whole batch in a single call.
    """
    provider = JugaadDataProvider()
    many = [f"NSE_EQ|SYM{i}" for i in range(provider.MAX_QUOTE_BATCH + 1)]

    with pytest.raises(ProviderError) as excinfo:
        provider.get_quotes(many)
    assert excinfo.value.status is ProviderStatus.UNAVAILABLE
    assert "batch" in str(excinfo.value).lower()


def test_chain_falls_through_when_nse_declines_a_large_batch() -> None:
    working = _StubProvider("yfinance")
    chain = ProviderChain([JugaadDataProvider(), working], QuoteCache(ttl_seconds=0))

    response = chain.get_quotes([f"NSE_EQ|SYM{i}" for i in range(20)])
    assert response.provider == "yfinance"
    assert any("jugaad" in attempt for attempt in chain.attempts)
