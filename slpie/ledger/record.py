"""Ledger records — an event, sealed into position.

A :class:`DomainEvent` is what happened. A :class:`LedgerRecord` is that event
plus where it landed in history and the hash binding it there. The separation
matters: a handler emits events freely and cheaply, and only the ledger decides
their order — which is the single point where concurrency has to be handled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..core.events import DomainEvent, EventKind
from .chain import GENESIS, record_hash


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    """One immutable entry. Nothing here is ever updated in place."""

    sequence: int
    event: DomainEvent
    previous_hash: str
    hash: str
    written_at: int = 0

    @classmethod
    def seal(
        cls,
        event: DomainEvent,
        *,
        sequence: int,
        previous_hash: str = GENESIS,
        written_at: int = 0,
    ) -> "LedgerRecord":
        """Place an event at a sequence and compute its chained hash."""
        placed = event.sequenced(sequence)
        digest = record_hash(
            sequence=sequence,
            previous_hash=previous_hash,
            event_id=placed.event_id,
            kind=placed.kind.value,
            subject=placed.subject,
            payload=placed.payload,
            occurred_at=placed.occurred_at,
            actor=placed.actor,
        )
        return cls(
            sequence=sequence, event=placed, previous_hash=previous_hash,
            hash=digest, written_at=written_at or placed.occurred_at,
        )

    def compute_hash(self) -> str:
        """Recompute from content — the verification half of the chain."""
        return record_hash(
            sequence=self.sequence,
            previous_hash=self.previous_hash,
            event_id=self.event.event_id,
            kind=self.event.kind.value,
            subject=self.event.subject,
            payload=self.event.payload,
            occurred_at=self.event.occurred_at,
            actor=self.event.actor,
        )

    @property
    def intact(self) -> bool:
        return self.compute_hash() == self.hash

    @property
    def kind(self) -> EventKind:
        return self.event.kind

    @property
    def subject(self) -> str:
        return self.event.subject

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "previous_hash": self.previous_hash,
            "hash": self.hash,
            "written_at": self.written_at,
            "event": self.event.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LedgerRecord":
        return cls(
            sequence=int(data["sequence"]),
            event=DomainEvent.from_dict(data["event"]),
            previous_hash=data["previous_hash"],
            hash=data["hash"],
            written_at=int(data.get("written_at", 0)),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Record #{self.sequence} {self.event.kind.value} {self.hash[:8]}>"
