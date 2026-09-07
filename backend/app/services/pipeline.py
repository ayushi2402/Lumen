"""The scoring cycle: provider payloads in, scored observations out.

This is the seam that joins the product layer to the intelligence engine. It
does four things and nothing else:

1. Fetch one batched quote payload from whichever provider is active.
2. Normalize it and assemble each instrument's ``ObservationContext``.
3. Hand every observation to the **unmodified** engine for scoring.
4. Classify each result as stock-specific, sector-wide or market-wide.

It never recomputes, adjusts or second-guesses a score. Personalization enters
only through the engine's own ``behavioral_relevance`` parameter, which is
already capped inside the engine.

Work is O(instruments), not O(users x instruments): the union of every
watchlisted symbol is polled once and scored once, and per-user personalization
is applied on top of shared observations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from app.intelligence.config import DEFAULT_CONFIG, IntelligenceConfig
from app.intelligence.models import AttentionResult, MarketObservation
from app.intelligence.scoring import score_observation
from app.market.calendar import SESSION_MINUTES
from app.market.calendar import minute_of_session as calendar_minute_of_session
from app.market.context import MarketContext, classify_move, compute_market_context
from app.market.freshness import FreshnessReport, classify as classify_freshness
from app.market.normalization import (
    ObservationContext,
    QuoteSnapshot,
    news_evidence_from,
    parse_candles,
    parse_quotes,
    realized_volatility,
    to_observation,
)
from app.providers.base import MarketDataProvider, ProviderError
from app.replay.universe import InstrumentBaseline


@dataclass(frozen=True)
class ScoredObservation:
    """One instrument's complete verdict for one cycle."""

    symbol: str
    observation: MarketObservation
    result: AttentionResult
    classification: str
    freshness: FreshnessReport
    quote: QuoteSnapshot


@dataclass(frozen=True)
class CycleResult:
    """Everything one polling cycle produced."""

    observed_at: datetime
    provider: str
    is_replay: bool
    market: MarketContext
    scored: list[ScoredObservation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def by_symbol(self) -> dict[str, ScoredObservation]:
        return {s.symbol: s for s in self.scored}

    def meaningful(self) -> list[ScoredObservation]:
        """Only results that cleared the engine's meaningfulness gate."""
        return [s for s in self.scored if s.result.meaningful]

    def ranked(self) -> list[ScoredObservation]:
        """Meaningful results, most attention-worthy first."""
        return sorted(self.meaningful(), key=lambda s: s.result.final_score, reverse=True)


def _provider_benchmark(provider: MarketDataProvider) -> float | None:
    """Benchmark return, when the provider can supply one directly."""
    getter = getattr(provider, "benchmark_return", None)
    return getter() if callable(getter) else None


def _provider_sector(provider: MarketDataProvider, sector: str | None) -> float | None:
    getter = getattr(provider, "sector_return", None)
    if not callable(getter) or sector is None:
        return None
    return getter(sector)


def _provider_news(provider: MarketDataProvider, symbol: str) -> dict | None:
    getter = getattr(provider, "news_for", None)
    return getter(symbol) if callable(getter) else None


def _intraday_volatility(
    provider: MarketDataProvider, baseline: InstrumentBaseline
) -> tuple[float | None, int]:
    """Realized intraday volatility for one instrument.

    Failures are swallowed into an explicit "no data" rather than raised: a
    missing intraday series must degrade the volatility signal to unavailable,
    never abort the whole cycle.
    """
    try:
        raw = provider.get_intraday_candles(baseline.provider_key, "15minute")
        return realized_volatility(parse_candles(raw), SESSION_MINUTES)
    except (ProviderError, ValueError, KeyError):
        return None, 0


def build_context(
    provider: MarketDataProvider,
    baseline: InstrumentBaseline | object,
    minute_of_session: int,
    include_intraday: bool = True,
) -> ObservationContext:
    """Assemble the non-quote inputs the engine needs for one instrument.

    The same-time-of-day volume baseline is preferred and its sample count is
    passed through honestly, so the engine - not this layer - decides whether
    it is usable or whether to fall back and say so.
    """
    from app.services.baselines import same_time_sample_days

    intraday_vol, intraday_count = (
        _intraday_volatility(provider, baseline) if include_intraday else (None, 0)
    )
    return ObservationContext(
        typical_daily_move=baseline.typical_daily_move,
        typical_move_sample_days=baseline.typical_move_sample_days,
        historical_volatility=baseline.historical_volatility,
        volatility_sample_days=baseline.volatility_sample_days,
        average_volume=baseline.average_volume,
        same_time_of_day_volume=baseline.same_time_volume(minute_of_session, SESSION_MINUTES),
        # Reported from the baseline itself rather than assumed. A stored
        # baseline with no measured bucket returns 0, so the engine falls back
        # to the full-day average and declares it.
        same_time_of_day_sample_days=same_time_sample_days(baseline, minute_of_session),
        intraday_volatility=intraday_vol,
        intraday_observation_count=intraday_count,
        historical_gap_stdev=baseline.historical_gap_stdev,
        benchmark_return=_provider_benchmark(provider),
        sector_return=_provider_sector(provider, baseline.sector),
        news=news_evidence_from(_provider_news(provider, baseline.symbol)),
    )


def run_cycle(
    provider: MarketDataProvider,
    baselines: dict[str, InstrumentBaseline],
    behavioral_relevance: dict[str, float] | None = None,
    include_intraday: bool = True,
    config: IntelligenceConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> CycleResult:
    """Poll, normalize, score and classify one batch of instruments.

    ``behavioral_relevance`` is per-symbol and per-user, in 0-100. It is passed
    straight to the engine, which caps its influence; nothing here can use it
    to change objective significance.
    """
    behavioral_relevance = behavioral_relevance or {}
    symbols = sorted(baselines)
    keys = [baselines[s].provider_key for s in symbols]

    errors: list[str] = []
    try:
        raw = provider.get_quotes(keys)
        snapshots = parse_quotes(raw)
    except (ProviderError, ValueError) as exc:
        # A failed batch is reported, not raised: the caller decides whether to
        # fall back to another provider or serve last-known data as stale.
        return CycleResult(
            observed_at=now or _utcnow(),
            provider=getattr(provider, "name", "unknown"),
            is_replay=getattr(provider, "is_replay", False),
            market=compute_market_context({}, None),
            errors=[f"quote fetch failed: {exc}"],
        )

    observed_at = raw.as_of
    # Replay reports its own virtual minute-of-session. For live providers it
    # is derived from the observation's own timestamp, so a midday quote is
    # compared against midday volume rather than a whole session's worth.
    provider_minute = getattr(provider, "minute_of_session", None)
    if callable(provider_minute):
        minute = provider_minute()
    else:
        minute = calendar_minute_of_session(observed_at)
        if minute is None:
            # Outside a session the full-day baseline is the right comparison.
            minute = SESSION_MINUTES

    # Breadth is computed across everything observed, before any per-instrument
    # scoring, so each event can be judged against the market it happened in.
    returns = {
        symbol: snap.daily_return
        for symbol, snap in snapshots.items()
        if snap.daily_return is not None
    }
    market = compute_market_context(returns, _provider_benchmark(provider))

    scored: list[ScoredObservation] = []
    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            errors.append(f"{symbol}: no quote in provider response")
            continue

        baseline = baselines[symbol]
        try:
            context = build_context(provider, baseline, minute, include_intraday)
            observation = to_observation(snapshot, context)
            result = score_observation(
                observation,
                behavioral_relevance=behavioral_relevance.get(symbol, 0.0),
                config=config,
            )
        except (ValueError, KeyError) as exc:
            # One bad instrument must never take down the cycle.
            errors.append(f"{symbol}: scoring skipped ({exc})")
            continue

        scored.append(
            ScoredObservation(
                symbol=symbol,
                observation=observation,
                result=result,
                classification=classify_move(result, market),
                freshness=classify_freshness(
                    snapshot.as_of, observed_at, snapshot.provider
                ),
                quote=snapshot,
            )
        )

    return CycleResult(
        observed_at=observed_at,
        provider=raw.provider,
        is_replay=raw.is_replay,
        market=market,
        scored=scored,
        errors=errors,
    )
