"""API response and request schemas.

Every ``/api/v1`` response is validated against one of these on the way out, so
a service returning a malformed dict fails loudly here rather than reaching the
frontend. Raw provider payloads have no representation in this module by
design - they never leave the backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class FreshnessOut(BaseModel):
    state: str
    as_of: str
    age_seconds: float
    source: str
    market_status: str
    label: str


class DataModeOut(BaseModel):
    """Always present, always explicit. Replay is never mistaken for live."""

    mode: str
    is_replay: bool
    degraded: bool
    label: str
    reason: str | None = None
    # Which source actually answered, and what was tried to get there. Shown so
    # a user can tell NSE data from Yahoo data from replay data.
    provider: str | None = None
    chain: list[str] = Field(default_factory=list)
    attempts: list[str] = Field(default_factory=list)
    scenario: str | None = None
    step: int | None = None
    total_steps: int | None = None
    virtual_time: datetime | None = None


class InstrumentOut(BaseModel):
    symbol: str
    name: str
    sector: str | None = None
    exchange: str = "NSE"
    is_supported: bool = True


class PriceOut(BaseModel):
    price: float
    previous_close: float
    change: float
    change_percent: float
    volume: float | None = None


class SignalOut(BaseModel):
    type: str
    family: str
    availability: str
    value: float | None = None
    strength: float | None = None
    direction: str
    evidence: str
    confidence: float


class ScoreBreakdownOut(BaseModel):
    """The "How LUMEN calculated this" payload. Always deterministic."""

    summary: str
    objective_score: float
    personalization_adjustment: float
    final_score: float
    severity: str
    breakdown: dict[str, float]
    confidence: str
    confidence_report: dict[str, Any]
    meaningful: bool
    signals: list[SignalOut]


class ExplanationOut(BaseModel):
    text: str
    deterministic_text: str
    source: str
    model: str | None = None
    fallback_reason: str | None = None


class EventSummaryOut(BaseModel):
    """What an event card shows before anything is expanded."""

    id: int
    event_key: str
    symbol: str
    name: str
    headline: str
    severity: str
    direction: str
    score: float
    peak_score: float
    confidence: str
    status: str
    classification: str
    is_market_wide: bool
    primary_reason: str
    started_at: datetime
    last_updated_at: datetime
    session_date: str
    observation_count: int
    read: bool = False
    reviewed: bool = False
    dismissed: bool = False


class TimelinePointOut(BaseModel):
    observed_at: datetime
    score: float
    severity: str
    direction: str
    price: float | None = None


class EventDetailOut(EventSummaryOut):
    explanation: ExplanationOut
    evidence: list[str]
    signals: list[SignalOut]
    score_detail: ScoreBreakdownOut
    timeline: list[TimelinePointOut]
    market_context_note: str | None = None
    price: PriceOut | None = None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class AttentionCardOut(BaseModel):
    """One of the "3 stocks deserve your attention" cards."""

    rank: int
    symbol: str
    name: str
    price: PriceOut
    score: float
    severity: str
    direction: str
    confidence: str
    primary_reason: str
    why_it_matters: str
    event_id: int | None = None
    classification: str


class MarketContextOut(BaseModel):
    benchmark_return: float | None = None
    advancing: int
    declining: int
    unchanged: int
    total: int
    breadth_ratio: float
    median_return: float
    dominant_direction: str
    is_market_wide_move: bool
    label: str


class DaySummaryOut(BaseModel):
    session_date: str
    headline: str
    top_symbols: list[str]
    event_count: int
    events: list[EventSummaryOut]


class SinceAwayOut(BaseModel):
    baseline_at: datetime
    baseline_source: str
    baseline_label: str
    summary: str
    sessions_missed: int
    is_multi_session: bool
    top_events: list[EventSummaryOut]
    remainder_count: int
    days: list[DaySummaryOut] = Field(default_factory=list)


class NotificationStateOut(BaseModel):
    unread_count: int
    pending_delivery: list[dict[str, Any]] = Field(default_factory=list)


class DashboardOut(BaseModel):
    """Everything the homepage needs in one call.

    Deliberately aggregated: the frontend must never have to reconstruct
    ranking, significance or digest logic from separate endpoints.
    """

    generated_at: datetime
    data_mode: DataModeOut
    freshness: FreshnessOut | None = None
    attention: list[AttentionCardOut]
    attention_headline: str
    since_you_were_away: SinceAwayOut
    event_feed: list[EventSummaryOut]
    market: MarketContextOut
    notifications: NotificationStateOut
    watchlist_count: int
    tracked_symbols: int
    muted_symbols: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


class WatchlistItemOut(BaseModel):
    symbol: str
    name: str
    sector: str | None = None
    position: int
    priority: int
    starred: bool
    notes: str | None = None
    price: PriceOut | None = None
    score: float | None = None
    severity: str | None = None
    latest_reason: str | None = None
    event_id: int | None = None
    sparkline: list[float] = Field(default_factory=list)
    muted: bool = False


class WatchlistOut(BaseModel):
    id: int
    name: str
    is_default: bool
    position: int
    item_count: int
    items: list[WatchlistItemOut] = Field(default_factory=list)


class WatchlistCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_default: bool = False


class WatchlistRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WatchlistAddIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    priority: int = Field(default=0, ge=0, le=3)
    starred: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class WatchlistItemUpdateIn(BaseModel):
    priority: int | None = Field(default=None, ge=0, le=3)
    starred: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    position: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------


class SearchResultOut(BaseModel):
    """Search stays deliberately thin: name, ticker, price. No intelligence."""

    symbol: str
    name: str
    sector: str | None = None
    price: float | None = None
    change_percent: float | None = None
    is_supported: bool


class StockDetailOut(BaseModel):
    instrument: InstrumentOut
    price: PriceOut
    freshness: FreshnessOut
    data_mode: DataModeOut
    score: float | None = None
    severity: str | None = None
    direction: str | None = None
    confidence: str | None = None
    current_event: EventSummaryOut | None = None
    event_timeline: list[EventSummaryOut] = Field(default_factory=list)
    chart: list[dict[str, Any]] = Field(default_factory=list)
    benchmark_return: float | None = None
    sector_return: float | None = None
    relative_to_benchmark: float | None = None
    relative_to_sector: float | None = None
    classification: str | None = None
    muted: bool = False
    in_watchlists: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth and profile
# ---------------------------------------------------------------------------


class GoogleLoginIn(BaseModel):
    id_token: str = Field(min_length=10)


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class UserOut(BaseModel):
    id: int
    email: str | None = None
    display_name: str | None = None
    picture_url: str | None = None
    is_guest: bool
    onboarded: bool
    has_watchlist: bool


class ProfileIn(BaseModel):
    """Lightweight and entirely optional. Not a questionnaire."""

    risk_appetite: str | None = Field(default=None, max_length=20)
    investment_style: str | None = Field(default=None, max_length=30)
    sectors: list[str] = Field(default_factory=list, max_length=20)


class ProfileOut(BaseModel):
    risk_appetite: str | None = None
    investment_style: str | None = None
    sectors: list[str] = Field(default_factory=list)
    onboarded: bool = False


class StarterSetupIn(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    watchlist_name: str | None = None


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


class InteractionIn(BaseModel):
    kind: str
    symbol: str | None = None
    event_id: int | None = None
    context: dict[str, Any] | None = None


class MuteIn(BaseModel):
    symbol: str
    hours: int = Field(default=24, ge=1, le=720)


class FeedbackIn(BaseModel):
    event_id: int
    feedback: str = Field(default="more_like_this")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class ScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    teaching_point: str
    focus_symbols: list[str]
    total_steps: int
    step_minutes: int


class ReplayStartIn(BaseModel):
    scenario_key: str
    speed: int = Field(default=1, ge=1, le=60)


class ReplayControlIn(BaseModel):
    steps: int = Field(default=1, ge=1, le=25)


class ReplaySessionOut(BaseModel):
    id: int
    scenario_key: str
    scenario_name: str
    speed: int
    status: str
    step_index: int
    total_steps: int
    virtual_now: datetime
    label: str = "Demo / Replay"
    detected: list[EventSummaryOut] = Field(default_factory=list)
    market: MarketContextOut | None = None


# Resolve the forward reference used by SessionOut.
SessionOut.model_rebuild()
