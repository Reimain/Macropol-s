"""A controllable clock, so bitemporal behaviour is testable.

The graph has two time axes: when something was true, and when we learned it.
Those only mean anything if time can be *moved*, and a test that has to sleep to
prove a supersession is a test nobody runs. So the simulator supplies a clock
that advances on command.

The live path uses :func:`time.time_ns` through the same interface. There is no
branch — the clock is injected, which is the same shape the binding layer uses
for everything else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Time, as the platform reads it."""

    def now(self) -> int:
        """Nanoseconds since the epoch."""
        ...


class SystemClock:
    """Real time. What a live target runs on."""

    def now(self) -> int:
        return time.time_ns()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return "<SystemClock>"


#: A fixed, recognisable starting point: 2024-01-01T00:00:00Z in nanoseconds.
EPOCH = 1_704_067_200_000_000_000

SECOND = 1_000_000_000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR


@dataclass(slots=True)
class ControlledClock:
    """Time that only moves when told to.

    Two properties make it useful rather than merely deterministic: it never
    returns the same value twice for successive reads at the same instant, so
    ordering by timestamp stays stable; and it can jump by days, so "this
    dependency has been unmaintained for two years" is a condition a scenario
    can create in a millisecond.
    """

    instant: int = EPOCH
    reads: int = 0

    def now(self) -> int:
        # A monotonic nudge per read. Without it, two events recorded in the
        # same tick would be indistinguishable and any ordering assertion would
        # be testing insertion order by accident.
        self.reads += 1
        return self.instant + self.reads

    def advance(self, nanoseconds: int) -> int:
        if nanoseconds < 0:
            raise ValueError("time does not run backwards; supersede instead")
        self.instant += nanoseconds
        self.reads = 0
        return self.instant

    def advance_days(self, days: float) -> int:
        return self.advance(int(days * DAY))

    def set(self, instant: int) -> int:
        """Jump to an absolute instant — for replaying a historical scenario."""
        self.instant = instant
        self.reads = 0
        return self.instant

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ControlledClock at {self.instant}>"
