"""Stock routes: search and detail.

Search stays deliberately thin - name, ticker, price - and intelligence only
appears once a stock is opened. Loading full event intelligence into every
search row would be slow and would bury the one thing a searching user wants.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import User
from app.providers.registry import DataMode, select_provider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.replay.universe import UNIVERSE
from app.schemas.api import SearchResultOut, StockDetailOut
from app.services import behavior, catalog, dashboard as dashboard_service
from app.services import events as events_service
from app.services import serializers
from app.services import watchlists as watchlists_service
from app.services.pipeline import run_cycle

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search", response_model=list[SearchResultOut])
def search_stocks(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=15, ge=1, le=50),
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=0, ge=0, le=25),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> list[dict]:
    """Search NSE instruments.

    Unsupported matches are still returned, flagged ``is_supported=False``, so
    the UI can show an honest "not supported yet" instead of pretending the
    instrument does not exist.
    """
    matches = catalog.search(db, q, limit=limit)
    symbols = [m.symbol for m in matches if m.symbol in UNIVERSE]

    prices: dict[str, dict] = {}
    baselines = dashboard_service.baselines_for(symbols, db)
    if baselines:
        selection = select_provider(mode, scenario, step)
        cycle = run_cycle(selection.provider, baselines, include_intraday=False)
        prices = {
            s.symbol: serializers.price_payload(s.quote) for s in cycle.scored
        }

    behavior.record_interaction(
        db, user, behavior.InteractionKind.SEARCH, context={"query": q}
    )
    db.commit()

    return [
        {
            "symbol": m.symbol,
            "name": m.name,
            "sector": m.sector,
            "price": prices.get(m.symbol, {}).get("price"),
            "change_percent": prices.get(m.symbol, {}).get("change_percent"),
            "is_supported": m.is_supported,
        }
        for m in matches
    ]


@router.get("/{symbol}", response_model=StockDetailOut)
def stock_detail(
    symbol: str,
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=0, ge=0, le=25),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Price, score, current event, timeline and market/sector comparison."""
    symbol = symbol.upper()
    instrument = catalog.get_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Unknown instrument '{symbol}'.")

    selection = select_provider(mode, scenario, step)
    scope = "live" if mode is DataMode.LIVE else f"replay:{scenario}"
    baselines = dashboard_service.baselines_for([symbol], db)
    if not baselines:
        raise HTTPException(
            status_code=400,
            detail=f"{symbol} has no baselines yet, so LUMEN cannot score it.",
        )

    cycle = run_cycle(selection.provider, baselines)
    scored = cycle.by_symbol().get(symbol)
    if scored is None:
        raise HTTPException(
            status_code=503,
            detail=f"No current market data available for {symbol}.",
        )

    result = scored.result
    rows = events_service.events_for_symbols(db, [symbol], scope=scope, limit=50)
    states = events_service.states_for(db, user, [r.id for r in rows])
    current = rows[0] if rows else None

    # The chart uses the current event's own timeline, so event markers and
    # the price line come from the same source and cannot disagree.
    chart = []
    if current is not None:
        chart = [
            {
                "timestamp": p.observed_at.isoformat(),
                "price": p.price,
                "score": p.score,
                "severity": p.severity,
            }
            for p in current.timeline
        ]

    behavior.record_interaction(
        db, user, behavior.InteractionKind.VIEW, instrument_id=instrument.id
    )
    db.commit()

    watchlist_names = [
        w.name
        for w in watchlists_service.list_watchlists(db, user)
        if any(i.instrument.symbol == symbol for i in w.items)
    ]
    observation = scored.observation
    benchmark = observation.benchmark_return
    sector = observation.sector_return

    return {
        "instrument": {
            "symbol": instrument.symbol,
            "name": instrument.name,
            "sector": instrument.sector,
            "exchange": instrument.exchange,
            "is_supported": instrument.is_supported,
        },
        "price": serializers.price_payload(scored.quote),
        "freshness": scored.freshness.to_dict(),
        "data_mode": selection.to_dict(),
        "score": round(result.final_score, 2),
        "severity": result.severity.value,
        "direction": result.direction.value,
        "confidence": result.confidence.value,
        "current_event": (
            serializers.event_summary(current, states.get(current.id)) if current else None
        ),
        "event_timeline": [
            serializers.event_summary(r, states.get(r.id)) for r in rows
        ],
        "chart": chart,
        "benchmark_return": benchmark,
        "sector_return": sector,
        "relative_to_benchmark": (
            round(observation.daily_return - benchmark, 2)
            if benchmark is not None and observation.daily_return is not None
            else None
        ),
        "relative_to_sector": (
            round(observation.daily_return - sector, 2)
            if sector is not None and observation.daily_return is not None
            else None
        ),
        "classification": scored.classification,
        "muted": symbol in behavior.muted_symbols(db, user),
        "in_watchlists": watchlist_names,
    }
