"""Watchlist management.

The shape is: user -> many watchlists -> many instruments, where the same
instrument may sit in several watchlists with genuinely independent membership
settings. Ordering, priority, star state and notes live on the *membership*
(``WatchlistItem``), not on the instrument, so starring RELIANCE in "Core
holdings" does not star it in "Watching".

Every function takes a ``user`` and filters by it. There is no code path that
reaches a watchlist without an owner check.
"""

from __future__ import annotations

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Instrument, User, Watchlist, WatchlistItem
from app.services import catalog

DEFAULT_WATCHLIST_NAME = "My Watchlist"
DEMO_WATCHLIST_NAME = "Demo Watchlist"


class WatchlistError(ValueError):
    """A watchlist operation was rejected."""


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_watchlists(db: Session, user: User) -> list[Watchlist]:
    return list(
        db.scalars(
            select(Watchlist)
            .where(Watchlist.user_id == user.id)
            .options(selectinload(Watchlist.items).selectinload(WatchlistItem.instrument))
            .order_by(Watchlist.position, Watchlist.id)
        ).all()
    )


def get_watchlist(db: Session, user: User, watchlist_id: int) -> Watchlist:
    """Fetch one watchlist, enforcing ownership."""
    watchlist = db.scalar(
        select(Watchlist)
        .where(Watchlist.id == watchlist_id, Watchlist.user_id == user.id)
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.instrument))
    )
    if watchlist is None:
        raise WatchlistError("Watchlist not found.")
    return watchlist


def default_watchlist(db: Session, user: User) -> Watchlist | None:
    return db.scalar(
        select(Watchlist)
        .where(Watchlist.user_id == user.id, Watchlist.is_default.is_(True))
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.instrument))
    )


def watched_symbols(db: Session, user: User) -> list[str]:
    """Every distinct symbol this user watches, across all their watchlists.

    This is the polling set: distinct instruments, so an instrument in three
    watchlists is fetched and scored once.
    """
    rows = db.scalars(
        select(Instrument.symbol)
        .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .where(Watchlist.user_id == user.id)
        .distinct()
    ).all()
    return sorted(rows)


def membership_settings(db: Session, user: User) -> dict[str, dict]:
    """Per-symbol star/priority, merged across watchlists.

    A stock starred in any watchlist counts as starred for ranking purposes,
    and the highest priority wins - the user's strongest expressed interest is
    the one that should influence attention.
    """
    # Boolean is cast to Integer because PostgreSQL has no max(boolean).
    rows = db.execute(
        select(
            Instrument.symbol,
            func.max(WatchlistItem.priority),
            func.max(cast(WatchlistItem.starred, Integer)),
        )
        .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
        .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .where(Watchlist.user_id == user.id)
        .group_by(Instrument.symbol)
    ).all()
    return {
        symbol: {"priority": int(priority or 0), "starred": bool(starred)}
        for symbol, priority, starred in rows
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create_watchlist(
    db: Session, user: User, name: str, is_default: bool = False
) -> Watchlist:
    name = (name or "").strip()
    if not name:
        raise WatchlistError("Watchlist name cannot be empty.")

    existing = db.scalar(
        select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.name == name)
    )
    if existing is not None:
        raise WatchlistError(f"A watchlist named '{name}' already exists.")

    position = db.scalar(
        select(func.coalesce(func.max(Watchlist.position), -1)).where(
            Watchlist.user_id == user.id
        )
    )
    if is_default:
        for other in db.scalars(
            select(Watchlist).where(Watchlist.user_id == user.id)
        ).all():
            other.is_default = False

    watchlist = Watchlist(
        user_id=user.id, name=name, is_default=is_default, position=int(position) + 1
    )
    db.add(watchlist)
    db.flush()
    return watchlist


def rename_watchlist(db: Session, user: User, watchlist_id: int, name: str) -> Watchlist:
    watchlist = get_watchlist(db, user, watchlist_id)
    name = (name or "").strip()
    if not name:
        raise WatchlistError("Watchlist name cannot be empty.")
    watchlist.name = name
    db.flush()
    return watchlist


def delete_watchlist(db: Session, user: User, watchlist_id: int) -> None:
    watchlist = get_watchlist(db, user, watchlist_id)
    db.delete(watchlist)
    db.flush()


def add_instrument(
    db: Session,
    user: User,
    watchlist_id: int,
    symbol: str,
    priority: int = 0,
    starred: bool = False,
    notes: str | None = None,
) -> WatchlistItem:
    """Add an instrument, enforcing the supported-instrument boundary."""
    watchlist = get_watchlist(db, user, watchlist_id)
    instrument = catalog.get_by_symbol(db, symbol)
    if instrument is None:
        raise WatchlistError(f"Unknown instrument '{symbol}'.")
    try:
        catalog.assert_supported(instrument)
    except ValueError as exc:
        raise WatchlistError(str(exc)) from exc

    existing = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.instrument_id == instrument.id,
        )
    )
    if existing is not None:
        return existing

    position = db.scalar(
        select(func.coalesce(func.max(WatchlistItem.position), -1)).where(
            WatchlistItem.watchlist_id == watchlist.id
        )
    )
    item = WatchlistItem(
        watchlist_id=watchlist.id,
        instrument_id=instrument.id,
        position=int(position) + 1,
        priority=priority,
        starred=starred,
        notes=notes,
    )
    db.add(item)
    db.flush()
    return item


def remove_instrument(db: Session, user: User, watchlist_id: int, symbol: str) -> None:
    watchlist = get_watchlist(db, user, watchlist_id)
    instrument = catalog.get_by_symbol(db, symbol)
    if instrument is None:
        raise WatchlistError(f"Unknown instrument '{symbol}'.")

    item = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.instrument_id == instrument.id,
        )
    )
    if item is None:
        raise WatchlistError(f"{symbol} is not in this watchlist.")
    db.delete(item)
    db.flush()


def update_item(
    db: Session,
    user: User,
    watchlist_id: int,
    symbol: str,
    priority: int | None = None,
    starred: bool | None = None,
    notes: str | None = None,
    position: int | None = None,
) -> WatchlistItem:
    """Update membership settings. Only affects this watchlist's membership."""
    watchlist = get_watchlist(db, user, watchlist_id)
    instrument = catalog.get_by_symbol(db, symbol)
    if instrument is None:
        raise WatchlistError(f"Unknown instrument '{symbol}'.")

    item = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.instrument_id == instrument.id,
        )
    )
    if item is None:
        raise WatchlistError(f"{symbol} is not in this watchlist.")

    if priority is not None:
        item.priority = max(0, min(3, priority))
    if starred is not None:
        item.starred = starred
    if notes is not None:
        item.notes = notes
    if position is not None:
        item.position = position
    db.flush()
    return item


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


def ensure_starter_watchlist(
    db: Session, user: User, symbols: list[str] | None = None
) -> Watchlist:
    """Guarantee the user has at least one watchlist.

    A user with nothing to watch has nothing to be told about, so first-use
    creates a default list rather than showing an empty dashboard.
    """
    existing = list_watchlists(db, user)
    if existing:
        return next((w for w in existing if w.is_default), existing[0])

    watchlist = create_watchlist(db, user, DEFAULT_WATCHLIST_NAME, is_default=True)
    for symbol in symbols or []:
        try:
            add_instrument(db, user, watchlist.id, symbol)
        except WatchlistError:
            continue
    db.flush()
    return watchlist


def create_demo_watchlist(db: Session, user: User) -> Watchlist:
    """The curated demo list, used by guest 'Try LUMEN' sessions."""
    from app.replay.universe import DEMO_WATCHLIST

    existing = db.scalar(
        select(Watchlist).where(
            Watchlist.user_id == user.id, Watchlist.name == DEMO_WATCHLIST_NAME
        )
    )
    if existing is not None:
        return existing

    watchlist = create_watchlist(db, user, DEMO_WATCHLIST_NAME, is_default=True)
    for symbol in DEMO_WATCHLIST:
        try:
            add_instrument(db, user, watchlist.id, symbol)
        except WatchlistError:
            continue
    return watchlist
