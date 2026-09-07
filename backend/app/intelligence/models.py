"""Typed domain models for LUMEN's market-intelligence engine.

UNIT CONVENTION
---------------
Every rate-like field in this module is expressed in **percent**, not as a
fraction. A 4.2% decline is ``-4.2``, never ``-0.042``. Volatility of 1.8% per
day is ``1.8``. Differences between two percentages are described in
*percentage points* (pp).

This convention is applied consistently across observations, signals and
scoring. Mixing the two representations is the single easiest way to produce
silently wrong significance scores, so it is stated once here and never varied.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Availability(str, Enum):
    """Whether a signal could be computed, and if not, why not.

    The distinction between ``UNAVAILABLE`` and ``INSUFFICIENT_HISTORY`` is
    deliberate: the first means the input was never supplied, the second means
    it was supplied but there is not enough of it to say anything honest.
    Neither is ever collapsed into a zero-valued signal.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_HISTORY = "insufficient_history"


class Direction(str, Enum):
    """Which way a move went. Entirely independent of how much it matters."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class SignalType(str, Enum):
    PRICE_MOVEMENT = "price_movement"
    HISTORICAL_NORMALIZED_MOVEMENT = "historical_normalized_movement"
    VOLATILITY_NORMALIZED_MOVEMENT = "volatility_normalized_movement"
    RELATIVE_TO_BENCHMARK = "relative_to_benchmark"
    RELATIVE_TO_SECTOR = "relative_to_sector"
    ABNORMAL_VOLUME = "abnormal_volume"
    VOLATILITY_ANOMALY = "volatility_anomaly"
    GAP = "gap"
    NEWS_EVENT = "news_event"


class SignalFamily(str, Enum):
    """Scoring families.

    Several signals can describe the same underlying phenomenon (three
    different views of "the price moved"). Scoring happens per *family* so that
    correlated signals cannot stack into an inflated score.
    """

    PRICE_MOVEMENT = "price_movement"
    RELATIVE_PERFORMANCE = "relative_performance"
    VOLUME = "volume"
    VOLATILITY = "volatility"
    GAP = "gap"
    NEWS = "news"


SIGNAL_FAMILY: dict[SignalType, SignalFamily] = {
    SignalType.PRICE_MOVEMENT: SignalFamily.PRICE_MOVEMENT,
    SignalType.HISTORICAL_NORMALIZED_MOVEMENT: SignalFamily.PRICE_MOVEMENT,
    SignalType.VOLATILITY_NORMALIZED_MOVEMENT: SignalFamily.PRICE_MOVEMENT,
    SignalType.RELATIVE_TO_BENCHMARK: SignalFamily.RELATIVE_PERFORMANCE,
    SignalType.RELATIVE_TO_SECTOR: SignalFamily.RELATIVE_PERFORMANCE,
    SignalType.ABNORMAL_VOLUME: SignalFamily.VOLUME,
    SignalType.VOLATILITY_ANOMALY: SignalFamily.VOLATILITY,
    SignalType.GAP: SignalFamily.GAP,
    SignalType.NEWS_EVENT: SignalFamily.NEWS,
}


class Severity(str, Enum):
    NOISE = "Noise"
    WORTH_WATCHING = "Worth Watching"
    HIGH_ATTENTION = "High Attention"
    CRITICAL = "Critical"


class Confidence(str, Enum):
    """How well-established the picture is. NOT a synonym for severity.

    A 90/100 move with missing corroborating history is high severity and
    medium confidence. The two dimensions are reported separately and never
    derived from one another.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VolumeBaseline(str, Enum):
    """Which baseline the abnormal-volume signal actually used.

    Recorded explicitly on every volume signal. A same-time-of-day baseline is
    never assumed when only a full-day average was supplied.
    """

    SAME_TIME_OF_DAY = "same_time_of_day"
    FULL_DAY_AVERAGE = "full_day_average"
    NONE = "none"


class NewsEvidence(BaseModel):
    """Structured news context supplied to the engine.

    News ingestion is not implemented. This is the interface future ingestion
    will populate; for now fixtures provide it explicitly.
    """

    model_config = ConfigDict(frozen=True)

    present: bool = False
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    minutes_before_move: int | None = Field(
        default=None,
        description="Publication lead time relative to the move. None = timing unknown.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    headline: str | None = None
    causal_link_established: bool = Field(
        default=False,
        description=(
            "True only when a source explicitly establishes causation. LUMEN "
            "never infers this from co-occurrence; it drives whether the "
            "explanation may use causal language at all."
        ),
    )


class MarketObservation(BaseModel):
    """A single point-in-time view of one instrument, plus its context.

    Optional fields are genuinely optional. A ``None`` means "not known", and
    the signal functions translate that into an explicit unavailable state
    rather than a zero.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime

    price: float = Field(gt=0)
    previous_price: float = Field(gt=0)
    daily_return: float | None = Field(
        default=None, description="Percent. Derived from prices when not supplied."
    )

    # --- Volume context -----------------------------------------------------
    volume: float | None = Field(default=None, ge=0)
    average_volume: float | None = Field(
        default=None, gt=0, description="Full-day average volume. Fallback baseline."
    )
    same_time_of_day_volume: float | None = Field(
        default=None,
        gt=0,
        description="Preferred baseline: average volume by this point of session.",
    )
    same_time_of_day_sample_days: int = Field(
        default=0,
        ge=0,
        description="Sessions backing the same-time-of-day baseline.",
    )

    # --- Volatility context -------------------------------------------------
    historical_volatility: float | None = Field(
        default=None, ge=0, description="Percent. Daily stdev of returns."
    )
    intraday_volatility: float | None = Field(
        default=None, ge=0, description="Percent. Realized volatility so far today."
    )
    volatility_sample_days: int = Field(default=0, ge=0)
    intraday_observation_count: int = Field(default=0, ge=0)

    # --- Typical-movement context ------------------------------------------
    typical_daily_move: float | None = Field(
        default=None, ge=0, description="Percent. Mean absolute daily return."
    )
    typical_move_sample_days: int = Field(default=0, ge=0)

    # --- Gap context --------------------------------------------------------
    gap_percent: float | None = Field(
        default=None, description="Percent. Open vs previous relevant close."
    )
    historical_gap_stdev: float | None = Field(
        default=None, ge=0, description="Percent. Stdev of historical opening gaps."
    )

    # --- Market context -----------------------------------------------------
    benchmark_return: float | None = Field(default=None, description="Percent. NIFTY.")
    sector_return: float | None = Field(default=None, description="Percent.")

    news: NewsEvidence | None = None

    @model_validator(mode="after")
    def _derive_daily_return(self) -> MarketObservation:
        """Fill ``daily_return`` from the price pair when it was not supplied."""
        if self.daily_return is None:
            derived = (self.price - self.previous_price) / self.previous_price * 100.0
            object.__setattr__(self, "daily_return", round(derived, 6))
        return self

    def evolve(self, **changes: Any) -> MarketObservation:
        """Return a modified copy, re-running validation.

        Use this instead of ``model_copy`` whenever a price changes.
        ``model_copy`` bypasses validators, so it would carry the original
        ``daily_return`` onto a new price and silently produce a signal that
        contradicts the prices it was derived from. This re-derives the return
        unless one is explicitly supplied.
        """
        data = self.model_dump()
        data.update(changes)
        if "daily_return" not in changes:
            data["daily_return"] = None
        return MarketObservation.model_validate(data)


class Signal(BaseModel):
    """One deterministic measurement, with its own availability and confidence."""

    model_config = ConfigDict(frozen=True)

    signal_type: SignalType
    availability: Availability
    value: float | None = Field(
        default=None, description="Raw domain value, e.g. -3.5 pp or 2.8 (x normal)."
    )
    normalized_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Strength 0-1. None when unavailable."
    )
    direction: Direction = Direction.NEUTRAL
    evidence: str = Field(default="", description="Factual, human-readable statement.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def family(self) -> SignalFamily:
        return SIGNAL_FAMILY[self.signal_type]

    @property
    def is_available(self) -> bool:
        return self.availability is Availability.AVAILABLE

    @property
    def is_directional(self) -> bool:
        """Volume and volatility have magnitude but no inherent direction."""
        return self.signal_type not in {
            SignalType.ABNORMAL_VOLUME,
            SignalType.VOLATILITY_ANOMALY,
        }


class ConfidenceReport(BaseModel):
    """Why a confidence level was assigned. Surfaced for explainability."""

    model_config = ConfigDict(frozen=True)

    level: Confidence
    independent_signals: int
    families_available: int
    families_considered: int
    coverage: float = Field(ge=0.0, le=1.0)
    contradictions: int = 0
    reasons: list[str] = Field(default_factory=list)


class AttentionResult(BaseModel):
    """The engine's complete, explainable verdict on one observation."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime

    objective_score: float = Field(ge=0.0, le=100.0)
    personalization_adjustment: float = 0.0
    final_score: float = Field(ge=0.0, le=100.0)

    severity: Severity
    direction: Direction
    meaningful: bool

    signals: list[Signal] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(
        default_factory=dict, description="Points contributed per signal family."
    )
    evidence: list[str] = Field(default_factory=list)

    confidence: Confidence
    confidence_report: ConfidenceReport

    @property
    def available_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.is_available]
