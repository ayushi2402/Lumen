"""Dashboard assembly: one call, everything the homepage renders.

This module runs a scoring cycle, persists what it produced, and composes the
result into a single payload. The frontend gets ranked attention cards, the
digest, the event feed, market context, freshness and notification state
already resolved - it never re-derives significance or ranking.

Ranking rule, in order:

1. The engine's ``final_score`` (which already includes its own capped
   personalization adjustment).
2. Muted stocks removed from the attention surface entirely.
3. Dismissed events removed from the homepage but kept in history.

There is no additional re-ranking pass here. Anything that would change the
order has to go through the engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Instrument, MarketEventRow, MarketObservationRow, User, utcnow
from app.market.calendar import session_date_for
from app.market.context import compute_market_context
from app.providers.registry import DataMode, ProviderSelection, select_provider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.replay.universe import UNIVERSE
from app.services import behavior, catalog, digest as digest_service
from app.services import events as events_service
from app.services import notifications as notifications_service
from app.services import serializers
from app.services.pipeline import CycleResult, ScoredObservation, run_cycle

ATTENTION_LIMIT = 3
FEED_LIMIT = 25


def baselines_for(symbols: list[str], db: Session | None = None) -> dict[str, Any]:
    """Baselines for the requested symbols.

    Prefers real history stored by the seed process; falls back to the
    synthetic universe for anything not yet seeded, so the product works
    before a seed has ever run. Both sources expose the same interface.
    """
    if db is not None:
        from app.services.baselines import load_baselines

        stored = load_baselines(db, symbols)
        if stored:
            return stored
    return {s: UNIVERSE[s] for s in symbols if s in UNIVERSE}


def run_user_cycle(
    db: Session,
    user: User,
    symbols: list[str],
    selection: ProviderSelection,
    scope: str = "live",
    persist: bool = True,
    settings: Settings | None = None,
) -> CycleResult:
    """Score one user's watchlist and persist the outcome.

    Personalization enters only as the engine's ``behavioral_relevance``
    input, which the engine itself caps.
    """
    settings = settings or get_settings()
    from app.services import watchlists as watchlists_service

    membership = watchlists_service.membership_settings(db, user)
    relevance = behavior.behavioral_relevance(db, user, membership)
    baselines = baselines_for(symbols, db)
    if not baselines:
        return CycleResult(
            observed_at=utcnow(),
            provider=selection.provider.name,
            is_replay=selection.is_replay,
            market=compute_market_context({}, None),
            errors=["No supported instruments in this watchlist."],
        )

    cycle = run_cycle(selection.provider, baselines, behavioral_relevance=relevance)

    if persist:
        instruments = catalog.get_many(db, list(baselines))
        for scored in cycle.scored:
            instrument = instruments.get(scored.symbol)
            if instrument is None:
                continue
            _persist_observation(db, scored, instrument, scope, selection.is_replay)
            row, _ = events_service.ingest_scored(
                db, scored, instrument, scope=scope, is_replay=selection.is_replay
            )
            if row is not None and not selection.is_replay:
                notifications_service.create_for_event(db, user, row, settings)
        events_service.expire_stale(db, scope=scope, now=cycle.observed_at)
        db.flush()

    return cycle


def _persist_observation(
    db: Session,
    scored: ScoredObservation,
    instrument: Instrument,
    scope: str,
    is_replay: bool,
) -> MarketObservationRow:
    """Store the engine's input, not the provider's payload.

    Keeping the normalized observation is what allows a past digest to be
    reconstructed exactly, and keeps vendor payloads out of the database.
    """
    observation = scored.observation
    row = MarketObservationRow(
        instrument_id=instrument.id,
        observed_at=observation.timestamp,
        session_date=session_date_for(observation.timestamp),
        scope=scope,
        source=scored.quote.provider,
        freshness=scored.freshness.state.value,
        is_replay=is_replay,
        price=observation.price,
        previous_price=observation.previous_price,
        daily_return=observation.daily_return,
        volume=observation.volume,
        gap_percent=observation.gap_percent,
        benchmark_return=observation.benchmark_return,
        sector_return=observation.sector_return,
        intraday_volatility=observation.intraday_volatility,
        payload=observation.model_dump(mode="json"),
    )
    db.add(row)
    return row


def attention_cards(
    cycle: CycleResult,
    event_rows: dict[str, MarketEventRow],
    muted: set[str],
    limit: int = ATTENTION_LIMIT,
) -> list[dict[str, Any]]:
    """The "3 stocks deserve your attention" cards.

    Muted stocks are excluded outright - a mute means the user asked not to
    have this dominate their attention, and honouring that only partially
    would be worse than not offering mute at all.
    """
    ranked = [s for s in cycle.ranked() if s.symbol not in muted]
    cards: list[dict[str, Any]] = []

    for rank, scored in enumerate(ranked[:limit], start=1):
        row = event_rows.get(scored.symbol)
        result = scored.result
        cards.append(
            {
                "rank": rank,
                "symbol": scored.symbol,
                "name": UNIVERSE[scored.symbol].name if scored.symbol in UNIVERSE else scored.symbol,
                "price": serializers.price_payload(scored.quote),
                "score": round(result.final_score, 2),
                "severity": result.severity.value,
                "direction": result.direction.value,
                "confidence": result.confidence.value,
                "primary_reason": (
                    serializers.primary_reason(row) if row else (result.evidence or [""])[0]
                ),
                "why_it_matters": (
                    serializers.why_it_matters(row)
                    if row
                    else "This move was specific to the stock rather than the market."
                ),
                "event_id": row.id if row else None,
                "classification": scored.classification,
            }
        )
    return cards


def attention_headline(cards: list[dict[str, Any]], market_wide: bool) -> str:
    """Generated from what actually happened, never hardcoded."""
    if not cards:
        if market_wide:
            return (
                "Nothing stock-specific needs your attention - today's moves "
                "tracked the broader market."
            )
        return "Nothing needs your attention right now."
    if len(cards) == 1:
        return "1 stock deserves your attention"
    return f"{len(cards)} stocks deserve your attention"


def build_dashboard(
    db: Session,
    user: User,
    mode: DataMode = DataMode.LIVE,
    scenario_key: str = DEFAULT_SCENARIO_KEY,
    step: int = 0,
    scope: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose the full dashboard payload."""
    settings = settings or get_settings()
    now = now or utcnow()
    from app.services import watchlists as watchlists_service

    selection = select_provider(mode, scenario_key, step, settings)
    scope = scope or ("live" if mode is DataMode.LIVE else f"replay:{scenario_key}")

    symbols = watchlists_service.watched_symbols(db, user)
    warnings: list[str] = []
    if not symbols:
        warnings.append("No stocks are being watched yet.")

    cycle = run_user_cycle(db, user, symbols, selection, scope=scope, settings=settings)
    warnings.extend(cycle.errors)
    if selection.degraded:
        warnings.append(selection.reason)

    muted = behavior.muted_symbols(db, user, now)

    # Scenario details only exist when a replay provider is actually in play -
    # the live chain has no scenario, so this is read defensively rather than
    # assumed from the is_replay flag alone.
    scenario = getattr(selection.provider, "scenario", None)

    # Events touched by this cycle, keyed by symbol for card assembly.
    rows = events_service.events_for_symbols(db, symbols, scope=scope, limit=FEED_LIMIT * 2)
    dismissed = events_service.dismissed_event_ids(db, user)
    states = events_service.states_for(db, user, [r.id for r in rows])

    latest_by_symbol: dict[str, MarketEventRow] = {}
    for row in rows:
        latest_by_symbol.setdefault(row.instrument.symbol, row)

    cards = attention_cards(cycle, latest_by_symbol, muted)

    feed_rows = [r for r in rows if r.id not in dismissed and r.instrument.symbol not in muted]
    # Ranked by significance, then chronological within a tier - which is what
    # "ranked by significance, chronological within tiers" means in practice.
    feed_rows.sort(key=lambda r: (-_severity_rank(r.severity), -r.last_updated_at.timestamp()))

    # In replay, both the baseline and "now" come from the virtual clock, so
    # the digest describes the scenario rather than the real calendar.
    if selection.is_replay and scenario is not None:
        replay_start = selection.provider.virtual_time(0)
        digest = digest_service.build_digest(
            db, user, symbols, scope=scope, now=cycle.observed_at,
            baseline_override=digest_service.replay_baseline(replay_start),
        )
    else:
        digest = digest_service.build_digest(db, user, symbols, scope=scope, now=now)

    freshness = None
    if cycle.scored:
        freshness = cycle.scored[0].freshness.to_dict()

    data_mode = selection.to_dict()
    data_mode.update(
        {
            "scenario": scenario_key if selection.is_replay else None,
            "step": step if selection.is_replay else None,
            "total_steps": scenario.total_steps if scenario is not None else None,
            "virtual_time": cycle.observed_at if selection.is_replay else None,
        }
    )

    return {
        "generated_at": now,
        "data_mode": data_mode,
        "freshness": freshness,
        "attention": cards,
        "attention_headline": attention_headline(cards, cycle.market.is_market_wide_move),
        "since_you_were_away": _digest_payload(digest, states),
        "event_feed": [
            serializers.event_summary(r, states.get(r.id)) for r in feed_rows[:FEED_LIMIT]
        ],
        "market": cycle.market.to_dict(),
        "notifications": {
            "unread_count": notifications_service.unread_count(db, user),
            "pending_delivery": [
                {
                    "id": n.id,
                    "title": n.title,
                    "body": n.body,
                    "severity": n.severity,
                    "event_id": n.event_id,
                }
                for n in notifications_service.pending_delivery(db, user)
            ],
        },
        "watchlist_count": len(watchlists_service.list_watchlists(db, user)),
        "tracked_symbols": len(symbols),
        "muted_symbols": sorted(muted),
        "warnings": warnings,
    }


_SEVERITY_ORDER = {"Critical": 3, "High Attention": 2, "Worth Watching": 1, "Noise": 0}


def _severity_rank(severity: str) -> int:
    return _SEVERITY_ORDER.get(severity, 0)


def _digest_payload(digest: digest_service.Digest, states: dict) -> dict[str, Any]:
    return {
        "baseline_at": digest.baseline.at,
        "baseline_source": digest.baseline.source,
        "baseline_label": digest.baseline.label,
        "summary": digest.summary,
        "sessions_missed": digest.sessions_missed,
        "is_multi_session": digest.is_multi_session,
        "top_events": [
            serializers.event_summary(r, states.get(r.id)) for r in digest.top_events
        ],
        "remainder_count": digest.remainder_count,
        "days": [
            {
                "session_date": day.session_date.isoformat(),
                "headline": day.headline,
                "top_symbols": day.top_symbols,
                "event_count": len(day.events),
                "events": [
                    serializers.event_summary(r, states.get(r.id)) for r in day.events
                ],
            }
            for day in digest.days
        ],
    }
