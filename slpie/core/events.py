"""Domain events — the write side of CQRS, and the only source of truth.

The ledger *is* the event store. Every graph table, every projection, every
report is a read model rebuilt from this stream, which means two things at once:
recovery and schema migration are the same operation, and the complete
historical evolution the specification demands is not a feature that had to be
added — it is what the storage already is.

Every event carries ``causation_id``, chaining it to the event that caused it.
That is provenance one level beneath the evidence layer: evidence explains why a
*claim* is true, causation explains why the platform *did* something. It is also
what makes the UI's live feed intelligible rather than a firehose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from ..domain.identity import digest


class EventKind(str, Enum):
    """The closed taxonomy of things that can happen.

    Closed on purpose: a projector switches on this, and an open vocabulary
    would mean a projector could silently ignore an event kind it had never
    heard of, leaving a read model quietly wrong.
    """

    # target and environment
    TARGET_CHANGED = "target_changed"
    MANIFEST_DECLARED = "manifest_declared"
    # station
    ELEMENT_ATTACHED = "element_attached"
    ELEMENT_DETACHED = "element_detached"
    ELEMENT_HANDED_OVER = "element_handed_over"
    CAPABILITY_GRANTED = "capability_granted"
    CAPABILITY_REFUSED = "capability_refused"
    HEARTBEAT_RECORDED = "heartbeat_recorded"
    # discovery
    OBSERVATION_RECORDED = "observation_recorded"
    EVIDENCE_RECORDED = "evidence_recorded"
    SOURCE_CHANGED = "source_changed"
    # graph
    NODE_ASSERTED = "node_asserted"
    NODE_RETIRED = "node_retired"
    EDGE_ASSERTED = "edge_asserted"
    EDGE_RETIRED = "edge_retired"
    # reasoning
    ENRICHMENT_DERIVED = "enrichment_derived"
    CONTRADICTION_FOUND = "contradiction_found"
    CONSTRAINT_VIOLATED = "constraint_violated"
    LAYER_COMPLETED = "layer_completed"
    # governance
    FINDING_RAISED = "finding_raised"
    FINDING_RESOLVED = "finding_resolved"
    FINDING_SUPPRESSED = "finding_suppressed"
    # outputs and operations
    SNAPSHOT_SEALED = "snapshot_sealed"
    ARTIFACT_GENERATED = "artifact_generated"
    SCENARIO_FIRED = "scenario_fired"
    PLUGIN_REGISTERED = "plugin_registered"
    PLUGIN_QUARANTINED = "plugin_quarantined"
    QUESTION_ASKED = "question_asked"

    @property
    def mutates_graph(self) -> bool:
        """Whether the graph projection has to react to this."""
        return self in _GRAPH_EVENTS

    @property
    def is_operational(self) -> bool:
        """Events about running the platform rather than about the ecosystem.

        The UI feed shows these differently — an operator wants "attached
        payments-repo" and "lodash gained a CVE" in the same stream but not
        looking the same.
        """
        return self in _OPERATIONAL


_GRAPH_EVENTS = frozenset({
    EventKind.NODE_ASSERTED, EventKind.NODE_RETIRED,
    EventKind.EDGE_ASSERTED, EventKind.EDGE_RETIRED,
})

_OPERATIONAL = frozenset({
    EventKind.TARGET_CHANGED, EventKind.ELEMENT_ATTACHED, EventKind.ELEMENT_DETACHED,
    EventKind.ELEMENT_HANDED_OVER, EventKind.HEARTBEAT_RECORDED,
    EventKind.PLUGIN_REGISTERED, EventKind.PLUGIN_QUARANTINED,
    EventKind.SCENARIO_FIRED, EventKind.QUESTION_ASKED, EventKind.SNAPSHOT_SEALED,
})

#: Sequence number meaning "not yet appended". The ledger assigns the real one.
UNSEQUENCED = 0


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """One thing that happened, immutably.

    ``sequence`` is assigned by the ledger on append, so an event constructed in
    a handler is unsequenced until it is written. That ordering is what every
    projection replays against.
    """

    kind: EventKind
    subject: str                              # node id, element name, or artifact path
    payload: Mapping[str, Any] = field(default_factory=dict)
    actor: str = "system"
    causation_id: str = ""                    # the event that caused this one
    correlation_id: str = ""                  # the operation all of these belong to
    occurred_at: int = 0
    sequence: int = UNSEQUENCED

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError(f"{self.kind.value} event has no subject")
        if self.occurred_at == 0:
            object.__setattr__(self, "occurred_at", time.time_ns())

    @property
    def event_id(self) -> str:
        """Content-addressed, so at-least-once delivery can dedupe on it.

        The sequence is excluded: an event's identity is what happened, not where
        it landed in the log.
        """
        return digest({
            "kind": self.kind.value,
            "subject": self.subject,
            "payload": self.payload,
            "actor": self.actor,
            "causation_id": self.causation_id,
            "occurred_at": self.occurred_at,
        })

    def caused(
        self,
        kind: EventKind,
        subject: str,
        /,
        payload: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> "DomainEvent":
        """Derive a follow-on event that records this one as its cause.

        ``kind`` and ``subject`` are positional-only and ``payload`` takes a
        mapping, because event payloads routinely contain keys named `kind` and
        `subject` — a manifest declaration has both — and a signature that could
        not carry them would be a trap set for every future caller.
        """
        return DomainEvent(
            kind=kind, subject=subject, payload={**(payload or {}), **extra},
            actor=self.actor,
            causation_id=self.event_id,
            correlation_id=self.correlation_id or self.event_id,
        )

    def sequenced(self, sequence: int) -> "DomainEvent":
        return replace(self, sequence=sequence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "subject": self.subject,
            "payload": dict(self.payload),
            "actor": self.actor,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DomainEvent":
        return cls(
            kind=EventKind(data["kind"]),
            subject=data["subject"],
            payload=dict(data.get("payload", {})),
            actor=data.get("actor", "system"),
            causation_id=data.get("causation_id", ""),
            correlation_id=data.get("correlation_id", ""),
            occurred_at=int(data.get("occurred_at", 0)),
            sequence=int(data.get("sequence", UNSEQUENCED)),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<{self.kind.value} #{self.sequence} {self.subject}>"


def emit(
    kind: EventKind,
    subject: str,
    /,
    payload: Mapping[str, Any] | None = None,
    *,
    actor: str = "system",
    cause: DomainEvent | None = None,
    **extra: Any,
) -> DomainEvent:
    """Construct an event, inheriting causation from its cause when there is one."""
    return DomainEvent(
        kind=kind,
        subject=subject,
        payload={**(payload or {}), **extra},
        actor=actor if cause is None else (actor if actor != "system" else cause.actor),
        causation_id=cause.event_id if cause else "",
        correlation_id=(cause.correlation_id or cause.event_id) if cause else "",
    )


def causation_chain(
    event: DomainEvent, *, by_id: Mapping[str, DomainEvent]
) -> tuple[DomainEvent, ...]:
    """Walk back to the root cause — why did the platform do this?

    Returned oldest-first, which is the order it reads in an explanation.
    """
    chain: list[DomainEvent] = [event]
    seen = {event.event_id}
    current = event
    while current.causation_id and current.causation_id not in seen:
        parent = by_id.get(current.causation_id)
        if parent is None:
            break
        seen.add(parent.event_id)
        chain.append(parent)
        current = parent
    return tuple(reversed(chain))
