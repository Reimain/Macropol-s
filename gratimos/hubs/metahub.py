"""MetaHub: the registry of what the kernel believes about its data.

Shapes are versioned here, not overwritten. Registering an observation that
differs from the current belief produces a new revision plus the diff that got
there — which is simultaneously the input to code generation, the trigger for a
migration, and the evidence a policymaker needs to decide whether the change is
safe to adopt.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..ids import new_id, now_ns
from ..meta import DataShape, ShapeChange, Wrapped


@dataclass(frozen=True, slots=True)
class ShapeRevision:
    """One accepted belief about an entity's structure."""

    entity: str
    revision: int
    shape: DataShape
    changes: tuple[ShapeChange, ...] = ()
    source: str = ""
    event_id: str = ""
    id: str = field(default_factory=lambda: new_id("rev"))
    ts_ns: int = field(default_factory=now_ns)

    @property
    def digest(self) -> str:
        return self.shape.digest

    @property
    def breaking(self) -> bool:
        return any(change.breaking for change in self.changes)

    @property
    def path(self) -> str:
        return f"shape.{self.entity}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity": self.entity,
            "revision": self.revision,
            "digest": self.digest,
            "breaking": self.breaking,
            "source": self.source,
            "event_id": self.event_id,
            "changes": [c.to_dict() for c in self.changes],
            "shape": self.shape.to_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        mark = "!" if self.breaking else ""
        return f"<ShapeRevision {self.entity} r{self.revision}{mark} changes={len(self.changes)}>"


@dataclass(frozen=True, slots=True)
class LineageEdge:
    """A derivation: one payload produced from another."""

    child: str
    parent: str
    operation: str = ""
    ts_ns: int = field(default_factory=now_ns)


class MetaHub:
    """Versioned shape registry plus the lineage graph over payloads."""

    def __init__(self, flow: ContextFlow | None = None) -> None:
        self.flow = flow
        self._revisions: dict[str, list[ShapeRevision]] = {}
        self._lineage: list[LineageEdge] = []
        self._parents: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    # -- shapes ----------------------------------------------------------

    def register(
        self,
        shape: DataShape,
        *,
        entity: str = "",
        source: str = "",
        actor: str = "metahub",
        widen: bool = True,
    ) -> ShapeRevision:
        """Record an observation, returning the revision it produced.

        When the observation matches the current belief, the existing revision
        is returned unchanged — re-observing the same data must not inflate the
        history. When it differs and ``widen`` is set, the new belief is the
        *union* of old and new, so a field seen only in one batch survives.
        """
        name = entity or shape.name
        with self._lock:
            history = self._revisions.setdefault(name, [])
            current = history[-1] if history else None

            if current is None:
                revision = ShapeRevision(entity=name, revision=1, shape=shape.rename(name), source=source)
                history.append(revision)
                self._emit(revision, actor)
                return revision

            if current.shape.digest == shape.digest:
                return current

            proposed = current.shape.merge(shape).rename(name) if widen else shape.rename(name)
            if proposed.digest == current.shape.digest:
                return current

            revision = ShapeRevision(
                entity=name,
                revision=current.revision + 1,
                shape=proposed,
                changes=tuple(current.shape.diff(proposed)),
                source=source or current.source,
            )
            history.append(revision)
            self._emit(revision, actor)
            return revision

    def observe(self, payload: Wrapped, *, actor: str = "metahub") -> ShapeRevision:
        """Register the shape carried by a wrapper, and record its lineage."""
        revision = self.register(
            payload.shape, entity=payload.name, source=payload.provenance.source, actor=actor
        )
        for parent in payload.provenance.lineage:
            self.link(payload.id, parent, operation=str(payload.provenance.labels.get("op", "")))
        return revision

    def current(self, entity: str) -> ShapeRevision | None:
        history = self._revisions.get(entity)
        return history[-1] if history else None

    def shape(self, entity: str) -> DataShape | None:
        revision = self.current(entity)
        return revision.shape if revision else None

    def history(self, entity: str) -> list[ShapeRevision]:
        return list(self._revisions.get(entity, ()))

    def entities(self) -> list[str]:
        return sorted(self._revisions)

    def diff(self, entity: str, shape: DataShape) -> list[ShapeChange]:
        """What would change if this observation were adopted."""
        current = self.current(entity)
        return current.shape.diff(shape) if current else []

    def breaking_entities(self) -> list[str]:
        return [e for e in self.entities() if (r := self.current(e)) and r.breaking]

    def revisions(self) -> list[ShapeRevision]:
        return [r for history in self._revisions.values() for r in history]

    # -- lineage ---------------------------------------------------------

    def link(self, child: str, parent: str, *, operation: str = "") -> LineageEdge:
        edge = LineageEdge(child=child, parent=parent, operation=operation)
        with self._lock:
            self._lineage.append(edge)
            self._parents.setdefault(child, []).append(parent)
        return edge

    def ancestors(self, payload_id: str, *, depth: int = 16) -> list[str]:
        """Walk back through derivations, breadth-first, cycle-safe."""
        seen: set[str] = set()
        frontier = list(self._parents.get(payload_id, ()))
        out: list[str] = []
        while frontier and depth > 0:
            depth -= 1
            nxt: list[str] = []
            for node in frontier:
                if node in seen:
                    continue
                seen.add(node)
                out.append(node)
                nxt.extend(self._parents.get(node, ()))
            frontier = nxt
        return out

    def lineage(self) -> list[LineageEdge]:
        return list(self._lineage)

    # -- reporting -------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        """The shape of the whole context, as a policymaker would read it."""
        return {
            entity: {
                "revision": revision.revision,
                "digest": revision.digest,
                "fields": len(revision.shape),
                "rows_observed": revision.shape.rows,
                "breaking": revision.breaking,
                "source": revision.source,
                "field_types": {f.ident: f.tag.value for f in revision.shape.fields},
            }
            for entity in self.entities()
            if (revision := self.current(entity)) is not None
        }

    def report(self) -> dict[str, Any]:
        return {
            "entities": len(self._revisions),
            "revisions": sum(len(h) for h in self._revisions.values()),
            "breaking": self.breaking_entities(),
            "lineage_edges": len(self._lineage),
        }

    def _emit(self, revision: ShapeRevision, actor: str) -> None:
        if self.flow is None:
            return
        event = self.flow.emit(
            EventKind.SHAPE,
            actor=actor,
            paths=[revision.path],
            payload={
                "entity": revision.entity,
                "revision": revision.revision,
                "shape_digest": revision.digest,
                "breaking": revision.breaking,
                "changes": [c.to_dict() for c in revision.changes],
                "fields": len(revision.shape),
            },
            labels={"source": revision.source} if revision.source else {},
        )
        object.__setattr__(revision, "event_id", event.id)

    def __contains__(self, entity: object) -> bool:
        return str(entity) in self._revisions

    def __iter__(self) -> Iterator[ShapeRevision]:
        return iter(r for e in self.entities() if (r := self.current(e)) is not None)

    def __len__(self) -> int:
        return len(self._revisions)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<MetaHub entities={len(self._revisions)} revisions={sum(len(h) for h in self._revisions.values())}>"
