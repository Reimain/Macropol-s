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


def application(broker: str = "", backend: str = "", *, eager: bool = False) -> Any:
    """A configured Celery app.

    `eager` runs everything in-process and is how the *shape* of this adapter is
    exercised without a broker. It is not a substitute for the distributed
    proof: a green test under `task_always_eager` shows the protocol is honoured
    and shows nothing about a worker on another machine. Said here rather than
    left for a tick to imply.
    """
    from celery import Celery

    app = Celery(
        "slpie",
        broker=broker or "memory://",
        backend=backend or "cache+memory://",
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
        task_acks_late=True,
    )
    return app


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
        for unit, work in units:
            if dispatchable(work):
                sent.append((unit, work.apply_async(), True))
                self.distributed += 1
            else:
                sent.append((unit, work, False))
                self.local += 1

        results: list[Result[T]] = []
        for unit, handle, remote in sent:
            results.append(
                self._collect(unit, handle) if remote else _here(unit, handle)
            )
        return tuple(results)

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
