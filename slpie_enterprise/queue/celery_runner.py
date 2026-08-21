"""Celery behind `slpie.core.tasks.TaskRunner`, and the honest limit of that seam.

── What the protocol asks for, and what a wire can carry ──────────────

`TaskRunner.run` takes `(name, callable)` pairs. In process that is exactly
right — it is the smallest thing that expresses "a unit of work" and it needs no
registry. Across a wire it is **impossible**: a closure cannot be serialised,
and the only serializers that come close are `pickle` and `dill`, which turn any
queue an attacker can write to into remote code execution. That is not a
trade-off worth taking for a scan.

The first version of this file ignored the problem and Celery answered
immediately — `Object of type function is not JSON serializable`, in eager mode,
before a broker was even involved. The good kind of failure.

So the runner does the only honest thing:

* a unit whose work is a **registered Celery task** is dispatched to a worker;
* a unit whose work is a **plain callable** is run here, in process, and
  **counted**. It is not silently degraded and it is not refused: the answer is
  still correct, it simply did not distribute, and `gaps()` says how many.

That is the treatment §3 gives a refused capability and §27 gives a missing
binary, applied to a runner. An adapter that quietly ran everything locally
would be an `InlineRunner` wearing Celery's name, and the operator would never
learn why adding workers changed nothing.

── Order is part of the contract ──────────────────────────────────────

    Discovery merges observations by subject and confidence follows from
    corroboration, so two runs over the same estate must produce the same graph
    — and a runner that returned results as they finished would make the merge
    order depend on which worker was quickest.

Celery makes completion order the convenient one to read. Taking it would make
the snapshot digest — which §12 promises is a function of the inputs — depend on
which worker was least loaded. Results are collected by **submission index**,
and a test asserts it against units that deliberately finish out of order.

A failed unit is a `Result`, never a raise: a scan where one plugin died is a
scan with a gap, and the other ninety-nine results are still worth having.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence, TypeVar

from slpie.core.tasks import Result

T = TypeVar("T")

#: How long a unit may take before the runner stops waiting. Generous, because
#: a scan of a large repository legitimately takes minutes and a timeout tuned
#: for a unit test would fail on real work.
TIMEOUT = 1800.0


#: The broker this adapter is built for, and the environment variable that
#: points at one. RabbitMQ rather than Redis, and the difference is not taste:
#:
#: * **Redis is a data store being used as a queue.** A worker that dies holding
#:   a task leaves it in a list nobody is watching; recovering it needs
#:   `visibility_timeout`, which is a *guess* at how long a task should take.
#:   A scan legitimately takes minutes, so the guess is either too short — and
#:   the task is redelivered while it is still running — or too long, and a dead
#:   worker's task sits idle for that long.
#: * **RabbitMQ has acknowledgements.** A task is delivered, held unacknowledged
#:   while it runs, and requeued the instant the connection drops. With
#:   `task_acks_late` that is exactly the semantics a scan needs: a worker
#:   killed mid-unit loses nothing, which is §23's deallocation protocol getting
#:   its guarantee from the broker rather than from a timer.
#: * **It can be *asked*.** Queue depth, consumer count and unacknowledged
#:   counts are first-class, which is what makes §23's elasticity curve
#:   measurable rather than modelled.
BROKER_ENV = "SLPIE_BROKER_URL"
DEFAULT_BROKER = "amqp://guest:guest@localhost:5672//"

#: Results go to a *store*, never back through the broker. RabbitMQ's RPC
#: backend creates a queue per client and loses results when the client
#: disconnects, which is the wrong shape for a scan somebody starts and reads
#: an hour later.
BACKEND_ENV = "SLPIE_RESULT_BACKEND"
DEFAULT_BACKEND = "redis://localhost:6379/0"


def application(broker: str = "", backend: str = "", *, eager: bool = False) -> Any:
    """A configured Celery app, pointed at a real broker by default.

    `eager` runs everything in-process and is how the *shape* of this adapter is
    exercised without a broker. It is not a substitute for the distributed
    proof: a green test under `task_always_eager` shows the protocol is honoured
    and shows nothing about a worker on another machine. Said here rather than
    left for a tick to imply.

    The defaults are a **local RabbitMQ and a local Redis**, not an in-memory
    transport. An in-memory default is a broker that works perfectly on one
    machine and silently distributes nothing, which is the same failure as a
    runner that quietly ran everything locally — the thing this module's
    docstring already refuses.
    """
    import os

    from celery import Celery

    app = Celery(
        "slpie",
        broker=broker or os.environ.get(BROKER_ENV) or DEFAULT_BROKER,
        backend=backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND,
    )
    app.conf.update(
        task_always_eager=eager,
        task_eager_propagates=False,
        # JSON only. `pickle` would let this accept a closure and would make any
        # queue an attacker can write to a remote-code-execution surface — the
        # exact trade this module refuses in its docstring.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Fair dispatch. Without it a worker takes a batch up front and a long
        # unit blocks the short ones queued behind it while another sits idle.
        worker_prefetch_multiplier=1,
        # Acknowledge *after* the unit finishes, so a worker that dies mid-scan
        # returns its task to the queue rather than losing it. This is the line
        # that makes RabbitMQ's delivery guarantee reach the scan.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # A task that has started is worth knowing about. Without this a unit
        # is `PENDING` — indistinguishable from one nobody ever queued — right
        # up until it finishes, and "queued" and "running for six minutes" are
        # exactly the two states an operator needs to tell apart.
        task_track_started=True,
        # Events, so `slpie queue jobs` and Flower see the same worker stream.
        worker_send_task_events=True,
        task_send_sent_event=True,
        # Results are not kept forever. A scan's result is read once and the
        # ledger is the durable record; a backend that grew without bound would
        # be a second store nobody prunes.
        result_expires=RESULT_TTL,
        broker_connection_retry_on_startup=True,
    )
    return app


#: How long a finished unit's result stays readable. A day: long enough that an
#: operator who started a scan before a weekend can still read it on Monday
#: morning, short enough that the backend is not a database.
RESULT_TTL = 86_400


def dispatchable(work: Any) -> bool:
    """Whether this unit can actually leave the process.

    A Celery task carries `delay`/`apply_async` and a registered `name`. A bare
    function carries neither, and no amount of configuration makes it portable.
    """
    return callable(getattr(work, "apply_async", None)) and bool(getattr(work, "name", ""))


@dataclass(slots=True)
class CeleryRunner:
    """Fan dispatchable units across workers; run the rest here and say so."""

    app: Any
    name: str = "celery"
    timeout: float = TIMEOUT
    #: Units that could not leave the process, since construction. Cumulative
    #: on purpose: a scan that distributed nothing across ten runs is the same
    #: finding as one that distributed nothing once, and only a total shows it.
    local: int = field(default=0, init=False)
    distributed: int = field(default=0, init=False)
    #: Live handles, by unit, for the batch currently in flight. Held so the
    #: queue can be *asked what it is doing* while it does it — which is the
    #: only time the answer is interesting. `run()` blocks until everything is
    #: collected, so without this the states would only ever be readable after
    #: they had all stopped changing.
    inflight: dict = field(default_factory=dict, init=False)

    def run(
        self, units: Sequence[tuple[str, Callable[[], T]]],
    ) -> tuple[Result[T], ...]:
        if not units:
            return ()

        # Submitted first, waited on second. Submitting and waiting per unit
        # would serialise the whole batch through one worker and make the queue
        # decorative — the mistake that makes a distributed runner slower than
        # the inline one it replaced.
        sent: list[tuple[str, Any, bool]] = []
        self.inflight = {}
        for unit, work in units:
            if dispatchable(work):
                handle = work.apply_async()
                sent.append((unit, handle, True))
                self.inflight[unit] = handle
                self.distributed += 1
            else:
                sent.append((unit, work, False))
                self.local += 1

        results: list[Result[T]] = []
        for unit, handle, remote in sent:
            results.append(
                self._collect(unit, handle) if remote else _here(unit, handle)
            )
            # Dropped as it is collected, so `states()` shows what is *still*
            # outstanding rather than a batch that finished an hour ago.
            self.inflight.pop(unit, None)
        return tuple(results)

    def states(self) -> tuple[Any, ...]:
        """Where each in-flight unit has got to, from the result backend."""
        from .jobs import states_of

        return states_of(self.app, self.inflight)

    def board(self, *, queues: Sequence[str] = ()) -> Any:
        """The whole queue: workers, what they are running, and what is missing.

        Asked of the workers rather than inferred from this object's counters.
        A runner reporting its own view of the world would answer confidently
        about workers that died ten minutes ago.
        """
        from .jobs import board

        return board(self.app, queues=queues, handles=self.inflight)

    def _collect(self, unit: str, handle: Any) -> Result[Any]:
        started = time.perf_counter_ns()
        try:
            answer = handle.get(timeout=self.timeout, propagate=False)
        except Exception as error:                  # noqa: BLE001 - reported
            return Result(
                unit=unit, error=f"{type(error).__name__}: {error}",
                duration_ns=time.perf_counter_ns() - started,
            )
        if isinstance(answer, BaseException):
            # `propagate=False` hands the exception back as a value, which is
            # what keeps one dead unit from taking the batch with it.
            return Result(
                unit=unit, error=f"{type(answer).__name__}: {answer}",
                duration_ns=time.perf_counter_ns() - started,
            )
        return Result(
            unit=unit, value=answer,
            duration_ns=time.perf_counter_ns() - started,
        )

    def gaps(self) -> tuple[str, ...]:
        """What this runner could not do, in the words a console shows.

        Empty when everything distributed. A runner reporting nothing and a
        runner that distributed nothing must not look the same from outside.
        """
        if not self.local:
            return ()
        return (
            f"{self.local} unit(s) ran in this process rather than on a worker: "
            f"their work was a plain callable, and a closure cannot cross a "
            f"process boundary. Submit a registered Celery task to distribute.",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "distributed": self.distributed,
            "local": self.local,
            "inflight": len(self.inflight),
            "broker": str(getattr(self.app.conf, "broker_url", "") or ""),
            "gaps": list(self.gaps()),
        }


def _here(unit: str, work: Any) -> Result[Any]:
    """A unit that could not leave, run correctly where it is.

    Identical to `InlineRunner`'s body on purpose: the answer must not depend on
    whether the unit happened to be distributable, only on where it ran.
    """
    started = time.perf_counter_ns()
    try:
        value = work() if callable(work) else work
    except Exception as error:                      # noqa: BLE001 - reported
        return Result(
            unit=unit, error=f"{type(error).__name__}: {error}",
            duration_ns=time.perf_counter_ns() - started,
        )
    return Result(
        unit=unit, value=value, duration_ns=time.perf_counter_ns() - started,
    )
