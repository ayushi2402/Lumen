"""Instrument catalogue: seeding, lookup and search.

The supported-instrument boundary is explicit and enforced here. Search may
match broadly, but ``is_supported`` decides what can actually be added to a
watchlist. That is the honest alternative to a fake universal NSE search that
accepts any symbol and then has no data for it: a user is told plainly that an
instrument is not supported yet, rather than adding a row that will never
produce intelligence.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Instrument
from app.replay.universe import BENCHMARK_NAME, BENCHMARK_SYMBOL, UNIVERSE


def seed_instruments(db: Session) -> int:
    """Insert any universe instruments not already present. Idempotent.

    Safe to call on every startup: existing rows are updated in place rather
    than duplicated, so re-seeding never disturbs watchlist references.
    """
    existing = {row.symbol: row for row in db.scalars(select(Instrument)).all()}
    created = 0

    for baseline in UNIVERSE.values():
        row = existing.get(baseline.symbol)
        if row is None:
            db.add(
                Instrument(
                    symbol=baseline.symbol,
                    name=baseline.name,
                    exchange="NSE",
                    sector=baseline.sector,
                    provider_key=baseline.provider_key,
                    is_supported=True,
                    is_active=True,
                )
            )
            created += 1
        else:
            row.name = baseline.name
            row.sector = baseline.sector
            row.provider_key = baseline.provider_key
            row.is_supported = True

    if BENCHMARK_SYMBOL not in existing:
        db.add(
            Instrument(
                symbol=BENCHMARK_SYMBOL,
                name=BENCHMARK_NAME,
                exchange="NSE",
                sector="Index",
                provider_key=f"NSE_INDEX|{BENCHMARK_SYMBOL}",
                # The benchmark is context, not something a user watches.
                is_supported=False,
                is_benchmark=True,
            )
        )
        created += 1

    db.flush()
    return created


def get_by_symbol(db: Session, symbol: str) -> Instrument | None:
    return db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))


def get_many(db: Session, symbols: list[str]) -> dict[str, Instrument]:
    if not symbols:
        return {}
    rows = db.scalars(
        select(Instrument).where(Instrument.symbol.in_([s.upper() for s in symbols]))
    ).all()
    return {row.symbol: row for row in rows}


def search(
    db: Session, query: str, limit: int = 20, supported_only: bool = False
) -> list[Instrument]:
    """Search by symbol or company name.

    Results carry ``is_supported`` so the UI can show an unsupported match
    without offering to add it. Exact symbol matches sort first, because
    someone typing "INFY" wants Infosys, not the first alphabetical match.
    """
    term = (query or "").strip()
    if not term:
        return []

    pattern = f"%{term.lower()}%"
    statement = select(Instrument).where(
        Instrument.is_active.is_(True),
        or_(
            func.lower(Instrument.symbol).like(pattern),
            func.lower(Instrument.name).like(pattern),
        ),
    )
    if supported_only:
        statement = statement.where(Instrument.is_supported.is_(True))

    rows = db.scalars(statement.limit(limit * 3)).all()

    upper = term.upper()
    lower = term.lower()

    def rank(row: Instrument) -> tuple[int, str]:
        if row.symbol == upper:
            return (0, row.symbol)
        if row.symbol.startswith(upper):
            return (1, row.symbol)
        if row.name.lower().startswith(lower):
            return (2, row.name)
        return (3, row.name)

    return sorted(rows, key=rank)[:limit]


def recommended_starter_instruments(db: Session, limit: int = 12) -> list[Instrument]:
    """Liquid, sector-spread suggestions for a user with no watchlist."""
    from app.replay.universe import DEMO_WATCHLIST

    found = get_many(db, list(DEMO_WATCHLIST))
    ordered = [found[s] for s in DEMO_WATCHLIST if s in found]
    return ordered[:limit]


def assert_supported(instrument: Instrument) -> None:
    """Raise if an instrument cannot be added to a watchlist."""
    if not instrument.is_supported or not instrument.is_active:
        raise ValueError(
            f"{instrument.symbol} is not supported by LUMEN yet. "
            "Supported instruments are those with the baselines the "
            "intelligence engine requires."
        )
