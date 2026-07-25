"""Rollback scopes and the timetravel guard.

Self-modifying agents fail in a specific way: an agent reads the world, spends
time thinking, and by the time it writes, the world has moved — often because
*it* rolled the world back. Committing that stale work is how a generator
silently reintroduces code that was already retracted.

The guard makes that failure loud. An agent pins the version it read, and every
write it attempts is checked against what has landed since. A conflict becomes a
:class:`Conflict` a policymaker can resolve, never a silent overwrite.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind, KeyringEvent
from ..contextflow.keyring import normalize_path
from ..errors import RollbackError, TimetravelConflict
from ..ids import iso, new_id


@dataclass(frozen=True, slots=True)
class Conflict:
    """A rejected write, described well enough to resolve it without the flow."""

    actor: str
    base_version: str
    head_version: str
    paths: tuple[str, ...]
    contenders: tuple[str, ...]
    kind: EventKind
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "base_version": self.base_version,
            "head_version": self.head_version,
            "paths": list(self.paths),
            "contenders": list(self.contenders),
            "kind": self.kind.value,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"conflict[{self.actor}] on {', '.join(self.paths)} ({self.base_version[:10]} vs {self.head_version[:10]})"


@dataclass(slots=True)
class ScopeResult:
    """What a :meth:`RollbackManager.scope` block did."""

    name: str
    entered_version: str
    exited_version: str
    rolled_back: bool = False
    error: str = ""
    events: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rolled_back and not self.error


class TimetravelGuard:
    """A pinned read position an agent writes against.

    Hold one for the duration of a unit of work. ``commit`` refuses anything
    computed against a version that no longer leads to head.
    """

    def __init__(self, flow: ContextFlow, actor: str) -> None:
        self.flow = flow
        self.actor = actor
        self.base_version = flow.head
        self.id = new_id("guard")
        self.conflicts: list[Conflict] = []

    def refresh(self) -> str:
        """Re-pin to current head, accepting whatever landed in between."""
        self.base_version = self.flow.head
        return self.base_version

    @property
    def stale(self) -> bool:
        return self.base_version != self.flow.head

    def landed_since(self) -> list[KeyringEvent]:
        if not self.flow.is_ancestor(self.base_version):
            return []
        after = False
        out: list[KeyringEvent] = []
        for event in self.flow:
            if after:
                out.append(event)
            if self.flow.version_of(event.id) == self.base_version:
                after = True
        return out

    def commit(
        self,
        kind: EventKind,
        *,
        paths: Sequence[str] = (),
        payload: Mapping[str, Any] | None = None,
        labels: Mapping[str, str] | None = None,
        storage: Any = None,
    ) -> KeyringEvent:
        """Write against the pinned base, raising on a genuine conflict."""
        try:
            event = self.flow.emit(
                kind,
                actor=self.actor,
                paths=paths,
                payload=payload,
                labels=labels,
                storage=storage,
                base_version=self.base_version,
            )
        except TimetravelConflict as exc:
            conflict = self._describe(kind, paths, exc)
            self.conflicts.append(conflict)
            raise
        self.base_version = self.flow.head
        return event

    def _describe(self, kind: EventKind, paths: Sequence[str], exc: TimetravelConflict) -> Conflict:
        touched = {normalize_path(p) for p in paths}
        contenders = [
            e.id
            for e in self.flow
            if not e.inert and touched & {normalize_path(p) for p in e.paths}
        ]
        detail = (
            "base version was superseded by a rollback"
            if not self.flow.is_ancestor(exc.base_version)
            else "another actor wrote the same keyring paths"
        )
        return Conflict(
            actor=self.actor,
            base_version=exc.base_version,
            head_version=exc.head_version,
            paths=tuple(sorted(touched)),
            contenders=tuple(contenders[-5:]),
            kind=kind,
            detail=detail,
        )


class RollbackManager:
    """Checkpoint, scope, and rewind a flow.

    A scope is the everyday tool: run risky work inside one and any exception
    rewinds the flow to the point it started, leaving a ``ROLLBACK`` event that
    explains why.
    """

    def __init__(self, flow: ContextFlow) -> None:
        self.flow = flow
        self.history: list[ScopeResult] = []

    def guard(self, actor: str | None = None) -> TimetravelGuard:
        return TimetravelGuard(self.flow, actor or self.flow.actor)

    def checkpoint(self, name: str, **labels: str) -> str:
        return self.flow.checkpoint(name, **labels).version

    @contextlib.contextmanager
    def scope(self, name: str, *, actor: str | None = None, reraise: bool = True) -> Iterator[ScopeResult]:
        """Run a block atomically with respect to the flow."""
        marker = f"{name}@{iso()}"
        entered = self.flow.checkpoint(marker, scope=name).version
        result = ScopeResult(name=name, entered_version=entered, exited_version=entered)
        before = self.flow.depth
        try:
            yield result
        except Exception as exc:  # noqa: BLE001 - the point is to catch everything
            result.error = f"{type(exc).__name__}: {exc}"
            result.rolled_back = True
            result.events = [e.id for e in list(self.flow)[before:]]
            self.flow.rollback(marker, actor=actor, reason=result.error)
            result.exited_version = self.flow.head
            self.history.append(result)
            if reraise:
                raise
            return
        result.events = [e.id for e in list(self.flow)[before:]]
        result.exited_version = self.flow.head
        self.history.append(result)

    def rewind(self, target: str, *, actor: str | None = None, reason: str = "manual rewind") -> str:
        """Explicit rewind to a checkpoint or version."""
        return self.flow.rollback(target, actor=actor, reason=reason)

    def undo_last(self, count: int = 1, *, actor: str | None = None) -> str:
        """Rewind the last ``count`` live events."""
        events = list(self.flow)
        if count <= 0 or count > len(events):
            raise RollbackError(f"cannot undo {count} of {len(events)} events")
        target_index = len(events) - count - 1
        if target_index < 0:
            raise RollbackError("cannot undo past the genesis of the flow")
        version = self.flow.version_of(events[target_index].id)
        return self.flow.rollback(version, actor=actor, reason=f"undo last {count}")

    def conflicts(self) -> list[Conflict]:
        return [c for result in self.history for c in getattr(result, "conflicts", [])]

    def report(self) -> dict[str, Any]:
        return {
            "head": self.flow.head,
            "depth": self.flow.depth,
            "checkpoints": [c.name for c in self.flow.checkpoints()],
            "superseded": len(self.flow.superseded()),
            "scopes": [
                {"name": r.name, "ok": r.ok, "rolled_back": r.rolled_back, "error": r.error}
                for r in self.history
            ],
        }
