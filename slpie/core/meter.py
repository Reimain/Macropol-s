"""Resource measurements, as evidence rather than as a shrug.

§23's `ResourceMeter`. The temptation is to bolt telemetry on the side with its
own opaque logic, and that would make capacity the one decision in this platform
nobody can audit: everything else terminates in a file and a line, and "we are
running twelve workers" would terminate in a shrug.

So a measurement is `RUNTIME_TRACE` evidence at 0.98 — the same kind, the same
ladder, the same `derived_from` walk as a dependency edge. A scaling rule is
then an ordinary `Rule` producing an ordinary `Finding`, and *why twelve* is
answerable by the query that answers everything else.

**This is a hoist, not an invention.** `acceptance.py` already measures wall
time and peak memory per stage with `time.monotonic` and `tracemalloc`, and
compares them against a recorded baseline. Both are stdlib; what was missing is
that the numbers lived in a script rather than in the kernel where a rule could
read them.

`tracemalloc` is off unless asked for, because tracing every allocation costs
roughly a factor of two. A meter with memory disabled still reports time, and
says that memory was not measured rather than reporting zero — a zero here would
read as "used no memory", which is a different and false claim.
"""

from __future__ import annotations

import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation

#: What the meter measures. A closed vocabulary, because a rule matching on a
#: free-form string is a rule that silently stops matching after a rename.
MEASURES = ("duration_ns", "peak_bytes", "units", "failures")


@dataclass(frozen=True, slots=True)
class Measurement:
    """One stage, measured.

    `memory_traced` is separate from `peak_bytes` on purpose. Without it a
    disabled meter reports zero bytes, which reads as "this used no memory" —
    a claim, and a false one. Unmeasured and zero are different answers, which
    is the same distinction the whole platform makes about an empty result.
    """

    stage: str
    duration_ns: int = 0
    peak_bytes: int = 0
    memory_traced: bool = False
    units: int = 0
    failures: int = 0
    started_at: int = 0
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return round(self.duration_ns / 1_000_000, 3)

    def evidence(self, subject: str = "") -> tuple[Evidence, ...]:
        """This measurement, as evidence a rule can reason over.

        `RUNTIME_TRACE` at 0.98 because it is exactly what that kind means: not
        inferred from a manifest, not read off a config — observed, while it was
        actually happening. The `uri` is the stage rather than a file, which is
        the honest address for something that has no line to point at.
        """
        where = SourceLocation(uri=f"slpie://meter/{subject or self.stage}")
        pieces = [Evidence(
            kind=EvidenceKind.RUNTIME_TRACE,
            location=where,
            extractor="slpie.core.meter",
            extractor_version="1",
            excerpt=(
                f"{self.stage}: {self.duration_ms}ms over {self.units} unit(s), "
                f"{self.failures} failed"
            ),
            observed_at=self.started_at,
            labels={
                "stage": self.stage,
                "duration_ns": str(self.duration_ns),
                "units": str(self.units),
                "failures": str(self.failures),
                **{key: str(value) for key, value in self.labels.items()},
            },
        )]
        if self.memory_traced:
            pieces.append(Evidence(
                kind=EvidenceKind.RUNTIME_TRACE,
                location=where,
                extractor="slpie.core.meter",
                extractor_version="1",
                excerpt=f"{self.stage}: peak {self.peak_bytes} bytes",
                observed_at=self.started_at,
                labels={"stage": self.stage, "peak_bytes": str(self.peak_bytes)},
            ))
        return tuple(pieces)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "stage": self.stage,
            "duration_ns": self.duration_ns,
            "duration_ms": self.duration_ms,
            "units": self.units,
            "failures": self.failures,
            "started_at": self.started_at,
        }
        # Absent rather than zero when it was not measured.
        if self.memory_traced:
            body["peak_bytes"] = self.peak_bytes
        return body


class ResourceMeter:
    """Measures stages and keeps what it measured.

    Nothing is emitted anywhere: this records, and a caller decides whether the
    numbers become evidence on a node, a line in a report, or nothing at all.
    A meter that wrote to the ledger on its own would put measurement inside
    every code path that might want to measure, which is how telemetry ends up
    impossible to turn off.
    """

    def __init__(self, *, memory: bool = False) -> None:
        self.memory = memory
        self._taken: list[Measurement] = []

    @property
    def measurements(self) -> tuple[Measurement, ...]:
        return tuple(self._taken)

    def measure(self, stage: str, **labels: str) -> "_Stage":
        """Context manager around one stage. `with meter.measure("scan"): ...`"""
        return _Stage(self, stage, labels)

    def record(self, measurement: Measurement) -> Measurement:
        self._taken.append(measurement)
        return measurement

    def evidence(self, subject: str = "") -> tuple[Evidence, ...]:
        return tuple(
            piece for item in self._taken for piece in item.evidence(subject)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_traced": self.memory,
            "stages": [item.to_dict() for item in self._taken],
            "duration_ns": sum(item.duration_ns for item in self._taken),
        }

    def __len__(self) -> int:
        return len(self._taken)

    def __iter__(self) -> Iterator[Measurement]:
        return iter(self.measurements)


class _Stage:
    """One measured stage. Records even when the body raises.

    A stage that failed is exactly the one whose cost is interesting, and a
    measurement that only survives the happy path is a measurement of the happy
    path.
    """

    __slots__ = ("_meter", "_stage", "_labels", "_started", "_wall", "_owned",
                 "units", "failures")

    def __init__(self, meter: ResourceMeter, stage: str, labels: Mapping[str, str]):
        self._meter = meter
        self._stage = stage
        self._labels = dict(labels)
        self._started = 0
        self._wall = 0
        self._owned = False
        self.units = 0
        self.failures = 0

    def __enter__(self) -> "_Stage":
        self._started = time.time_ns()
        self._wall = time.perf_counter_ns()
        if self._meter.memory and not tracemalloc.is_tracing():
            # Only stop what we started. Another measurer — `acceptance.py`, a
            # profiler, a test — may already be tracing, and turning theirs off
            # on the way out would silently blind them.
            tracemalloc.start()
            self._owned = True
        return self

    def __exit__(self, *_exception: Any) -> None:
        elapsed = time.perf_counter_ns() - self._wall
        peak = 0
        traced = False
        if self._meter.memory and tracemalloc.is_tracing():
            _current, peak = tracemalloc.get_traced_memory()
            traced = True
            if self._owned:
                tracemalloc.stop()
        self._meter.record(Measurement(
            stage=self._stage, duration_ns=elapsed, peak_bytes=peak,
            memory_traced=traced, units=self.units, failures=self.failures,
            started_at=self._started, labels=self._labels,
        ))
