"""Watchlist routes.

Every route resolves the user from the session token and scopes its queries to
that user. There is no path where a watchlist id alone grants access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import User, Watchlist
from app.providers.registry import DataMode, select_provider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.schemas.api import (
    WatchlistAddIn,
    WatchlistCreateIn,
    WatchlistItemUpdateIn,
    WatchlistOut,
    WatchlistRenameIn,
)
from app.services import behavior, dashboard as dashboard_service
from app.services import events as events_service
from app.services import serializers
from app.services import watchlists as watchlists_service
from app.services.pipeline import run_cycle

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _serialize(watchlist: Watchlist, enriched: dict | None = None) -> dict:
    enriched = enriched or {}
    items = []
    for item in sorted(watchlist.items, key=lambda i: (i.position, i.id)):
        symbol = item.instrument.symbol
        extra = enriched.get(symbol, {})
        items.append(
            {
                "symbol": symbol,
                "name": item.instrument.name,
                "sector": item.instrument.sector,
                "position": item.position,
                "priority": item.priority,
                "starred": item.starred,
                "notes": item.notes,
                **extra,
            }
        )
    return {
        "id": watchlist.id,
        "name": watchlist.name,
        "is_default": watchlist.is_default,
        "position": watchlist.position,
        "item_count": len(items),
        "items": items,
    }


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> list[dict]:
    return [_serialize(w) for w in watchlists_service.list_watchlists(db, user)]


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    body: WatchlistCreateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    try:
        watchlist = watchlists_service.create_watchlist(db, user, body.name, body.is_default)
    except watchlists_service.WatchlistError as exc:
        raise _error(exc) from exc
    db.commit()
    return _serialize(watchlists_service.get_watchlist(db, user, watchlist.id))


@router.get("/{watchlist_id}", response_model=WatchlistOut)
def get_watchlist(
    watchlist_id: int,
    mode: DataMode = Query(default=DataMode.LIVE),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=0, ge=0, le=25),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """One watchlist, ranked by attention with each stock's latest reason.

    The primary view is attention order, not alphabetical: the point of the
    product is that the list tells you where to look.
    """
    try:
        watchlist = watchlists_service.get_watchlist(db, user, watchlist_id)
    except watchlists_service.WatchlistError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    symbols = [item.instrument.symbol for item in watchlist.items]
    enriched = _enrich(db, user, symbols, mode, scenario, step)
    payload = _serialize(watchlist, enriched)
    # Attention order first, then the user's own ordering as a tiebreak.
    payload["items"].sort(
        key=lambda i: (-(i.get("score") or 0.0), i.get("position", 0))
    )
    db.commit()
    return payload


def _enrich(
    db: Session,
    user: User,
    symbols: list[str],
    mode: DataMode,
    scenario: str,
    step: int,
) -> dict[str, dict]:
    """Attach price, score, latest reason and a sparkline to each row."""
    if not symbols:
        return {}

    selection = select_provider(mode, scenario, step)
    baselines = dashboard_service.baselines_for(symbols, db)
    scope = "live" if mode is DataMode.LIVE else f"replay:{scenario}"
    muted = behavior.muted_symbols(db, user)

    enriched: dict[str, dict] = {}
    if baselines:
        cycle = run_cycle(selection.provider, baselines, include_intraday=False)
        for scored in cycle.scored:
            enriched[scored.symbol] = {
                "price": serializers.price_payload(scored.quote),
                "score": round(scored.result.final_score, 2),
                "severity": scored.result.severity.value,
                "muted": scored.symbol in muted,
            }

    rows = events_service.events_for_symbols(db, symbols, scope=scope, limit=100)
    for row in rows:
        entry = enriched.setdefault(row.instrument.symbol, {})
        if "latest_reason" not in entry:
            entry["latest_reason"] = serializers.primary_reason(row)
            entry["event_id"] = row.id
            entry["sparkline"] = [
                p.price for p in row.timeline if p.price is not None
            ][-30:]
    return enriched


@router.patch("/{watchlist_id}", response_model=WatchlistOut)
def rename_watchlist(
    watchlist_id: int,
    body: WatchlistRenameIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    try:
        watchlists_service.rename_watchlist(db, user, watchlist_id, body.name)
    except watchlists_service.WatchlistError as exc:
        raise _error(exc) from exc
    db.commit()
    return _serialize(watchlists_service.get_watchlist(db, user, watchlist_id))


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(
    watchlist_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> None:
    try:
        watchlists_service.delete_watchlist(db, user, watchlist_id)
    except watchlists_service.WatchlistError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()


@router.post("/{watchlist_id}/items", response_model=WatchlistOut)
def add_item(
    watchlist_id: int,
    body: WatchlistAddIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Add an instrument. Unsupported instruments are refused, not faked."""
    try:
        watchlists_service.add_instrument(
            db, user, watchlist_id, body.symbol, body.priority, body.starred, body.notes
        )
    except watchlists_service.WatchlistError as exc:
        raise _error(exc) from exc
    db.commit()
    return _serialize(watchlists_service.get_watchlist(db, user, watchlist_id))


@router.patch("/{watchlist_id}/items/{symbol}", response_model=WatchlistOut)
def update_item(
    watchlist_id: int,
    symbol: str,
    body: WatchlistItemUpdateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Update membership settings for this watchlist only.

    Starring here does not star the same stock in another watchlist -
    memberships are independent.
    """
    try:
        watchlists_service.update_item(
            db, user, watchlist_id, symbol,
            priority=body.priority, starred=body.starred,
            notes=body.notes, position=body.position,
        )
    except watchlists_service.WatchlistError as exc:
        raise _error(exc) from exc
    db.commit()
    return _serialize(watchlists_service.get_watchlist(db, user, watchlist_id))


@router.delete("/{watchlist_id}/items/{symbol}", response_model=WatchlistOut)
def remove_item(
    watchlist_id: int,
    symbol: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    try:
        watchlists_service.remove_instrument(db, user, watchlist_id, symbol)
    except watchlists_service.WatchlistError as exc:
        raise _error(exc) from exc
    db.commit()
    return _serialize(watchlists_service.get_watchlist(db, user, watchlist_id))
