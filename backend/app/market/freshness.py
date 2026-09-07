"""Data freshness classification.

There is no single universal staleness threshold, and pretending otherwise
would be wrong in both directions: a 90-second-old quote is stale mid-session
and perfectly current at 9pm on a Saturday. Freshness therefore depends on
market status and on which provider supplied the value.

Every value LUMEN shows carries one of these states plus the timestamp and
source it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.config import Settings, get_settings
from app.market.calendar import MarketStatus, market_status


class Freshness(str, Enum):
    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"


@dataclass(frozen=True)
class FreshnessReport:
    """Freshness plus everything needed to justify it in the UI."""

    state: Freshness
    as_of: datetime
    age_seconds: float
    source: str
    market_status: MarketStatus
    label: str

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "as_of": self.as_of.isoformat(),
            "age_seconds": round(self.age_seconds, 1),
            "source": self.source,
            "market_status": self.market_status.value,
            "label": self.label,
        }


def _thresholds(status: MarketStatus, settings: Settings) -> tuple[int, int]:
    """(live_ceiling, delayed_ceiling) in seconds for the given market state."""
    if status is MarketStatus.OPEN:
        return settings.freshness_live_seconds_open, settings.freshness_delayed_seconds_open
    return settings.freshness_live_seconds_closed, settings.freshness_delayed_seconds_closed


def classify(
    as_of: datetime,
    now: datetime,
    source: str,
    settings: Settings | None = None,
) -> FreshnessReport:
    """Classify a value's freshness.

    ``as_of`` is the provider's own timestamp, not our receipt time. When a
    feed lags, its timestamp is the truth about how old the data is.
    """
    settings = settings or get_settings()
    status = market_status(now)
    age = max(0.0, (now - as_of).total_seconds())
    live_ceiling, delayed_ceiling = _thresholds(status, settings)

    if age <= live_ceiling:
        state = Freshness.LIVE
    elif age <= delayed_ceiling:
        state = Freshness.DELAYED
    else:
        state = Freshness.STALE

    return FreshnessReport(
        state=state,
        as_of=as_of,
        age_seconds=age,
        source=source,
        market_status=status,
        label=_label(state, status, age),
    )


def _label(state: Freshness, status: MarketStatus, age: float) -> str:
    """Short human phrasing. The UI shows this verbatim next to a price."""
    if status is not MarketStatus.OPEN:
        closed_reason = {
            MarketStatus.WEEKEND: "Market closed - weekend",
            MarketStatus.HOLIDAY: "Market closed - holiday",
            MarketStatus.PRE_OPEN: "Pre-open",
        }.get(status, "Market closed")
        if state is Freshness.STALE:
            return f"{closed_reason}, data may be out of date"
        return f"{closed_reason}, showing last session"

    if state is Freshness.LIVE:
        return "Live"
    if state is Freshness.DELAYED:
        return f"Delayed {int(age)}s"
    return f"Stale - last update {int(age // 60)}m ago"


@dataclass(frozen=True)
class ValueDisagreement:
    """Two sources reporting materially different values for one field.

    LUMEN flags the discrepancy and keeps both values rather than silently
    picking a winner - a disagreement between feeds is itself information.
    """

    field: str
    primary_source: str
    primary_value: float
    secondary_source: str
    secondary_value: float
    difference_percent: float

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "primary": {"source": self.primary_source, "value": self.primary_value},
            "secondary": {"source": self.secondary_source, "value": self.secondary_value},
            "difference_percent": round(self.difference_percent, 4),
            "note": "Sources disagree; both values are shown.",
        }


def detect_disagreement(
    field: str,
    primary_source: str,
    primary_value: float | None,
    secondary_source: str,
    secondary_value: float | None,
    tolerance_percent: float = 0.5,
) -> ValueDisagreement | None:
    """Flag a material difference between two sources for the same field."""
    if primary_value is None or secondary_value is None or primary_value == 0:
        return None
    difference = abs(primary_value - secondary_value) / abs(primary_value) * 100.0
    if difference <= tolerance_percent:
        return None
    return ValueDisagreement(
        field=field,
        primary_source=primary_source,
        primary_value=primary_value,
        secondary_source=secondary_source,
        secondary_value=secondary_value,
        difference_percent=difference,
    )
