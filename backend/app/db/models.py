"""LUMEN ORM models.

Scope discipline
----------------
PostgreSQL holds *product and derived* data: users, watchlists, scored events,
behaviour, notifications. It does not hold bulk raw market history - replay
candle sets live as files under ``backend/data/`` and are streamed by the
replay provider. Only observations LUMEN actually reasoned about are persisted
(90-day retention), because those are what "what changed since you left" needs.

Portability
-----------
Generic SQLAlchemy types are used throughout (``JSON``, not ``JSONB``; no
``ARRAY``), so the same metadata and the same migration run on PostgreSQL for
real use and on SQLite for tests. Swapping ``JSON`` for ``JSONB`` later is a
type change, not a schema redesign.

Identity
--------
Integer surrogate keys everywhere, except that ``MarketEventRow`` also carries
the engine's deterministic ``event_key`` so a replayed run reproduces exactly
the same event identities.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    """Naive UTC timestamp. All stored times are UTC without tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    """An authenticated person, or a guest demo session.

    ``google_sub`` is Google's stable subject id. It is the identity anchor:
    email can change, ``sub`` does not. Guest users have no ``google_sub``.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(String(1000))
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    # --- "Since you were away" baselines ------------------------------------
    # Three independent marks, deliberately not collapsed into one. The digest
    # baseline is chosen from these per the resolution rules in
    # services/digest.py, and a refresh must not silently destroy a digest.
    last_app_open_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The end of the user's PREVIOUS visit. This, not the current visit, is
    # what "since you last checked" compares against - otherwise opening the
    # app would immediately reset the very digest the user came back to read.
    previous_visit_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_digest_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_observation_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    manual_baseline_at: Mapped[datetime | None] = mapped_column(DateTime)

    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime)

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    watchlists: Mapped[list[Watchlist]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserProfile(Base):
    """Optional lightweight investor profile. Never a long questionnaire."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    risk_appetite: Mapped[str | None] = mapped_column(String(20))  # low|moderate|high
    investment_style: Mapped[str | None] = mapped_column(String(30))
    # JSON rather than ARRAY to keep the schema portable to SQLite.
    sectors: Mapped[list | None] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="profile")


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------


class Instrument(Base):
    """One tradable entity, shared across every user and watchlist.

    A stock is a single row here no matter how many watchlists reference it -
    that is what lets polling and scoring be O(instruments) rather than
    O(users x instruments).

    ``is_supported`` is the honest boundary: search may surface an instrument,
    but only supported ones can be added to a watchlist.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), default="NSE", nullable=False)
    isin: Mapped[str | None] = mapped_column(String(20), index=True)
    sector: Mapped[str | None] = mapped_column(String(80), index=True)

    # Upstox's own identifier, e.g. "NSE_EQ|INE002A01018".
    provider_key: Mapped[str | None] = mapped_column(String(64), index=True)

    is_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class InstrumentDailyStats(Base):
    """Per-instrument baselines the engine needs to call anything 'abnormal'.

    Computed once from historical candles and read from here, never fetched
    inside a request. Sample counts are stored alongside the values because
    the engine refuses to use a baseline that is too thin.
    """

    __tablename__ = "instrument_daily_stats"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of_date", name="uq_daily_stats"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[datetime] = mapped_column(Date, nullable=False)

    typical_daily_move: Mapped[float | None] = mapped_column(Float)
    typical_move_sample_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    historical_volatility: Mapped[float | None] = mapped_column(Float)
    volatility_sample_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    average_volume: Mapped[float | None] = mapped_column(Float)
    historical_gap_stdev: Mapped[float | None] = mapped_column(Float)

    previous_close: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class IntradayVolumeBaseline(Base):
    """Same-time-of-day volume baselines, bucketed by minute-of-session.

    This is the preferred volume baseline. When a bucket has too few sample
    days the engine falls back to the full-day average and says so - which is
    why ``sample_days`` is stored per bucket rather than assumed.
    """

    __tablename__ = "intraday_volume_baselines"
    __table_args__ = (
        UniqueConstraint("instrument_id", "minute_of_session", name="uq_intraday_volume"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    minute_of_session: Mapped[int] = mapped_column(Integer, nullable=False)
    average_volume: Mapped[float] = mapped_column(Float, nullable=False)
    sample_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="watchlists")
    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    """Membership of one instrument in one watchlist.

    Per-membership rather than per-instrument: the same stock can sit in two
    watchlists with different ordering, priority, notes and star state, and
    the two are genuinely independent.
    """

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id", name="uq_watchlist_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")
    instrument: Mapped[Instrument] = relationship()


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


class MarketObservationRow(Base):
    """A normalized observation the engine reasoned about. 90-day retention.

    Stores the *inputs* to scoring rather than raw provider payloads, so a
    past digest can be reconstructed exactly and so provider responses never
    leak outward. ``scope`` separates live data from each replay session.
    """

    __tablename__ = "market_observations"
    __table_args__ = (
        Index("ix_observation_instrument_time", "instrument_id", "observed_at"),
        Index("ix_observation_scope_time", "scope", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    session_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)

    scope: Mapped[str] = mapped_column(String(64), default="live", nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness: Mapped[str] = mapped_column(String(16), default="live", nullable=False)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    price: Mapped[float] = mapped_column(Float, nullable=False)
    previous_price: Mapped[float] = mapped_column(Float, nullable=False)
    daily_return: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    gap_percent: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    sector_return: Mapped[float | None] = mapped_column(Float)
    intraday_volatility: Mapped[float | None] = mapped_column(Float)

    # The exact MarketObservation payload handed to the engine, for audit and
    # for the protected debug endpoint.
    payload: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class NewsItem(Base):
    """A news article, matched to instruments by entity + publication time."""

    __tablename__ = "news_items"
    __table_args__ = (Index("ix_news_instrument_time", "instrument_id", "published_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Only ever true when a source explicitly establishes causation. LUMEN
    # never infers it from co-occurrence.
    causal_link_established: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    entities: Mapped[list | None] = mapped_column(JSON, default=list)

    scope: Mapped[str] = mapped_column(String(64), default="live", nullable=False)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MarketCalendarDay(Base):
    """Exchange calendar. Drives session state, gaps and 'previous session'."""

    __tablename__ = "market_calendar"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_date: Mapped[datetime] = mapped_column(Date, unique=True, nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="NSE", nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class MarketEventRow(Base):
    """A persisted, evolving market event.

    Mirrors the engine's ``MarketEvent``. Scores, severity, breakdown and
    evidence are all produced by the deterministic engine; nothing here is
    written by an LLM. ``llm_explanation`` is stored separately from
    ``deterministic_explanation`` precisely so the fallback is always intact.
    """

    __tablename__ = "market_events"
    __table_args__ = (
        UniqueConstraint("event_key", "scope", name="uq_event_key_scope"),
        Index("ix_event_instrument_updated", "instrument_id", "last_updated_at"),
        Index("ix_event_scope_score", "scope", "current_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(64), default="live", nullable=False)

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    session_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)

    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    current_score: Mapped[float] = mapped_column(Float, nullable=False)
    peak_score: Mapped[float] = mapped_column(Float, nullable=False)
    objective_score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)

    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    # Deterministic templates. Always present, never overwritten by the LLM.
    deterministic_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    llm_explanation: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(String(80))

    # Stock-specific vs market-wide, classified from the engine's
    # relative-performance signals. Not a score mutation.
    is_market_wide: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    breakdown: Mapped[dict | None] = mapped_column(JSON)
    evidence: Mapped[list | None] = mapped_column(JSON)
    supporting_signals: Mapped[list | None] = mapped_column(JSON)
    confidence_report: Mapped[dict | None] = mapped_column(JSON)

    observation_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    instrument: Mapped[Instrument] = relationship()
    timeline: Mapped[list[EventTimelinePoint]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventTimelinePoint.observed_at"
    )


class EventTimelinePoint(Base):
    """One step in an event's evolution. Powers the event timeline UI."""

    __tablename__ = "event_timeline_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("market_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float | None] = mapped_column(Float)

    event: Mapped[MarketEventRow] = relationship(back_populates="timeline")


class UserEventState(Base):
    """Per-user relationship to one event: read, reviewed, dismissed.

    Dismissal removes the event from the homepage, keeps it in history, and
    feeds back into ranking - which is why it lives here rather than on the
    event itself. Events are shared; opinions about them are not.
    """

    __tablename__ = "user_event_states"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_user_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("market_events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
    why_opened_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    breakdown_opened_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String(30))  # "more_like_this"

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Behaviour and personalization
# ---------------------------------------------------------------------------


class UserInteraction(Base):
    """Append-only behaviour log: views, clicks, why-opens, dismissals, feedback.

    Kept raw and thin. Aggregation into affinity happens in
    ``UserInstrumentAffinity`` so ranking never has to scan this table.
    """

    __tablename__ = "user_interactions"
    __table_args__ = (Index("ix_interaction_user_time", "user_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_events.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSON)


class UserInstrumentAffinity(Base):
    """Aggregated per-user, per-instrument behavioural relevance.

    The single input to the engine's capped personalization adjustment. Stored
    aggregated rather than derived on read, and deliberately simple counters
    rather than a learned model.
    """

    __tablename__ = "user_instrument_affinity"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id", name="uq_user_instrument"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    why_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dismiss_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    more_like_this_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mute_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class UserSectorAffinity(Base):
    """Aggregated sector-level interest, used as a weak ranking prior."""

    __tablename__ = "user_sector_affinity"
    __table_args__ = (UniqueConstraint("user_id", "sector", name="uq_user_sector"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sector: Mapped[str] = mapped_column(String(80), nullable=False)
    interaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class StockMute(Base):
    """A temporary mute. Time-boxed by design - no permanent mute in v1."""

    __tablename__ = "stock_mutes"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id", name="uq_user_mute"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    muted_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class Notification(Base):
    """An inbox entry. Generated only for High Attention and Critical events.

    Persisted regardless of whether the browser ever displays it, so denied
    notification permission degrades to an inbox rather than losing the alert.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_created", "user_id", "created_at"),
        UniqueConstraint("user_id", "event_id", name="uq_notification_user_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_events.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class ReplaySession(Base):
    """One user's run through a replay scenario.

    The virtual clock lives here: ``virtual_now`` advances by scenario steps
    multiplied by ``speed``, never by wall-clock time. That is what makes
    replay work identically at 3am on a Sunday.
    """

    __tablename__ = "replay_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    speed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ready", nullable=False)

    virtual_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    virtual_now: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def scope(self) -> str:
        """Namespace isolating this session's observations and events."""
        return f"replay:{self.id}"


__all__ = [
    "EventTimelinePoint",
    "Instrument",
    "InstrumentDailyStats",
    "IntradayVolumeBaseline",
    "MarketCalendarDay",
    "MarketEventRow",
    "MarketObservationRow",
    "NewsItem",
    "Notification",
    "ReplaySession",
    "StockMute",
    "User",
    "UserEventState",
    "UserInstrumentAffinity",
    "UserInteraction",
    "UserProfile",
    "UserSectorAffinity",
    "Watchlist",
    "WatchlistItem",
    "utcnow",
]
