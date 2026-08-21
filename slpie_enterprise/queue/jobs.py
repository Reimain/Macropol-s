"""What the queue is doing — states, depth, and workers, asked rather than guessed.

A distributed runner that cannot be inspected is a runner nobody trusts. The
question an operator actually has is never "is Celery running"; it is one of:

    is anything stuck?          → a unit STARTED long ago and not finished
    is the queue growing?       → depth against consumers, over time
    did that scan survive?      → the states of one submission's units
    why are there 12 workers?   → §23's answer, from measurements

None of those is answerable from a return value. They are answerable from the
broker and from Celery's own inspection API, and this module is the one place
that asks.

── States are Celery's, not ours ────────────────────────────────────────

`PENDING · RECEIVED · STARTED · RETRY · SUCCESS · FAILURE · REVOKED`. Not
re-spelled, not collapsed, and one of them is a trap worth naming: **`PENDING`
means "this backend has never heard of that id"** — a queued task and a typo
produce the same answer. Celery cannot tell them apart and neither can this
module, so `Job.certain` reports which case a reader is looking at rather than
implying the queue said something it did not.

That is the §25 rule — `INDETERMINATE` never passes as upheld — applied to a
job board.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

#: Celery's own vocabulary, in the order a unit moves through it. Ordered so a
#: board can sort by progress rather than alphabetically, which would put
#: FAILURE first and SUCCESS last.
ORDER = ("PENDING", "RECEIVED", "STARTED", "RETRY", "SUCCESS", "FAILURE", "REVOKED")

#: States a unit will not leave on its own.
TERMINAL = frozenset({"SUCCESS", "FAILURE", "REVOKED"})

#: States that mean somebody should look. `RETRY` is here deliberately: a unit
#: retrying is working as designed *and* is evidence that something it depends
#: on is not.
ATTENTION = frozenset({"FAILURE", "RETRY", "REVOKED"})


class Health(str, Enum):
    """What the queue as a whole is doing, in a word a dashboard can colour."""

    IDLE = "idle"              # nothing queued, nothing running
    WORKING = "working"        # depth and consumers both non-zero
    STARVED = "starved"        # work queued and nobody consuming it
    BACKLOGGED = "backlogged"  # consumers present, depth still climbing
    UNREACHABLE = "unreachable"  # the broker did not answer

    @property
    def wants_attention(self) -> bool:
        return self in (Health.STARVED, Health.BACKLOGGED, Health.UNREACHABLE)


@dataclass(frozen=True, slots=True)
class Job:
    """One unit, and how much the platform actually knows about it."""

    unit: str
    task_id: str
    state: str = "PENDING"
    worker: str = ""
    queue: str = ""
    runtime: float = 0.0
    error: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def certain(self) -> bool:
        """Whether this state means what it appears to mean.

        `PENDING` is Celery's answer for *both* "queued, not started" and "no
        such task id" — an unknown id is indistinguishable from a waiting one.
        Every other state was reported by a worker and is a fact. Rendering the
        two the same way would let a typo look like a job that is about to run.
        """
        return self.state != "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit, "task_id": self.task_id, "state": self.state,
            "worker": self.worker, "queue": self.queue,
            "runtime": round(self.runtime, 3), "error": self.error,
            "terminal": self.terminal, "certain": self.certain,
            "attention": self.state in ATTENTION,
        }

    def __str__(self) -> str:
        where = f" on {self.worker}" if self.worker else ""
        if not self.certain:
            return f"{self.unit}: PENDING — queued, or no such task{where}"
        return f"{self.unit}: {self.state}{where}"


@dataclass(frozen=True, slots=True)
class Queue:
    """One queue's depth and who is draining it."""

    name: str
    depth: int = 0
    unacknowledged: int = 0
    consumers: int = 0

    @property
    def starved(self) -> bool:
        return self.depth > 0 and self.consumers == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "depth": self.depth,
            "unacknowledged": self.unacknowledged,
            "consumers": self.consumers, "starved": self.starved,
        }


@dataclass(frozen=True, slots=True)
class Board:
    """The whole picture, and what it could not see.

    `gaps` is not decoration. Half of what an operator wants is unavailable
    without the broker's management API — depth needs it, and Celery's
    inspection API answers about *workers* rather than about queues. A board
    that showed a depth of zero because nobody could ask would be the worst
    possible answer: a starved queue rendered as an idle one.
    """

    queues: tuple[Queue, ...] = ()
    jobs: tuple[Job, ...] = ()
    workers: tuple[str, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)
    reachable: bool = True

    @property
    def depth(self) -> int:
        return sum(queue.depth for queue in self.queues)

    @property
    def running(self) -> tuple[Job, ...]:
        return tuple(job for job in self.jobs if job.state == "STARTED")

    @property
    def needs_attention(self) -> tuple[Job, ...]:
        return tuple(job for job in self.jobs if job.state in ATTENTION)

    @property
    def health(self) -> Health:
        if not self.reachable:
            return Health.UNREACHABLE
        if any(queue.starved for queue in self.queues):
            return Health.STARVED
        if not self.depth and not self.running:
            return Health.IDLE
        # More waiting than anybody can be working on at once. Not a threshold
        # anyone tuned: it is the point past which the pool is definitionally
        # behind, whatever its size.
        if self.depth > max(len(self.workers), 1) * 2:
            return Health.BACKLOGGED
        return Health.WORKING

    def summary(self) -> str:
        if not self.reachable:
            return "the broker did not answer; nothing below is current"
        parts = [
            f"{self.health.value}",
            f"{self.depth} queued",
            f"{len(self.running)} running",
            f"{len(self.workers)} worker(s)",
        ]
        if self.needs_attention:
            parts.append(f"{len(self.needs_attention)} needing attention")
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "wants_attention": self.health.wants_attention,
            "reachable": self.reachable,
            "depth": self.depth,
            "queues": [queue.to_dict() for queue in self.queues],
            "jobs": [job.to_dict() for job in self.jobs],
            "workers": list(self.workers),
            "running": len(self.running),
            "gaps": list(self.gaps),
            "summary": self.summary(),
        }


def states_of(app: Any, handles: Mapping[str, Any]) -> tuple[Job, ...]:
    """The state of each submitted unit, from the result backend.

    `handles` is `{unit: AsyncResult}` — what `CeleryRunner` holds between
    submitting and collecting. Reading state here rather than inside `run()` is
    what lets a caller watch a scan *while* it runs, which is the only time the
    answer is interesting.
    """
    found: list[Job] = []
    for unit, handle in sorted(handles.items()):
        state = str(getattr(handle, "state", "PENDING") or "PENDING")
        info = getattr(handle, "info", None)
        found.append(Job(
            unit=unit,
            task_id=str(getattr(handle, "id", "") or ""),
            state=state,
            # `info` carries the worker and the start time while a task is
            # STARTED, and the exception once it has failed. Read defensively:
            # it is whatever the task last published, and a task that published
            # a string is not a bug worth crashing a status board over.
            worker=str(info.get("hostname", "")) if isinstance(info, dict) else "",
            error="" if state != "FAILURE" else str(info or "the worker did not say"),
        ))
    return tuple(found)


def inspect_workers(app: Any) -> tuple[tuple[str, ...], tuple[Job, ...], tuple[str, ...]]:
    """Ask the workers what they are doing. Returns `(workers, jobs, gaps)`.

    Celery's inspection is a broadcast with a timeout, so *no reply* and *no
    workers* are the same silence. It is reported as a gap rather than as an
    empty pool, because "nobody is working" and "nobody answered" lead an
    operator to opposite actions.
    """
    try:
        inspector = app.control.inspect(timeout=2.0)
        active = inspector.active() or {}
        registered = inspector.ping() or {}
    except Exception as error:  # noqa: BLE001 - a silent broker is an answer
        return (), (), (
            f"the workers could not be reached ({type(error).__name__}: {error}); "
            f"no job state below is current",
        )

    if not registered:
        return (), (), (
            "no worker answered within 2s. That is either an empty pool or a "
            "broker nobody can reach, and this cannot tell them apart.",
        )

    jobs: list[Job] = []
    for worker, running in sorted(active.items()):
        for task in running or ():
            jobs.append(Job(
                unit=str(task.get("name", "")),
                task_id=str(task.get("id", "")),
                state="STARTED",
                worker=str(worker),
                queue=str((task.get("delivery_info") or {}).get("routing_key", "")),
                runtime=float(task.get("time_start") or 0.0),
            ))
    return tuple(sorted(registered)), tuple(jobs), ()


def board(app: Any, *, queues: Iterable[str] = (), handles: Mapping[str, Any] | None = None) -> Board:
    """Everything the platform can say about the queue right now.

    Depth is deliberately absent unless a management API supplies it: Celery's
    inspection reports what *workers* hold, never what is waiting in a queue
    nobody has consumed yet. Reporting `reserved` as depth would show zero for a
    queue with ten thousand messages and no consumers — the exact situation
    somebody is looking at this board to find.
    """
    workers, running, gaps = inspect_workers(app)
    known = list(running)
    if handles:
        known.extend(states_of(app, handles))

    return Board(
        queues=tuple(Queue(name=name, consumers=len(workers)) for name in sorted(queues)),
        jobs=tuple(known),
        workers=workers,
        gaps=gaps + (
            (
                "queue depth is not shown: it comes from the broker's management "
                "API, and Celery's inspection reports only what workers already "
                "hold. A depth taken from `reserved` would read zero for a queue "
                "with ten thousand waiting messages and no consumers.",
            ) if queues else ()
        ),
        reachable=not gaps,
    )
