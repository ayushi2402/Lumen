"""Since You Were Away.

Two problems to solve honestly.

**Which baseline?** "Since you last checked" is ambiguous, so LUMEN keeps three
independent marks plus an optional manual one and resolves between them by
explicit precedence:

1. a manual "start from here" the user chose,
2. the last digest they actually reviewed,
3. the last time they opened the app,
4. the last market observation they were shown.

A manual choice beats an inferred one, and *reviewing* a digest is stronger
evidence of having seen something than merely opening the app. Critically, the
baseline is **not** advanced by rendering the digest - only by an explicit
review - so refreshing the page cannot destroy what the user came back to read.

**How long were they away?** Over a single gap the answer is a short list. Over
a weekend or a holiday it is several sessions, and a flat list buries the
structure, so absences spanning more than one session return an overall summary
plus a day-by-day breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import MarketEventRow, User, utcnow
from app.market.calendar import (
    previous_trading_day,
    session_bounds,
    session_date_for,
    trading_days_between,
)
from app.services import events as events_service


# Reopening within this window is treated as the same visit, so refreshing or
# navigating does not count as "leaving and coming back".
VISIT_GAP_MINUTES = 30


class BaselineSource:
    MANUAL = "manual"
    REVIEWED_DIGEST = "reviewed_digest"
    APP_OPEN = "app_open"
    LAST_OBSERVATION = "last_observation"
    FIRST_USE = "first_use"
    REPLAY_SESSION = "replay_session"


_SOURCE_LABEL = {
    BaselineSource.MANUAL: "since the point you chose",
    BaselineSource.REVIEWED_DIGEST: "since you last reviewed your digest",
    BaselineSource.APP_OPEN: "since you last opened LUMEN",
    BaselineSource.LAST_OBSERVATION: "since the last update you saw",
    BaselineSource.FIRST_USE: "since the previous session",
    BaselineSource.REPLAY_SESSION: "since this scenario started",
}


@dataclass(frozen=True)
class DigestBaseline:
    at: datetime
    source: str
    label: str

    def to_dict(self) -> dict:
        return {"at": self.at.isoformat(), "source": self.source, "label": self.label}


@dataclass
class DaySummary:
    """One trading session's worth of change, for drilldown."""

    session_date: date
    events: list[MarketEventRow] = field(default_factory=list)
    top_symbols: list[str] = field(default_factory=list)
    headline: str = ""


@dataclass
class Digest:
    baseline: DigestBaseline
    now: datetime
    sessions_missed: int
    is_multi_session: bool
    summary: str
    events: list[MarketEventRow] = field(default_factory=list)
    top_events: list[MarketEventRow] = field(default_factory=list)
    remainder_count: int = 0
    days: list[DaySummary] = field(default_factory=list)


def resolve_baseline(user: User, now: datetime | None = None) -> DigestBaseline:
    """Pick the baseline by explicit precedence. Never silently advanced."""
    now = now or utcnow()

    for source, value in (
        (BaselineSource.MANUAL, user.manual_baseline_at),
        (BaselineSource.REVIEWED_DIGEST, user.last_digest_reviewed_at),
        (BaselineSource.APP_OPEN, user.previous_visit_at),
        (BaselineSource.LAST_OBSERVATION, user.last_observation_seen_at),
    ):
        if value is not None:
            return DigestBaseline(at=value, source=source, label=_SOURCE_LABEL[source])

    # First visit: anchor to the start of the previous session so there is
    # something meaningful to show rather than an empty digest.
    previous = previous_trading_day(session_date_for(now))
    opened, _ = session_bounds(previous)
    return DigestBaseline(
        at=opened, source=BaselineSource.FIRST_USE, label=_SOURCE_LABEL[BaselineSource.FIRST_USE]
    )


def mark_app_opened(db: Session, user: User, now: datetime | None = None) -> None:
    """Record a visit without destroying the digest.

    The baseline reads ``previous_visit_at``, which only moves when the user
    genuinely leaves and returns - a gap longer than ``VISIT_GAP_MINUTES``.
    Refreshing the dashboard, or loading it twice in a row, keeps the same
    baseline and therefore the same digest.
    """
    moment = now or utcnow()
    last_open = user.last_app_open_at

    if last_open is None:
        # First ever visit: leave previous_visit_at unset so the first-use
        # anchor applies and the user sees the previous session rather than
        # an empty digest.
        pass
    elif (moment - last_open) > timedelta(minutes=VISIT_GAP_MINUTES):
        # A real return: the previous visit ended at the last open we saw.
        user.previous_visit_at = last_open

    user.last_app_open_at = moment
    db.flush()


def mark_digest_reviewed(db: Session, user: User, now: datetime | None = None) -> None:
    """Advance the baseline. Only ever called on an explicit user action."""
    moment = now or utcnow()
    user.last_digest_reviewed_at = moment
    user.manual_baseline_at = None  # an explicit review supersedes a manual mark
    db.flush()


def set_manual_baseline(
    db: Session, user: User, moment: datetime | None = None
) -> DigestBaseline:
    """"Start from here" - the user pins their own baseline."""
    at = moment or utcnow()
    user.manual_baseline_at = at
    db.flush()
    return DigestBaseline(
        at=at, source=BaselineSource.MANUAL, label=_SOURCE_LABEL[BaselineSource.MANUAL]
    )


def replay_baseline(session_start: datetime) -> DigestBaseline:
    """Baseline for a replay: the moment the scenario began.

    Inside a demo, "since you were away" can only sensibly mean "since this
    scenario started". The user's real-world baseline is months away from the
    scenario's virtual dates, so using it would report that nothing changed
    while an event sits on screen.
    """
    return DigestBaseline(
        at=session_start,
        source=BaselineSource.REPLAY_SESSION,
        label=_SOURCE_LABEL[BaselineSource.REPLAY_SESSION],
    )


def build_digest(
    db: Session,
    user: User,
    symbols: list[str],
    scope: str = "live",
    now: datetime | None = None,
    top_n: int = 3,
    baseline_override: DigestBaseline | None = None,
) -> Digest:
    """Assemble what changed since the user's baseline."""
    now = now or utcnow()
    baseline = baseline_override or resolve_baseline(user, now)

    rows = events_service.events_for_symbols(db, symbols, scope=scope, since=baseline.at)
    dismissed = events_service.dismissed_event_ids(db, user)
    visible = [r for r in rows if r.id not in dismissed]

    ranked = sorted(visible, key=lambda r: (-r.peak_score, r.last_updated_at))
    top = ranked[:top_n]

    missed_days = trading_days_between(
        session_date_for(baseline.at), session_date_for(now)
    )
    sessions_missed = len(missed_days)
    multi = sessions_missed > 1

    days: list[DaySummary] = []
    if multi:
        for day in missed_days:
            day_events = [r for r in visible if r.session_date == day]
            day_events.sort(key=lambda r: -r.peak_score)
            days.append(
                DaySummary(
                    session_date=day,
                    events=day_events,
                    top_symbols=[r.instrument.symbol for r in day_events[:3]],
                    headline=_day_headline(day, day_events),
                )
            )

    return Digest(
        baseline=baseline,
        now=now,
        sessions_missed=sessions_missed,
        is_multi_session=multi,
        summary=_summary_text(visible, sessions_missed, baseline),
        events=ranked,
        top_events=top,
        remainder_count=max(0, len(ranked) - len(top)),
        days=days,
    )


def _summary_text(
    rows: list[MarketEventRow], sessions_missed: int, baseline: DigestBaseline
) -> str:
    """Generate the summary from the actual events. Never a hardcoded sentence."""
    if not rows:
        if sessions_missed > 1:
            return (
                f"Nothing meaningful changed across the last {sessions_missed} "
                "sessions in the stocks you follow."
            )
        return "Nothing meaningful changed in the stocks you follow."

    count = len(rows)
    noun = "thing" if count == 1 else "things"
    symbols = sorted({r.instrument.symbol for r in rows})
    named = ", ".join(symbols[:3])
    more = f" and {len(symbols) - 3} more" if len(symbols) > 3 else ""

    critical = sum(1 for r in rows if r.severity == "Critical")
    high = sum(1 for r in rows if r.severity == "High Attention")
    market_wide = sum(1 for r in rows if r.is_market_wide)

    text = f"{count} {noun} changed {baseline.label}"
    if sessions_missed > 1:
        text += f", across {sessions_missed} trading sessions"
    text += f": {named}{more}."

    emphasis: list[str] = []
    if critical:
        emphasis.append(f"{critical} critical")
    if high:
        emphasis.append(f"{high} high attention")
    if emphasis:
        text += f" {' and '.join(emphasis)}."
    if market_wide:
        text += (
            f" {market_wide} of these tracked the broader market rather than "
            "being specific to the stock."
        )
    return text


def _day_headline(day: date, rows: list[MarketEventRow]) -> str:
    if not rows:
        return f"{day.isoformat()}: nothing meaningful."
    top = rows[0]
    extra = f", plus {len(rows) - 1} more" if len(rows) > 1 else ""
    return f"{day.isoformat()}: {top.headline} ({top.severity}){extra}."
