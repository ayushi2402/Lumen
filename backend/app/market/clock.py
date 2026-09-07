"""Clocks.

Replay must produce identical results at 3am on a Sunday as during a live
session, so nothing in the replay path may read the system clock. Both clocks
implement the same tiny interface; callers take a ``Clock`` and never call
``datetime.now()`` themselves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """Anything that can report the current instant as naive UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time. Used for live data only."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


class VirtualClock:
    """A clock advanced explicitly, never by elapsed real time.

    Time moves only when ``advance()`` or ``set()`` is called, so a replay
    stepped by a user and a replay stepped by a test produce byte-identical
    sequences. ``speed`` is a multiplier applied to requested advances - it
    scales how much virtual time one step represents, and does not introduce
    any dependency on real elapsed time.
    """

    def __init__(self, start: datetime, speed: int = 1) -> None:
        self._now = start
        self.start = start
        self.speed = max(1, speed)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        """Move forward by ``seconds`` of scenario time, scaled by ``speed``."""
        self._now = self._now + timedelta(seconds=seconds * self.speed)
        return self._now

    def set(self, moment: datetime) -> datetime:
        """Jump to an absolute instant, e.g. when seeking within a scenario."""
        self._now = moment
        return self._now

    def elapsed(self) -> timedelta:
        return self._now - self.start
