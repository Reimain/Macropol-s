"""ContextFlow — the versioned spine every subsystem writes to.

History is a hash chain. Each append links the previous version, the event id,
and the event's content digest, so a version string is a cryptographic claim
about the entire prefix of history that produced it.

Two rules make concurrent, self-modifying agents safe:

1. Every event declares the ``base_version`` it was computed against.
2. An event may only land if nothing it touches changed since that base.

Rule 2 is what the codebase calls the timetravel guard. An agent that rolled
back, went away, and came back with work computed on a version that history has
since abandoned cannot silently reapply it — the append raises
:class:`TimetravelConflict` and the conflict goes to a policymaker instead of
into the code.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..errors import ContextFlowError, RollbackError, TimetravelConflict
from ..ids import digest, new_id
from .clock import LogicalClock, Ordering, compare, merge
from .event import EventKind, KeyringEvent
from .keyring import MetaKeyring, normalize_path

GENESIS = "v0" + "0" * 30

Listener = Callable[[KeyringEvent, str], None]


@dataclass(frozen=True, slots=True)
class Version:
    """One link in the chain."""

    id: str
    event_id: str
    index: int
    parent: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.id


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A named, rollback-addressable point in history."""

    name: str
    version: str
    index: int
    event_id: str
    labels: Mapping[str, str] = field(default_factory=dict)


def _chain(parent: str, event_id: str, event_digest: str) -> str:
    return "v" + digest({"p": parent, "e": event_id, "d": event_digest}, length=15)


class ContextFlow:
    """An append-only, replayable, rollback-aware context.

    The flow owns three things: the ordered event log, the version chain over
    it, and the :class:`MetaKeyring` materialized by replaying the live prefix.
    Everything else in Gratimos is a producer or consumer of these.
    """

    def __init__(self, actor: str = "kernel", *, name: str = "") -> None:
        self.name = name or new_id("flow")
        self.actor = actor
        self.clock = LogicalClock(actor)
        self.keyring = MetaKeyring()
        self._events: list[KeyringEvent] = []
        self._versions: list[Version] = []
        self._index_of: dict[str, int] = {GENESIS: -1}
        self._by_id: dict[str, int] = {}
        self._superseded: set[str] = set()
        self._checkpoints: dict[str, Checkpoint] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()

    # -- introspection ---------------------------------------------------

    @property
    def head(self) -> str:
        return self._versions[-1].id if self._versions else GENESIS

    @property
    def depth(self) -> int:
        """Number of live (non-superseded) events on the spine."""
        return len(self._events)

    def events(self, *, kind: EventKind | None = None, path: str | None = None) -> list[KeyringEvent]:
        target = normalize_path(path) if path else None
        return [
            e
            for e in self._events
            if (kind is None or e.kind is kind)
            and (target is None or target in {normalize_path(p) for p in e.paths})
        ]

    def event(self, event_id: str) -> KeyringEvent:
        idx = self._by_id.get(event_id)
        if idx is None:
            raise ContextFlowError(f"unknown event: {event_id!r}")
        return self._events[idx]

    def version_of(self, event_id: str) -> str:
        idx = self._by_id.get(event_id)
        if idx is None:
            raise ContextFlowError(f"unknown event: {event_id!r}")
        return self._versions[idx].id

    def is_ancestor(self, version: str, of: str | None = None) -> bool:
        """True when ``version`` is on the live chain leading to ``of``."""
        of = of or self.head
        base = self._index_of.get(version)
        target = self._index_of.get(of)
        if base is None or target is None:
            return False
        return base <= target

    def superseded(self) -> list[str]:
        return sorted(self._superseded)

    def checkpoints(self) -> list[Checkpoint]:
        return sorted(self._checkpoints.values(), key=lambda c: c.index)

    # -- append ----------------------------------------------------------

    def append(self, event: KeyringEvent, *, rebase: bool = True) -> str:
        """Land an event and return the version it produced.

        ``rebase`` lets an event that was computed against an older-but-still-
        live version land anyway, provided nothing it touches moved in the
        meantime. Set it to ``False`` to demand strict head-only appends.
        """
        with self._lock:
            base = event.base_version or self.head
            self._guard(event, base, rebase=rebase)

            self.clock.observe(event.lamport, event.vector)
            lamport, vector = self.clock.tick()
            if event.vector:
                vector = merge(vector, event.vector)
            landed = event.stamped(lamport=lamport, vector=vector, base_version=base)

            parent = self.head
            version = _chain(parent, landed.id, landed.digest)
            self._events.append(landed)
            self._versions.append(
                Version(id=version, event_id=landed.id, index=len(self._events) - 1, parent=parent)
            )
            self._index_of[version] = len(self._events) - 1
            self._by_id[landed.id] = len(self._events) - 1
            self.keyring.apply(landed, version)

            if landed.kind is EventKind.CHECKPOINT:
                name = str(landed.payload.get("name") or landed.id)
                self._checkpoints[name] = Checkpoint(
                    name=name,
                    version=version,
                    index=len(self._events) - 1,
                    event_id=landed.id,
                    labels=dict(landed.labels),
                )

            self._notify(landed, version)
            return version

    def emit(
        self,
        kind: EventKind,
        *,
        actor: str | None = None,
        paths: Sequence[str] = (),
        payload: Mapping[str, Any] | None = None,
        base_version: str = "",
        labels: Mapping[str, str] | None = None,
        storage: Any = None,
        parents: Sequence[str] = (),
    ) -> KeyringEvent:
        """Build, append, and return an event in one call."""
        event = KeyringEvent(
            kind=kind,
            actor=actor or self.actor,
            paths=tuple(normalize_path(p) for p in paths),
            payload=dict(payload or {}),
            base_version=base_version,
            labels=dict(labels or {}),
            storage=storage,
            parents=tuple(parents),
        )
        version = self.append(event)
        return self._events[self._index_of[version]]

    def _guard(self, event: KeyringEvent, base: str, *, rebase: bool) -> None:
        """Enforce the timetravel rules before an event is allowed to land."""
        if base == self.head:
            return
        if base not in self._index_of:
            raise TimetravelConflict(base, self.head, event.actor)
        if not rebase:
            raise TimetravelConflict(base, self.head, event.actor)

        touched = {normalize_path(p) for p in event.paths}
        if not touched:
            return
        moved: set[str] = set()
        for later in self._events[self._index_of[base] + 1 :]:
            if later.inert:
                continue
            moved.update(normalize_path(p) for p in later.paths)
        overlap = touched & moved
        if overlap:
            raise TimetravelConflict(base, self.head, event.actor)

        # Concurrent by the vector clock but path-disjoint is a legal merge.
        if event.vector and compare(event.vector, self.clock.snapshot()) is Ordering.CONCURRENT:
            return

    # -- checkpoints and rollback ----------------------------------------

    def checkpoint(self, name: str, **labels: str) -> Checkpoint:
        """Mark a point history can be rewound to."""
        self.emit(EventKind.CHECKPOINT, payload={"name": name}, labels=labels)
        return self._checkpoints[name]

    def rollback(self, target: str, *, actor: str | None = None, reason: str = "") -> str:
        """Rewind live state to a checkpoint name or version.

        Nothing is deleted. The undone events are marked superseded, the keyring
        is rebuilt by replaying the surviving prefix, and a ``ROLLBACK`` event is
        appended *forward* so the trace records that the rewind happened. Any
        agent still holding a superseded version now fails the timetravel guard.
        """
        with self._lock:
            checkpoint = self._checkpoints.get(target)
            version = checkpoint.version if checkpoint else target
            index = self._index_of.get(version)
            if index is None:
                raise RollbackError(f"no such checkpoint or version: {target!r}")
            if version in self._superseded:
                raise RollbackError(f"target already superseded: {target!r}")

            undone = self._events[index + 1 :]
            if not undone:
                return self.head

            undone_ids = [e.id for e in undone]
            for event, ver in zip(undone, self._versions[index + 1 :]):
                self._superseded.add(ver.id)
                self._index_of.pop(ver.id, None)
                self._by_id.pop(event.id, None)
            dropped = set(undone_ids)
            self._checkpoints = {
                n: c for n, c in self._checkpoints.items() if c.event_id not in dropped
            }

            self._events = self._events[: index + 1]
            self._versions = self._versions[: index + 1]
            self._replay()

            self.emit(
                EventKind.ROLLBACK,
                actor=actor or self.actor,
                payload={
                    "target": target,
                    "to_version": version,
                    "undone": undone_ids,
                    "undone_count": len(undone_ids),
                    "reason": reason,
                },
                paths=sorted({p for e in undone for p in e.paths}),
            )
            return self.head

    def _replay(self) -> None:
        self.keyring.clear()
        for event, ver in zip(self._events, self._versions):
            self.keyring.apply(event, ver.id)

    # -- listeners -------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self, event: KeyringEvent, version: str) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, version)
            except Exception:  # noqa: BLE001 - a bad listener must not corrupt history
                continue

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actor": self.actor,
            "head": self.head,
            "depth": self.depth,
            "events": [e.to_dict() for e in self._events],
            "versions": [{"id": v.id, "event_id": v.event_id, "parent": v.parent} for v in self._versions],
            "superseded": sorted(self._superseded),
            "checkpoints": {
                name: {"version": c.version, "event_id": c.event_id, "labels": dict(c.labels)}
                for name, c in self._checkpoints.items()
            },
            "keyring": self.keyring.to_dict(),
        }

    @classmethod
    def replay(cls, events: Iterable[Mapping[str, Any]], *, actor: str = "kernel", name: str = "") -> "ContextFlow":
        """Rebuild a flow from a serialized event log."""
        flow = cls(actor=actor, name=name)
        for raw in events:
            event = KeyringEvent.from_dict(raw)
            flow.append(event.stamped(lamport=event.lamport, vector=event.vector, base_version=flow.head))
        return flow

    def __iter__(self) -> Iterator[KeyringEvent]:
        return iter(list(self._events))

    def __len__(self) -> int:
        return len(self._events)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ContextFlow {self.name} head={self.head[:10]} depth={self.depth}>"
