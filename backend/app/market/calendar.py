"""NSE exchange calendar.

Everything time-related in LUMEN routes through here: whether the market is
open, what the previous session was, how far into a session a timestamp sits.

Two conventions, applied without exception:

* Stored and passed-around datetimes are **naive UTC**.
* Session reasoning happens in **IST**, because that is what defines an NSE
  session boundary.

The conversion lives here rather than being repeated at call sites, which is
what stops "is the market open?" from quietly meaning different things in
different modules.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
PRE_OPEN_START = time(9, 0)

SESSION_MINUTES = (
    datetime.combine(date(2000, 1, 1), SESSION_CLOSE)
    - datetime.combine(date(2000, 1, 1), SESSION_OPEN)
).seconds // 60

# NSE trading holidays. Deliberately a static list: a hackathon does not need
# a holiday feed, but it does need to not call Republic Day a trading day.
# Extend as the exchange publishes each year.
NSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31), date(2025, 4, 10),
        date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 8, 15),
        date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 21), date(2025, 10, 22),
        date(2025, 11, 5), date(2025, 12, 25),
        # 2026
        date(2026, 1, 26), date(2026, 3, 4), date(2026, 3, 19), date(2026, 4, 1),
        date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 8, 15),
        date(2026, 10, 2), date(2026, 11, 9), date(2026, 12, 25),
    }
)


class MarketStatus(str, Enum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    CLOSED = "closed"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"

    @property
    def is_tradeable(self) -> bool:
        return self is MarketStatus.OPEN


def to_ist(moment: datetime) -> datetime:
    """Interpret a naive datetime as UTC and convert to IST."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST)


def to_utc_naive(moment: datetime) -> datetime:
    """Convert any datetime to the naive-UTC form used for storage."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def is_trading_day(day: date, holidays: frozenset[date] = NSE_HOLIDAYS) -> bool:
    """Weekday and not an exchange holiday."""
    return day.weekday() < 5 and day not in holidays


def market_status(
    moment: datetime, holidays: frozenset[date] = NSE_HOLIDAYS
) -> MarketStatus:
    """Classify a UTC instant against the NSE session calendar."""
    local = to_ist(moment)
    day = local.date()

    if day.weekday() >= 5:
        return MarketStatus.WEEKEND
    if day in holidays:
        return MarketStatus.HOLIDAY

    now = local.time()
    if PRE_OPEN_START <= now < SESSION_OPEN:
        return MarketStatus.PRE_OPEN
    if SESSION_OPEN <= now <= SESSION_CLOSE:
        return MarketStatus.OPEN
    return MarketStatus.CLOSED


def previous_trading_day(day: date, holidays: frozenset[date] = NSE_HOLIDAYS) -> date:
    """The most recent trading day strictly before ``day``."""
    cursor = day - timedelta(days=1)
    # Bounded so a bad holiday table can never loop forever.
    for _ in range(30):
        if is_trading_day(cursor, holidays):
            return cursor
        cursor -= timedelta(days=1)
    return cursor


def trading_days_between(
    start: date, end: date, holidays: frozenset[date] = NSE_HOLIDAYS
) -> list[date]:
    """Trading days in ``(start, end]`` - the sessions a returning user missed."""
    days: list[date] = []
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if is_trading_day(cursor, holidays):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """Naive-UTC open and close instants for a session date."""
    open_ist = datetime.combine(day, SESSION_OPEN, tzinfo=IST)
    close_ist = datetime.combine(day, SESSION_CLOSE, tzinfo=IST)
    return to_utc_naive(open_ist), to_utc_naive(close_ist)


def session_date_for(moment: datetime, holidays: frozenset[date] = NSE_HOLIDAYS) -> date:
    """The session a timestamp belongs to.

    Before a day's open - and on any non-trading day - the relevant session is
    the previous one. This is what makes "show the previous session" correct
    over a weekend without special-casing at every call site.
    """
    local = to_ist(moment)
    day = local.date()
    if not is_trading_day(day, holidays) or local.time() < SESSION_OPEN:
        return previous_trading_day(day, holidays)
    return day


def minute_of_session(moment: datetime) -> int | None:
    """Minutes elapsed since the open, or ``None`` outside a live session.

    This is the bucket key for same-time-of-day volume baselines: comparing
    10:00 volume against 10:00 volume rather than against a full-day average.
    """
    local = to_ist(moment)
    if not is_trading_day(local.date()):
        return None
    now = local.time()
    if not (SESSION_OPEN <= now <= SESSION_CLOSE):
        return None
    opened = datetime.combine(local.date(), SESSION_OPEN, tzinfo=IST)
    return int((local - opened).total_seconds() // 60)
