"""The unit of history: a keyring event.

Everything the kernel does — capturing a byte range, inferring a shape, emitting
a module, running a transformation, exchanging a message with a peer agent — is
recorded as one of these. They are immutable; correction happens by appending,
never by editing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from ..ids import StorageName, digest, iso, new_id, now_ns


class EventKind(str, Enum):
    """Why an event exists. Policymakers route on this."""

    CAPTURE = "capture"          # raw data observed from the environment
    SHAPE = "shape"              # a shape was inferred or revised
    ROUTE = "route"              # a payload moved through a hub channel
    SPILL = "spill"              # in-memory payload staged to object storage
    CODEGEN = "codegen"          # code was generated or regenerated
    MERGE = "merge"              # generated code merged with existing source
    TRANSFORM = "transform"      # a sandboxed transformation ran
    MIGRATION = "migration"      # a data-as-code mutation was recorded
    POLICY = "policy"            # a policymaker reached a decision
    AGENT = "agent"              # an exchange with a peer agent
    CHECKPOINT = "checkpoint"    # a named point history can be rolled back to
    ROLLBACK = "rollback"        # history was rewound (recorded going forward)
    NOTE = "note"                # human/agent annotation, no state effect


#: Kinds that never mutate materialized state during replay — pure history.
#:
#: ``ROLLBACK`` belongs here even though a rollback plainly changes state: the
#: change is applied by truncating and replaying the surviving prefix, and the
#: event itself is the *record* of that. It carries the undone paths for audit,
#: so materializing them would resurrect exactly what the rollback removed.
INERT_KINDS = frozenset({EventKind.NOTE, EventKind.CHECKPOINT, EventKind.ROLLBACK})


@dataclass(frozen=True, slots=True)
class KeyringEvent:
    """An immutable entry on the ContextFlow spine.

    `paths` is what makes concurrent work safe: two events that touch disjoint
    keyring paths can land in either order, while two that touch the same path
    from divergent bases are a genuine conflict.
    """

    kind: EventKind
    actor: str
    paths: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    base_version: str = ""
    parents: tuple[str, ...] = ()
    lamport: int = 0
    vector: Mapping[str, int] = field(default_factory=dict)
    storage: StorageName | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("evt"))
    ts_ns: int = field(default_factory=now_ns)

    # -- derived ---------------------------------------------------------

    @property
    def digest(self) -> str:
        """Content address of the event, excluding its position in history."""
        return digest(
            {
                "kind": self.kind.value,
                "actor": self.actor,
                "paths": list(self.paths),
                "payload": dict(self.payload),
                "parents": list(self.parents),
                "labels": dict(self.labels),
            }
        )

    @property
    def at(self) -> str:
        return iso(self.ts_ns)

    @property
    def inert(self) -> bool:
        return self.kind in INERT_KINDS

    # -- construction helpers --------------------------------------------

    def stamped(self, *, lamport: int, vector: Mapping[str, int], base_version: str) -> "KeyringEvent":
        """Return a copy carrying the clock and base assigned at append time."""
        return replace(self, lamport=lamport, vector=dict(vector), base_version=base_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "actor": self.actor,
            "paths": list(self.paths),
            "payload": dict(self.payload),
            "base_version": self.base_version,
            "parents": list(self.parents),
            "lamport": self.lamport,
            "vector": dict(self.vector),
            "storage": self.storage.key if self.storage else None,
            "labels": dict(self.labels),
            "ts_ns": self.ts_ns,
            "at": self.at,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KeyringEvent":
        storage = data.get("storage")
        return cls(
            kind=EventKind(data["kind"]),
            actor=data["actor"],
            paths=tuple(data.get("paths", ())),
            payload=dict(data.get("payload", {})),
            base_version=data.get("base_version", ""),
            parents=tuple(data.get("parents", ())),
            lamport=int(data.get("lamport", 0)),
            vector=dict(data.get("vector", {})),
            storage=StorageName.parse(storage) if storage else None,
            labels=dict(data.get("labels", {})),
            id=data["id"],
            ts_ns=int(data.get("ts_ns", now_ns())),
        )

    def __str__(self) -> str:  # pragma: no cover - display only
        where = ",".join(self.paths) or "-"
        return f"[{self.lamport:04d}] {self.kind.value:<10} {self.actor:<16} {where}"
