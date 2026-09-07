"""Market layer: calendar, virtual clock, freshness, normalization, breadth."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.config import Settings
from app.market.calendar import (
    MarketStatus,
    is_trading_day,
    market_status,
    minute_of_session,
    previous_trading_day,
    session_bounds,
    session_date_for,
    to_ist,
    trading_days_between,
)
from app.market.clock import SystemClock, VirtualClock
from app.market.context import compute_market_context
from app.market.freshness import Freshness, classify, detect_disagreement
from app.market.normalization import (
    NormalizationError,
    ObservationContext,
    daily_baselines,
    news_evidence_from,
    parse_candles,
    parse_quotes,
    realized_volatility,
    to_observation,
)
from app.providers.base import ProviderStatus, RawResponse
from app.providers.registry import DataMode, select_provider
from app.providers.replay import ReplayProvider
from app.replay.scenarios import get_scenario
from app.replay.universe import UNIVERSE

# 2026-03-12 is a Thursday. 04:00 UTC == 09:30 IST, inside the session.
MID_SESSION = datetime(2026, 3, 12, 4, 0)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_session_open_is_detected_in_ist_not_utc() -> None:
    """04:00 UTC is 09:30 IST - inside the session, despite looking like dawn."""
    assert market_status(MID_SESSION) is MarketStatus.OPEN
    assert to_ist(MID_SESSION).hour == 9


def test_weekend_and_holiday_are_distinguished_from_merely_closed() -> None:
    assert market_status(datetime(2026, 3, 14, 6, 0)) is MarketStatus.WEEKEND
    assert market_status(datetime(2026, 1, 26, 6, 0)) is MarketStatus.HOLIDAY
    assert market_status(datetime(2026, 3, 12, 14, 0)) is MarketStatus.CLOSED


def test_pre_open_window() -> None:
    # 03:40 UTC == 09:10 IST
    assert market_status(datetime(2026, 3, 12, 3, 40)) is MarketStatus.PRE_OPEN


def test_holidays_are_not_trading_days() -> None:
    assert is_trading_day(date(2026, 1, 26)) is False
    assert is_trading_day(date(2026, 3, 14)) is False  # Saturday
    assert is_trading_day(date(2026, 3, 12)) is True


def test_previous_trading_day_skips_the_weekend() -> None:
    assert previous_trading_day(date(2026, 3, 16)) == date(2026, 3, 13)


def test_session_date_before_open_belongs_to_the_previous_session() -> None:
    """At 08:00 IST on Monday the relevant session is still Friday's."""
    monday_early = datetime(2026, 3, 16, 2, 30)  # 08:00 IST
    assert session_date_for(monday_early) == date(2026, 3, 13)


def test_session_date_on_a_weekend_is_the_previous_session() -> None:
    assert session_date_for(datetime(2026, 3, 15, 6, 0)) == date(2026, 3, 13)


def test_trading_days_between_excludes_weekends() -> None:
    days = trading_days_between(date(2026, 3, 12), date(2026, 3, 17))
    assert days == [date(2026, 3, 13), date(2026, 3, 16), date(2026, 3, 17)]


def test_minute_of_session_is_none_outside_a_session() -> None:
    assert minute_of_session(MID_SESSION) == 15
    assert minute_of_session(datetime(2026, 3, 14, 6, 0)) is None


def test_session_bounds_round_trip() -> None:
    opened, closed = session_bounds(date(2026, 3, 12))
    assert market_status(opened) is MarketStatus.OPEN
    assert (closed - opened) == timedelta(minutes=375)


# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------


def test_virtual_clock_only_moves_when_told() -> None:
    clock = VirtualClock(MID_SESSION)
    assert clock.now() == MID_SESSION
    clock.advance(600)
    assert clock.now() == MID_SESSION + timedelta(seconds=600)


def test_virtual_clock_speed_scales_scenario_time() -> None:
    slow = VirtualClock(MID_SESSION, speed=1)
    fast = VirtualClock(MID_SESSION, speed=10)
    slow.advance(60)
    fast.advance(60)
    assert (fast.now() - MID_SESSION) == 10 * (slow.now() - MID_SESSION)


def test_system_clock_is_independent_of_virtual_clock() -> None:
    assert SystemClock().now() != VirtualClock(MID_SESSION).now()


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_freshness_thresholds_depend_on_market_status() -> None:
    """A 90-second-old quote is delayed mid-session and fine when closed."""
    settings = Settings()
    during = classify(MID_SESSION - timedelta(seconds=90), MID_SESSION, "upstox", settings)

    closed_now = datetime(2026, 3, 14, 6, 0)  # Saturday
    when_closed = classify(
        closed_now - timedelta(seconds=90), closed_now, "upstox", settings
    )

    assert during.state is Freshness.DELAYED
    assert when_closed.state is Freshness.LIVE


def test_stale_data_is_labelled_stale() -> None:
    report = classify(MID_SESSION - timedelta(hours=3), MID_SESSION, "upstox")
    assert report.state is Freshness.STALE
    assert "Stale" in report.label


def test_freshness_reports_source_and_timestamp() -> None:
    report = classify(MID_SESSION, MID_SESSION, "replay")
    payload = report.to_dict()
    assert payload["source"] == "replay"
    assert payload["as_of"] == MID_SESSION.isoformat()
    assert payload["state"] == "live"


def test_closed_market_label_explains_why() -> None:
    saturday = datetime(2026, 3, 14, 6, 0)
    report = classify(saturday, saturday, "upstox")
    assert "weekend" in report.label.lower()


def test_provider_disagreement_is_flagged_and_keeps_both_values() -> None:
    disagreement = detect_disagreement("price", "upstox", 100.0, "backup", 103.0)
    assert disagreement is not None
    payload = disagreement.to_dict()
    assert payload["primary"]["value"] == 100.0
    assert payload["secondary"]["value"] == 103.0
    assert payload["difference_percent"] == pytest.approx(3.0)


def test_small_provider_differences_are_not_flagged() -> None:
    assert detect_disagreement("price", "a", 100.0, "b", 100.2) is None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _raw(payload) -> RawResponse:
    return RawResponse(
        provider="test", payload=payload, fetched_at=MID_SESSION, as_of=MID_SESSION
    )


def test_parse_quotes_reads_upstox_shape() -> None:
    raw = _raw(
        {
            "status": "success",
            "data": {
                "NSE_EQ:RELIANCE": {
                    "instrument_token": "NSE_EQ|RELIANCE",
                    "last_price": 1245.3,
                    "volume": 1000,
                    "ohlc": {"open": 1290.0, "high": 1295.0, "low": 1240.0, "close": 1300.0},
                    "last_trade_time": MID_SESSION.isoformat(),
                }
            },
        }
    )
    snapshots = parse_quotes(raw)
    quote = snapshots["RELIANCE"]
    assert quote.last_price == 1245.3
    assert quote.previous_close == 1300.0
    assert quote.daily_return == pytest.approx(-4.208, abs=0.01)
    assert quote.gap_percent == pytest.approx(-0.769, abs=0.01)


def test_parse_quotes_skips_entries_without_a_usable_previous_close() -> None:
    """A quote LUMEN cannot trust must not become a scored observation."""
    raw = _raw(
        {
            "data": {
                "NSE_EQ:BAD": {"last_price": 100.0, "ohlc": {"close": 0}},
                "NSE_EQ:ALSOBAD": {"last_price": None, "ohlc": {"close": 50}},
            }
        }
    )
    assert parse_quotes(raw) == {}


def test_malformed_payload_raises_rather_than_returning_empty() -> None:
    with pytest.raises(NormalizationError):
        parse_quotes(_raw("not-an-object"))
    with pytest.raises(NormalizationError):
        parse_quotes(_raw({"no_data": True}))


def test_realized_volatility_is_scaled_to_daily_equivalent_units() -> None:
    """Per-candle dispersion must be comparable with daily volatility.

    Without square-root-of-time scaling, 15-minute returns would look tiny
    against a daily baseline and the volatility signal could never fire.
    """
    candles = [
        {"timestamp": MID_SESSION + timedelta(minutes=15 * i), "close": 100 + (i % 2)}
        for i in range(10)
    ]
    scaled, count = realized_volatility(candles, session_minutes=375)
    unscaled_stdev = 0.4975  # ~ per-period stdev of the alternating series

    assert count == 10
    assert scaled > unscaled_stdev * 4  # sqrt(375/15) == 5


def test_realized_volatility_reports_insufficient_data_rather_than_zero() -> None:
    value, count = realized_volatility([{"close": 100.0}])
    assert value is None
    assert count == 1


def test_daily_baselines_match_the_engine_input_shape() -> None:
    candles = [
        {"timestamp": None, "open": 100 + i, "high": 0, "low": 0,
         "close": 100 + i * 1.5, "volume": 1000 + i}
        for i in range(20)
    ]
    baselines = daily_baselines(candles)
    assert baselines["typical_daily_move"] is not None
    assert baselines["volatility_sample_days"] == 19
    assert baselines["average_volume"] is not None


def test_to_observation_never_invents_missing_inputs() -> None:
    raw = _raw(
        {
            "data": {
                "NSE_EQ:INFY": {
                    "instrument_token": "NSE_EQ|INFY",
                    "last_price": 1470.0,
                    "ohlc": {"close": 1500.0},
                }
            }
        }
    )
    quote = parse_quotes(raw)["INFY"]
    observation = to_observation(quote, ObservationContext())

    assert observation.historical_volatility is None
    assert observation.average_volume is None
    assert observation.typical_daily_move is None
    assert observation.news is None
    assert observation.volatility_sample_days == 0


def test_news_evidence_defaults_to_no_established_causation() -> None:
    evidence = news_evidence_from({"headline": "x", "relevance": 0.8, "confidence": 0.7})
    assert evidence.present is True
    assert evidence.causal_link_established is False


def test_news_evidence_is_none_without_a_payload() -> None:
    assert news_evidence_from(None) is None


def test_parse_candles_handles_positional_upstox_rows() -> None:
    raw = _raw(
        {"data": {"candles": [[MID_SESSION.isoformat(), 1, 2, 0.5, 1.5, 100, 0], "junk"]}}
    )
    candles = parse_candles(raw)
    assert len(candles) == 1
    assert candles[0]["close"] == 1.5


# ---------------------------------------------------------------------------
# Market breadth
# ---------------------------------------------------------------------------


def test_broad_participation_with_a_real_move_is_market_wide() -> None:
    returns = {f"S{i}": -3.5 for i in range(10)}
    context = compute_market_context(returns, benchmark_return=-3.4)
    assert context.is_market_wide_move is True
    assert context.declining == 10


def test_broad_participation_without_a_real_move_is_not_an_event() -> None:
    """Almost every session leans one way; drift alone is not a market move."""
    returns = {f"S{i}": -0.2 for i in range(10)}
    context = compute_market_context(returns, benchmark_return=-0.2)
    assert context.is_market_wide_move is False


def test_mixed_market_is_not_market_wide() -> None:
    returns = {"A": 3.0, "B": -3.0, "C": 2.5, "D": -2.5}
    assert compute_market_context(returns).is_market_wide_move is False


def test_empty_market_context_is_safe() -> None:
    context = compute_market_context({})
    assert context.total == 0
    assert context.is_market_wide_move is False


# ---------------------------------------------------------------------------
# Provider registry and fallback
# ---------------------------------------------------------------------------


def test_replay_mode_always_returns_the_replay_provider() -> None:
    selection = select_provider(DataMode.REPLAY, "crash_stable_market", 5)
    assert selection.is_replay is True
    assert selection.label == "Demo / Replay"
    assert isinstance(selection.provider, ReplayProvider)


def test_upstox_is_not_required_for_live_mode() -> None:
    """The locked requirement: Upstox must never be a dependency.

    With no Upstox token at all, the default chain still produces a working
    live selection from the NSE and yfinance providers.
    """
    selection = select_provider(
        DataMode.LIVE,
        settings=Settings(
            upstox_access_token=None, market_provider_chain="jugaad,yfinance"
        ),
    )
    assert selection.degraded is False
    assert selection.is_replay is False
    assert selection.chain == ["jugaad", "yfinance"]


def test_upstox_is_skipped_when_it_has_no_token() -> None:
    """An unusable link is dropped from the chain rather than failing it."""
    selection = select_provider(
        DataMode.LIVE,
        settings=Settings(
            upstox_access_token=None, market_provider_chain="upstox,yfinance"
        ),
    )
    assert "upstox" not in selection.chain
    assert "yfinance" in selection.chain


def test_upstox_joins_the_chain_when_configured_with_a_token() -> None:
    selection = select_provider(
        DataMode.LIVE,
        settings=Settings(
            upstox_access_token="test-token-not-real",
            market_provider_chain="upstox,yfinance",
        ),
    )
    assert selection.chain == ["upstox", "yfinance"]


def test_no_live_provider_falls_back_to_clearly_labelled_replay() -> None:
    """Demo data may substitute for a dead feed only if it says so loudly."""
    selection = select_provider(
        DataMode.LIVE,
        settings=Settings(market_provider_chain="", allow_replay_fallback=True),
    )
    assert selection.degraded is True
    assert selection.is_replay is True
    assert selection.label == "Demo / Replay"


def test_no_live_provider_and_no_replay_reports_degraded_without_faking() -> None:
    selection = select_provider(
        DataMode.LIVE,
        settings=Settings(market_provider_chain="", allow_replay_fallback=False),
    )
    assert selection.degraded is True
    assert selection.is_replay is False
    assert "unavailable" in selection.label.lower()


def test_quote_cache_serves_stale_data_preserving_its_age() -> None:
    """A stale real price beats a synthetic one, but must be labelled stale."""
    from app.providers.registry import QuoteCache

    cache = QuoteCache(ttl_seconds=30)
    original = RawResponse(
        provider="yfinance", payload={"quotes": {}}, fetched_at=MID_SESSION, as_of=MID_SESSION
    )
    cache.put("quotes", original, now=MID_SESSION)

    # Inside the TTL it is served as a normal hit.
    assert cache.get("quotes", now=MID_SESSION) is not None
    # Outside it, the fresh path misses but the stale path still answers.
    assert cache.get("quotes", now=MID_SESSION + timedelta(minutes=30)) is None

    recalled = cache.get_stale("quotes")
    assert recalled is not None
    assert recalled.as_of == MID_SESSION, "age must reflect the original timestamp"
    assert recalled.status is ProviderStatus.UNAVAILABLE
    assert recalled.meta["stale"] is True


def test_replay_provider_intraday_candles_grow_with_the_step() -> None:
    scenario = get_scenario("crash_stable_market")
    key = UNIVERSE["RELIANCE"].provider_key
    early = parse_candles(ReplayProvider(scenario, step=3).get_intraday_candles(key, "15minute"))
    late = parse_candles(ReplayProvider(scenario, step=15).get_intraday_candles(key, "15minute"))
    assert len(late) > len(early)
