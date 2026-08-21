"""Durable, append-only trace of a ContextFlow.

The flow holds history in memory; the journal makes it survive the process. It
is a JSON-lines file so it can be tailed, diffed, and shipped to a log sink
without a reader library. Writes are fsync-guarded on close so a crash leaves a
truncated-but-valid prefix rather than a corrupt record.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..contextflow import ContextFlow, EventKind, KeyringEvent
from ..errors import ContextFlowError
from ..ids import iso, now_ns


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """One journal line: an event plus the version it produced."""

    version: str
    event: KeyringEvent
    written_ns: int

    def to_json(self) -> str:
        return json.dumps(
            {"version": self.version, "written_at": iso(self.written_ns), "event": self.event.to_dict()},
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> "TraceRecord":
        data = json.loads(line)
        return cls(
            version=data["version"],
            event=KeyringEvent.from_dict(data["event"]),
            written_ns=int(data.get("written_ns", now_ns())),
        )


class Journal:
    """Append-only sink for flow events.

    Attach it to a flow with :meth:`attach` and every subsequent append is
    persisted. Use :meth:`load` to rebuild a flow from disk.
    """

    def __init__(self, path: str | os.PathLike[str], *, autoflush: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.autoflush = autoflush
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._detach: Any = None
        self._count = 0

    # -- writing ---------------------------------------------------------

    def write(self, event: KeyringEvent, version: str) -> None:
        record = TraceRecord(version=version, event=event, written_ns=now_ns())
        with self._lock:
            self._handle.write(record.to_json() + "\n")
            self._count += 1
            if self.autoflush:
                self._handle.flush()

    def attach(self, flow: ContextFlow) -> "Journal":
        """Mirror every append on ``flow`` into this journal."""
        if self._detach is not None:
            raise ContextFlowError("journal is already attached to a flow")
        self._detach = flow.subscribe(self.write)
        return self

    def detach(self) -> None:
        if self._detach is not None:
            self._detach()
            self._detach = None

    def flush(self) -> None:
        with self._lock:
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        self.detach()
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                os.fsync(self._handle.fileno())
                self._handle.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- reading ---------------------------------------------------------

    def records(self) -> Iterator[TraceRecord]:
        """Yield every well-formed record, skipping a torn trailing line."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield TraceRecord.from_json(line)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def load(self, *, actor: str = "kernel", name: str = "") -> ContextFlow:
        """Rebuild a flow by replaying the journal from the beginning."""
        return ContextFlow.replay((r.event.to_dict() for r in self.records()), actor=actor, name=name)

    def tail(self, count: int = 20) -> list[TraceRecord]:
        records = list(self.records())
        return records[-count:]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        actors: dict[str, int] = {}
        total = 0
        for record in self.records():
            counts[record.event.kind.value] = counts.get(record.event.kind.value, 0) + 1
            actors[record.event.actor] = actors.get(record.event.actor, 0) + 1
            total += 1
        return {"path": str(self.path), "records": total, "by_kind": counts, "by_actor": actors}

    def find(self, *, kind: EventKind | None = None, actor: str = "", path: str = "") -> list[TraceRecord]:
        out = []
        for record in self.records():
            event = record.event
            if kind is not None and event.kind is not kind:
                continue
            if actor and event.actor != actor:
                continue
            if path and path not in event.paths:
                continue
            out.append(record)
        return out

    def __len__(self) -> int:
        return self._count

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Journal {self.path} written={self._count}>"


def replay_into(flow: ContextFlow, records: Mapping[str, Any] | Iterator[TraceRecord]) -> ContextFlow:
    """Fold journal records into an existing flow (used when resuming a run)."""
    for record in records:  # type: ignore[union-attr]
        event = record.event if isinstance(record, TraceRecord) else KeyringEvent.from_dict(record)
        flow.append(event.stamped(lamport=event.lamport, vector=event.vector, base_version=flow.head))
    return flow
