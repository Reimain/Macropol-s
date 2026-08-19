"""The seam a worker pool implements, and the default that needs none.

§14's `TaskRunner`. A scan is naturally one unit per bound element — the
elements are independent, they are already enumerated, and the plugin sandbox
(`slpie/plugins/sandbox.py`) already makes each unit independently retryable
because it is per-subprocess. What is missing is somewhere to *put* those units
other than this process.

**The default changes nothing, and that is the point.** `InlineRunner` submits
by calling, in order, on this thread. Under it `Scanner.scan()` behaves exactly
as it did — same order, same errors, same report — so the entire existing suite
standing unchanged is the proof that the seam is inert until something is
plugged into it. That is the same argument §30 made for `gateway=None`, and it
is worth repeating because it is the only kind of proof that scales: a thousand
tests that did not change say more than ten written to check that nothing did.

Ring 0, stdlib. This declares a protocol and implements the boring case;
`slpie_enterprise/` supplies the Celery adapter, and the kernel never learns it
exists (invariant 9).

**Deallocation is the part that loses data** (§23). A worker removed mid-scan
drops observations the ledger never recorded, and the graph silently loses
whatever it had read. So `Result` carries which unit produced it and whether it
completed, and a runner that cannot finish a unit must say so rather than
returning a short list that looks like a clean scan of a smaller estate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Protocol, Sequence, TypeVar

from ..errors import SlpieError

T = TypeVar("T")


class TaskError(SlpieError):
    """A unit of work could not be scheduled, or a runner would not drain."""


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    """What one unit produced, or why it produced nothing.

    `failed` is a first-class outcome rather than an exception, because a scan
    of forty elements where one refused is a scan of thirty-nine plus a named
    gap — not a failure. Raising would throw away the thirty-nine, which is the
    behaviour that makes people run scans they do not trust.
    """

    unit: str
    value: T | None = None
    error: str = ""
    duration_ns: int = 0

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit, "ok": self.ok, "error": self.error,
            "duration_ns": self.duration_ns,
        }


class TaskRunner(Protocol):
    """Somewhere to put independent units of work.

    Deliberately tiny. A richer protocol — priorities, cancellation, progress —
    would have to be honoured by the in-process default too, and the default's
    honest implementation of a priority queue is "ignore it", which is a
    protocol lying about what it does.
    """

    name: str

    def run(
        self, units: Sequence[tuple[str, Callable[[], T]]],
    ) -> tuple[Result[T], ...]:
        """Run every unit and return every outcome, in submission order.

        Order is part of the contract. Discovery merges observations by subject
        and confidence follows from corroboration, so two runs over the same
        estate must produce the same graph — and a runner that returned results
        as they finished would make the merge order depend on which worker was
        quickest.
        """
        ...


@dataclass(slots=True)
class InlineRunner:
    """The default: submit by calling, here, now.

    No threads. Discovery is IO-bound and threading it would be a real
    improvement, and it is deliberately not done here — the point of this file
    is a *seam*, and adding concurrency to the default would mean the "nothing
    changed" proof no longer holds and every existing test would be re-verifying
    a new execution model rather than the old one.
    """

    name: str = "inline"

    def run(
        self, units: Sequence[tuple[str, Callable[[], T]]],
    ) -> tuple[Result[T], ...]:
        results: list[Result[T]] = []
        for unit, work in units:
            started = time.perf_counter_ns()
            try:
                value = work()
            except Exception as error:            # noqa: BLE001 - reported, not raised
                results.append(Result(
                    unit=unit, error=f"{type(error).__name__}: {error}",
                    duration_ns=time.perf_counter_ns() - started,
                ))
                continue
            results.append(Result(
                unit=unit, value=value,
                duration_ns=time.perf_counter_ns() - started,
            ))
        return tuple(results)


@dataclass(slots=True)
class RecordingRunner:
    """An `InlineRunner` that remembers what it was asked to do.

    Not a mock — it really runs the work. It exists so a test can assert that
    `scan` submitted one unit per element rather than one unit for everything,
    which is the difference between a seam a worker pool can use and a seam that
    only looks like one.
    """

    name: str = "recording"
    submitted: list[str] = field(default_factory=list)
    batches: int = 0

    def run(
        self, units: Sequence[tuple[str, Callable[[], T]]],
    ) -> tuple[Result[T], ...]:
        self.batches += 1
        self.submitted.extend(unit for unit, _ in units)
        return InlineRunner().run(units)


def default_runner() -> TaskRunner:
    """The runner used when nobody supplies one."""
    return InlineRunner()
