"""Behaviour tracking and personalization.

Two layers, kept separate on purpose:

* ``UserInteraction`` is an append-only log of what happened.
* ``UserInstrumentAffinity`` / ``UserSectorAffinity`` are the aggregates that
  ranking actually reads, so the hot path never scans the log.

The output of this module is a single number per symbol in 0-100:
``behavioral_relevance``. It is handed to the engine, which converts it into a
capped adjustment. **Nothing here touches a score directly.** That containment
is what makes "personalization cannot overpower objective significance" true by
construction rather than by careful coding.

Immediate signals count right away (one dismissal changes ranking now), but the
weights are arranged so a single interaction nudges and a consistent pattern
moves the needle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Instrument,
    StockMute,
    User,
    UserInstrumentAffinity,
    UserInteraction,
    UserSectorAffinity,
    utcnow,
)


class InteractionKind:
    """The behavioural vocabulary. Anything else is rejected."""

    VIEW = "view"
    CLICK = "click"
    WHY_OPENED = "why_opened"
    BREAKDOWN_OPENED = "breakdown_opened"
    DISMISS = "dismiss"
    ACKNOWLEDGE = "acknowledge"
    MORE_LIKE_THIS = "more_like_this"
    MUTE = "mute"
    UNMUTE = "unmute"
    SEARCH = "search"

    ALL = frozenset(
        {
            VIEW, CLICK, WHY_OPENED, BREAKDOWN_OPENED, DISMISS,
            ACKNOWLEDGE, MORE_LIKE_THIS, MUTE, UNMUTE, SEARCH,
        }
    )


@dataclass(frozen=True)
class RelevanceWeights:
    """Behavioural weights, centralized so they can be tuned in one place."""

    star: float = 35.0
    priority_step: float = 10.0
    per_view: float = 3.0
    view_cap: float = 20.0
    per_why: float = 6.0
    why_cap: float = 25.0
    per_more_like_this: float = 8.0
    more_like_this_cap: float = 16.0
    per_dismiss: float = 15.0
    dismiss_cap: float = 45.0
    per_mute: float = 12.0
    mute_cap: float = 24.0
    sector_bonus_cap: float = 10.0
    # Interest decays if the user stops engaging, so stale enthusiasm does not
    # keep promoting a stock forever.
    decay_after_days: int = 30
    decay_floor: float = 0.5


WEIGHTS = RelevanceWeights()

# A stock must be interacted with this many times before its affinity is
# treated as an established preference rather than a one-off.
ESTABLISHED_INTERACTION_COUNT = 4


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record_interaction(
    db: Session,
    user: User,
    kind: str,
    instrument_id: int | None = None,
    event_id: int | None = None,
    context: dict | None = None,
    occurred_at: datetime | None = None,
) -> UserInteraction:
    """Log one interaction and fold it into the aggregates."""
    if kind not in InteractionKind.ALL:
        raise ValueError(f"Unknown interaction kind '{kind}'.")

    interaction = UserInteraction(
        user_id=user.id,
        instrument_id=instrument_id,
        event_id=event_id,
        kind=kind,
        occurred_at=occurred_at or utcnow(),
        context=context,
    )
    db.add(interaction)

    if instrument_id is not None:
        _apply_to_affinity(db, user, instrument_id, kind, interaction.occurred_at)

    db.flush()
    return interaction


def _apply_to_affinity(
    db: Session, user: User, instrument_id: int, kind: str, moment: datetime
) -> UserInstrumentAffinity:
    affinity = db.scalar(
        select(UserInstrumentAffinity).where(
            UserInstrumentAffinity.user_id == user.id,
            UserInstrumentAffinity.instrument_id == instrument_id,
        )
    )
    if affinity is None:
        # Counters are initialized explicitly: SQLAlchemy column defaults are
        # applied at INSERT time, so a freshly constructed row would carry
        # None and the increments below would fail.
        affinity = UserInstrumentAffinity(
            user_id=user.id,
            instrument_id=instrument_id,
            view_count=0,
            why_count=0,
            dismiss_count=0,
            more_like_this_count=0,
            mute_count=0,
            relevance_score=0.0,
        )
        db.add(affinity)

    if kind in (InteractionKind.VIEW, InteractionKind.CLICK):
        affinity.view_count += 1
    elif kind in (InteractionKind.WHY_OPENED, InteractionKind.BREAKDOWN_OPENED):
        affinity.why_count += 1
    elif kind == InteractionKind.DISMISS:
        affinity.dismiss_count += 1
    elif kind == InteractionKind.MORE_LIKE_THIS:
        affinity.more_like_this_count += 1
    elif kind == InteractionKind.MUTE:
        # An explicit mute is recorded as a behavioural signal even though it
        # also suppresses display - the user told us something.
        affinity.mute_count += 1

    affinity.last_interaction_at = moment
    affinity.relevance_score = _raw_relevance(affinity)

    instrument = db.get(Instrument, instrument_id)
    if instrument is not None and instrument.sector:
        _apply_to_sector(db, user, instrument.sector, kind)

    return affinity


def _apply_to_sector(db: Session, user: User, sector: str, kind: str) -> None:
    row = db.scalar(
        select(UserSectorAffinity).where(
            UserSectorAffinity.user_id == user.id, UserSectorAffinity.sector == sector
        )
    )
    if row is None:
        row = UserSectorAffinity(
            user_id=user.id, sector=sector, interaction_count=0, relevance_score=0.0
        )
        db.add(row)

    if kind == InteractionKind.DISMISS:
        row.relevance_score = max(0.0, row.relevance_score - 1.0)
    elif kind in (
        InteractionKind.WHY_OPENED,
        InteractionKind.MORE_LIKE_THIS,
        InteractionKind.BREAKDOWN_OPENED,
    ):
        row.relevance_score += 2.0
    elif kind in (InteractionKind.VIEW, InteractionKind.CLICK):
        row.relevance_score += 0.5
    row.interaction_count += 1


# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------


def _raw_relevance(affinity: UserInstrumentAffinity, weights: RelevanceWeights = WEIGHTS) -> float:
    """Interaction-derived relevance, before star/priority and decay."""
    score = 0.0
    score += min(weights.view_cap, affinity.view_count * weights.per_view)
    score += min(weights.why_cap, affinity.why_count * weights.per_why)
    score += min(
        weights.more_like_this_cap,
        affinity.more_like_this_count * weights.per_more_like_this,
    )
    score -= min(weights.dismiss_cap, affinity.dismiss_count * weights.per_dismiss)
    score -= min(weights.mute_cap, affinity.mute_count * weights.per_mute)
    return score


def _decay_factor(
    last_interaction: datetime | None, now: datetime, weights: RelevanceWeights = WEIGHTS
) -> float:
    if last_interaction is None:
        return 1.0
    days_idle = max(0.0, (now - last_interaction).total_seconds() / 86400.0)
    if days_idle <= weights.decay_after_days:
        return 1.0
    # Linear decay to a floor rather than to zero: a stock the user once cared
    # about is still more relevant than one they never touched.
    over = days_idle - weights.decay_after_days
    return max(weights.decay_floor, 1.0 - over / (weights.decay_after_days * 2))


def behavioral_relevance(
    db: Session,
    user: User,
    membership: dict[str, dict] | None = None,
    now: datetime | None = None,
    weights: RelevanceWeights = WEIGHTS,
) -> dict[str, float]:
    """Per-symbol behavioural relevance in 0-100.

    Combines explicit signals (star, priority) with implicit ones (views, why
    opens, dismissals) and a weak sector prior. The result is clamped to
    0-100; the engine then caps how much of it can affect a score.
    """
    now = now or utcnow()
    membership = membership or {}

    rows = db.execute(
        select(Instrument.symbol, Instrument.sector, UserInstrumentAffinity)
        .join(
            UserInstrumentAffinity,
            UserInstrumentAffinity.instrument_id == Instrument.id,
        )
        .where(UserInstrumentAffinity.user_id == user.id)
    ).all()

    sector_scores = {
        row.sector: row.relevance_score
        for row in db.scalars(
            select(UserSectorAffinity).where(UserSectorAffinity.user_id == user.id)
        ).all()
    }
    max_sector = max(sector_scores.values(), default=0.0)

    relevance: dict[str, float] = {}
    for symbol, sector, affinity in rows:
        score = _raw_relevance(affinity, weights) * _decay_factor(
            affinity.last_interaction_at, now, weights
        )
        if sector and max_sector > 0:
            score += weights.sector_bonus_cap * (sector_scores.get(sector, 0.0) / max_sector)
        relevance[symbol] = score

    # Explicit signals apply to every watched symbol, including ones with no
    # interaction history: starring something is itself a strong statement.
    for symbol, settings in membership.items():
        score = relevance.get(symbol, 0.0)
        if settings.get("starred"):
            score += weights.star
        score += min(3, int(settings.get("priority", 0))) * weights.priority_step
        relevance[symbol] = score

    return {symbol: max(0.0, min(100.0, score)) for symbol, score in relevance.items()}


def is_established(db: Session, user: User, instrument_id: int) -> bool:
    """Whether enough interactions exist to call this a settled preference."""
    affinity = db.scalar(
        select(UserInstrumentAffinity).where(
            UserInstrumentAffinity.user_id == user.id,
            UserInstrumentAffinity.instrument_id == instrument_id,
        )
    )
    if affinity is None:
        return False
    total = (
        affinity.view_count
        + affinity.why_count
        + affinity.dismiss_count
        + affinity.more_like_this_count
    )
    return total >= ESTABLISHED_INTERACTION_COUNT


# ---------------------------------------------------------------------------
# Mutes
# ---------------------------------------------------------------------------


DEFAULT_MUTE_HOURS = 24


def mute_instrument(
    db: Session, user: User, instrument_id: int, hours: int = DEFAULT_MUTE_HOURS
) -> StockMute:
    """Temporarily mute a stock. Always time-boxed - no permanent mute in v1."""
    hours = max(1, min(24 * 30, hours))
    until = utcnow() + timedelta(hours=hours)

    mute = db.scalar(
        select(StockMute).where(
            StockMute.user_id == user.id, StockMute.instrument_id == instrument_id
        )
    )
    if mute is None:
        mute = StockMute(user_id=user.id, instrument_id=instrument_id, muted_until=until)
        db.add(mute)
    else:
        mute.muted_until = until

    record_interaction(
        db, user, InteractionKind.MUTE, instrument_id=instrument_id,
        context={"hours": hours},
    )
    db.flush()
    return mute


def unmute_instrument(db: Session, user: User, instrument_id: int) -> None:
    mute = db.scalar(
        select(StockMute).where(
            StockMute.user_id == user.id, StockMute.instrument_id == instrument_id
        )
    )
    if mute is not None:
        db.delete(mute)
    record_interaction(db, user, InteractionKind.UNMUTE, instrument_id=instrument_id)
    db.flush()


def muted_symbols(db: Session, user: User, now: datetime | None = None) -> set[str]:
    """Symbols currently muted. Expired mutes lapse on their own."""
    now = now or utcnow()
    rows = db.execute(
        select(Instrument.symbol)
        .join(StockMute, StockMute.instrument_id == Instrument.id)
        .where(StockMute.user_id == user.id, StockMute.muted_until > now)
    ).all()
    return {row[0] for row in rows}
