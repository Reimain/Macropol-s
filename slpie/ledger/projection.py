"""Projections — read models, always rebuildable from sequence 0.

A projection holds no truth of its own. It is a function of the ledger, folded
one event at a time, and that is what makes a schema change routine: drop the
read model, replay the ledger, and the new shape exists. The same operation
covers recovery after a crash and adding a view that nobody had thought of when
the events were written.

The rule projections must not break: **never derive state from anything but the
events**. A projector that reads the clock, the filesystem, or another read model
stops being a pure fold, and its rebuild silently stops matching its live state —
which is the failure mode CQRS is supposed to eliminate, arriving through the
back door.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..core.events import DomainEvent, EventKind
from .record import LedgerRecord


class Projection(ABC):
    """Base class for every read model.

    Subclasses implement :meth:`apply` for the events they care about and
    :meth:`reset` to return to empty. :meth:`rebuild` is then free.
    """

    #: Empty means "every kind" — a projection that filters declares it here so
    #: the bus can skip delivering what it would ignore.
    kinds: frozenset[EventKind] = frozenset()

    def __init__(self, name: str = "") -> None:
        self.name = name or type(self).__name__.lower()
        self.last_sequence = 0
        self.applied = 0

    @abstractmethod
    def apply(self, event: DomainEvent) -> None:
        """Fold one event into the read model."""

    @abstractmethod
    def reset(self) -> None:
        """Return to the empty state a rebuild starts from."""

    def handle(self, event: DomainEvent) -> None:
        """Bus entry point — filters, folds, and tracks position."""
        if self.kinds and event.kind not in self.kinds:
            return
        self.apply(event)
        self.applied += 1
        self.last_sequence = max(self.last_sequence, event.sequence)

    def rebuild(self, records: Iterable[LedgerRecord]) -> int:
        """Discard everything and refold the whole history.

        The recovery story and the migration story, in one method.
        """
        self.reset()
        self.last_sequence = 0
        self.applied = 0
        count = 0
        for record in records:
            self.handle(record.event)
            count += 1
        return count

    def catch_up(self, records: Iterable[LedgerRecord]) -> int:
        """Fold only what this projection has not seen — the incremental path."""
        count = 0
        for record in records:
            if record.sequence <= self.last_sequence:
                continue
            self.handle(record.event)
            count += 1
        return count

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "last_sequence": self.last_sequence,
            "applied": self.applied,
            "kinds": sorted(k.value for k in self.kinds) or ["*"],
        }


@dataclass(slots=True)
class Attachment:
    """One element's station registration, as the projection sees it."""

    element: str
    kind: str = ""
    attached: bool = False
    attached_at: int = 0
    detached_at: int = 0
    last_heartbeat: int = 0
    granted: tuple[str, ...] = ()
    refused: Mapping[str, str] = field(default_factory=dict)   # capability -> reason
    handovers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element, "kind": self.kind, "attached": self.attached,
            "attached_at": self.attached_at, "detached_at": self.detached_at,
            "last_heartbeat": self.last_heartbeat, "granted": list(self.granted),
            "refused": dict(self.refused), "handovers": self.handovers,
        }


class StationProjection(Projection):
    """Who is attached, what they let us see, and what they refused.

    The refusals are the valuable half. They become the gaps attached to every
    answer whose confidence they limit, which is what lets the platform say "I
    could not read the payments repo" instead of quietly reporting a smaller
    ecosystem than the one that exists.
    """

    kinds = frozenset({
        EventKind.ELEMENT_ATTACHED, EventKind.ELEMENT_DETACHED,
        EventKind.ELEMENT_HANDED_OVER, EventKind.CAPABILITY_GRANTED,
        EventKind.CAPABILITY_REFUSED, EventKind.HEARTBEAT_RECORDED,
    })

    def __init__(self) -> None:
        super().__init__("station")
        self.elements: dict[str, Attachment] = {}

    def reset(self) -> None:
        self.elements.clear()

    def apply(self, event: DomainEvent) -> None:
        element = self.elements.setdefault(event.subject, Attachment(element=event.subject))

        if event.kind is EventKind.ELEMENT_ATTACHED:
            element.attached = True
            element.attached_at = event.occurred_at
            element.detached_at = 0
            element.kind = str(event.payload.get("kind", element.kind))
        elif event.kind is EventKind.ELEMENT_DETACHED:
            element.attached = False
            element.detached_at = event.occurred_at
        elif event.kind is EventKind.ELEMENT_HANDED_OVER:
            element.handovers += 1
        elif event.kind is EventKind.CAPABILITY_GRANTED:
            capability = str(event.payload.get("capability", ""))
            if capability and capability not in element.granted:
                element.granted = (*element.granted, capability)
            # A capability granted after a refusal supersedes it.
            element.refused = {
                k: v for k, v in element.refused.items() if k != capability
            }
        elif event.kind is EventKind.CAPABILITY_REFUSED:
            capability = str(event.payload.get("capability", ""))
            if capability:
                element.refused = {
                    **element.refused,
                    capability: str(event.payload.get("reason", "no reason given")),
                }
                element.granted = tuple(c for c in element.granted if c != capability)
        elif event.kind is EventKind.HEARTBEAT_RECORDED:
            element.last_heartbeat = event.occurred_at

    # -- queries ---------------------------------------------------------

    @property
    def attached(self) -> tuple[Attachment, ...]:
        return tuple(e for e in self.elements.values() if e.attached)

    def refusals(self) -> tuple[tuple[str, str, str], ...]:
        """Every refused capability as (element, capability, reason)."""
        return tuple(
            (element.element, capability, reason)
            for element in self.elements.values()
            for capability, reason in sorted(element.refused.items())
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: element.to_dict() for name, element in sorted(self.elements.items())}


class FindingProjection(Projection):
    """Open findings, keyed by finding id.

    Resolution and suppression remove a finding from the *view* while leaving it
    in the ledger — which is exactly the distinction a compliance auditor needs
    between "this was never a problem" and "this was a problem and we waived it".
    """

    kinds = frozenset({
        EventKind.FINDING_RAISED, EventKind.FINDING_RESOLVED, EventKind.FINDING_SUPPRESSED,
    })

    def __init__(self) -> None:
        super().__init__("findings")
        self.open: dict[str, dict[str, Any]] = {}
        self.resolved: dict[str, int] = {}       # finding id -> sequence resolved at
        self.suppressed: dict[str, str] = {}     # finding id -> reason

    def reset(self) -> None:
        self.open.clear()
        self.resolved.clear()
        self.suppressed.clear()

    def apply(self, event: DomainEvent) -> None:
        finding_id = str(event.payload.get("finding_id", event.subject))

        if event.kind is EventKind.FINDING_RAISED:
            # A finding raised again after resolution is genuinely open again.
            self.resolved.pop(finding_id, None)
            self.open[finding_id] = {
                **dict(event.payload),
                "finding_id": finding_id,
                "subject": event.subject,
                "raised_at": event.occurred_at,
                "sequence": event.sequence,
            }
        elif event.kind is EventKind.FINDING_RESOLVED:
            self.open.pop(finding_id, None)
            self.resolved[finding_id] = event.sequence
        elif event.kind is EventKind.FINDING_SUPPRESSED:
            self.suppressed[finding_id] = str(event.payload.get("reason", ""))
            entry = self.open.pop(finding_id, None)
            if entry is not None:
                entry["suppressed"] = True
                entry["suppression_reason"] = self.suppressed[finding_id]

    def by_severity(self, severity: str) -> tuple[dict[str, Any], ...]:
        return tuple(f for f in self.open.values() if f.get("severity") == severity)

    def blocking(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            f for f in self.open.values() if f.get("severity") in ("high", "critical")
        )


class SourceProjection(Projection):
    """Which source files produced which evidence — the invalidation index.

    Phase 16's incremental recomputation is a lookup in here: a file changes,
    this says exactly which evidence goes stale, and nothing else has to be
    rescanned.
    """

    kinds = frozenset({EventKind.EVIDENCE_RECORDED, EventKind.SOURCE_CHANGED})

    def __init__(self) -> None:
        super().__init__("sources")
        self.evidence_by_uri: dict[str, set[str]] = {}
        self.digests: dict[str, str] = {}

    def reset(self) -> None:
        self.evidence_by_uri.clear()
        self.digests.clear()

    def apply(self, event: DomainEvent) -> None:
        uri = str(event.payload.get("uri", event.subject))

        if event.kind is EventKind.EVIDENCE_RECORDED:
            evidence_id = str(event.payload.get("evidence_id", ""))
            if evidence_id:
                self.evidence_by_uri.setdefault(uri, set()).add(evidence_id)
            if digest := str(event.payload.get("content_digest", "")):
                self.digests[uri] = digest
        elif event.kind is EventKind.SOURCE_CHANGED:
            # The evidence drawn from the old content is stale by definition.
            self.evidence_by_uri.pop(uri, None)
            self.digests[uri] = str(event.payload.get("digest", ""))

    def stale_evidence(self, uris: Iterable[str]) -> tuple[str, ...]:
        stale: set[str] = set()
        for uri in uris:
            stale |= self.evidence_by_uri.get(uri, set())
        return tuple(sorted(stale))

    def changed(self, digests: Mapping[str, str]) -> tuple[str, ...]:
        """Which of these files differ from what we last recorded."""
        return tuple(
            sorted(uri for uri, digest in digests.items() if self.digests.get(uri) != digest)
        )


class CountingProjection(Projection):
    """Event counts by kind. The cheapest possible health check on a rebuild."""

    def __init__(self) -> None:
        super().__init__("counts")
        self.counts: dict[str, int] = {}

    def reset(self) -> None:
        self.counts.clear()

    def apply(self, event: DomainEvent) -> None:
        self.counts[event.kind.value] = self.counts.get(event.kind.value, 0) + 1

    def total(self) -> int:
        return sum(self.counts.values())


class ProjectionSet:
    """A group of read models rebuilt and kept current together.

    Rebuilding them in one pass matters: replaying a large ledger once per
    projection is the difference between a rebuild being routine and being
    something nobody dares do.
    """

    def __init__(self, *projections: Projection) -> None:
        self._projections: dict[str, Projection] = {p.name: p for p in projections}

    def add(self, projection: Projection) -> Projection:
        self._projections[projection.name] = projection
        return projection

    def get(self, name: str) -> Projection | None:
        return self._projections.get(name)

    def __getitem__(self, name: str) -> Projection:
        return self._projections[name]

    def __iter__(self):
        return iter(self._projections.values())

    def __len__(self) -> int:
        return len(self._projections)

    def handle(self, event: DomainEvent) -> None:
        for projection in self._projections.values():
            projection.handle(event)

    def rebuild(self, records: Sequence[LedgerRecord]) -> dict[str, int]:
        """One pass over history, every projection refolded."""
        for projection in self._projections.values():
            projection.reset()
            projection.last_sequence = 0
            projection.applied = 0
        for record in records:
            for projection in self._projections.values():
                projection.handle(record.event)
        return {name: p.applied for name, p in self._projections.items()}

    def catch_up(self, records: Sequence[LedgerRecord]) -> dict[str, int]:
        return {
            name: projection.catch_up(records)
            for name, projection in self._projections.items()
        }

    def subscribe_to(self, bus: Any) -> None:
        """Register every projection with an event bus."""
        for projection in self._projections.values():
            bus.subscribe(projection.name, projection.handle, kinds=projection.kinds)

    def status(self) -> dict[str, dict[str, Any]]:
        return {name: p.status() for name, p in self._projections.items()}


def rebuildable(
    projection: Projection, records: Sequence[LedgerRecord], *, snapshot: Callable[[Projection], Any]
) -> bool:
    """Whether replaying from zero reproduces the projection's current state.

    The property every read model must have, checked directly rather than
    assumed. ``snapshot`` renders a projection to something comparable — usually
    a dict.
    """
    before = snapshot(projection)
    projection.rebuild(records)
    return snapshot(projection) == before
