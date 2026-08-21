"""Causal ordering primitives.

Wall clock time cannot order events produced by several agents at once, and it
cannot detect concurrency. A Lamport counter gives us a total order for display;
the vector clock gives us the *happens-before* relation we actually need to tell
"this is a legitimate follow-up" from "this is a stale branch trying to land".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

VectorClock = Mapping[str, int]


class Ordering(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    EQUAL = "equal"
    CONCURRENT = "concurrent"


def compare(left: VectorClock, right: VectorClock) -> Ordering:
    """Partial order over vector clocks."""
    left_smaller = right_smaller = False
    for actor in set(left) | set(right):
        a, b = left.get(actor, 0), right.get(actor, 0)
        if a < b:
            left_smaller = True
        elif a > b:
            right_smaller = True
    if left_smaller and right_smaller:
        return Ordering.CONCURRENT
    if left_smaller:
        return Ordering.BEFORE
    if right_smaller:
        return Ordering.AFTER
    return Ordering.EQUAL


def merge(left: VectorClock, right: VectorClock) -> dict[str, int]:
    """Pointwise maximum — the join of two causal histories."""
    return {actor: max(left.get(actor, 0), right.get(actor, 0)) for actor in set(left) | set(right)}


def happens_before(left: VectorClock, right: VectorClock) -> bool:
    return compare(left, right) is Ordering.BEFORE


@dataclass(slots=True)
class LogicalClock:
    """Per-actor clock. One instance per participant in a ContextFlow."""

    actor: str
    lamport: int = 0
    vector: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def tick(self) -> tuple[int, dict[str, int]]:
        """Advance for a locally generated event."""
        with self._lock:
            self.lamport += 1
            self.vector[self.actor] = self.vector.get(self.actor, 0) + 1
            return self.lamport, dict(self.vector)

    def observe(self, lamport: int, vector: VectorClock) -> None:
        """Fold in a remote event so our next tick dominates it."""
        with self._lock:
            self.lamport = max(self.lamport, lamport)
            self.vector = merge(self.vector, vector)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.vector)
