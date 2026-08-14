"""The ledger interface, and an in-memory implementation of it.

``LedgerStore`` is the seam the plan reserves for distributed storage. Everything
above it — handlers, projections, the graph, the UI — talks to this protocol, so
replacing SQLite with something clustered is a new implementation rather than a
redesign.

The in-memory store is not a toy: the simulator and the tests run against it, and
it enforces exactly the same chain invariants as the durable one. A store that
relaxed the rules "because it's only for testing" would make every test that
passes against it a weaker statement than it appears to be.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable, Iterator, Protocol, Sequence, runtime_checkable

from ..core.events import DomainEvent, EventKind
from ..errors import LedgerError
from .chain import GENESIS, head_digest, verify_chain
from .record import LedgerRecord


@runtime_checkable
class LedgerStore(Protocol):
    """Append-only, hash-chained, replayable from the beginning."""

    def append(self, *events: DomainEvent) -> tuple[LedgerRecord, ...]: ...

    def read(self, *, since: int = 0, limit: int = 0) -> tuple[LedgerRecord, ...]: ...

    def head(self) -> LedgerRecord | None: ...

    def verify(self) -> None: ...

    def __len__(self) -> int: ...


class LedgerReader(Protocol):
    """The read half, for projections that must never write."""

    def read(self, *, since: int = 0, limit: int = 0) -> tuple[LedgerRecord, ...]: ...


class InMemoryLedger:
    """A complete ledger held in a list. Same invariants as the durable store."""

    def __init__(self) -> None:
        self._records: list[LedgerRecord] = []
        self._by_event_id: dict[str, int] = {}
        self._lock = threading.RLock()

    # -- writing ---------------------------------------------------------

    def append(self, *events: DomainEvent) -> tuple[LedgerRecord, ...]:
        """Seal events into consecutive positions.

        Appending an event already in the ledger is idempotent rather than an
        error: at-least-once delivery from a plugin or a watcher is a normal
        condition, not a fault, and duplicating it would corrupt every count
        derived from the stream.
        """
        with self._lock:
            written: list[LedgerRecord] = []
            for event in events:
                if event.event_id in self._by_event_id:
                    written.append(self._records[self._by_event_id[event.event_id] - 1])
                    continue
                previous = self._records[-1].hash if self._records else GENESIS
                record = LedgerRecord.seal(
                    event,
                    sequence=len(self._records) + 1,
                    previous_hash=previous,
                    written_at=time.time_ns(),
                )
                self._records.append(record)
                self._by_event_id[record.event.event_id] = record.sequence
                written.append(record)
            return tuple(written)

    # -- reading ---------------------------------------------------------

    def read(self, *, since: int = 0, limit: int = 0) -> tuple[LedgerRecord, ...]:
        with self._lock:
            selected = self._records[since:]
            return tuple(selected[:limit] if limit else selected)

    def events(self, *, since: int = 0, kinds: Iterable[EventKind] = ()) -> tuple[DomainEvent, ...]:
        wanted = frozenset(kinds)
        return tuple(
            record.event for record in self.read(since=since)
            if not wanted or record.kind in wanted
        )

    def head(self) -> LedgerRecord | None:
        with self._lock:
            return self._records[-1] if self._records else None

    def at(self, sequence: int) -> LedgerRecord:
        with self._lock:
            if not 1 <= sequence <= len(self._records):
                raise LedgerError(f"no record at sequence {sequence}")
            return self._records[sequence - 1]

    def by_event_id(self, event_id: str) -> LedgerRecord | None:
        with self._lock:
            position = self._by_event_id.get(event_id)
            return self._records[position - 1] if position else None

    def subject_history(self, subject: str) -> tuple[LedgerRecord, ...]:
        """Everything that ever happened to one thing, in order."""
        with self._lock:
            return tuple(r for r in self._records if r.subject == subject)

    # -- integrity -------------------------------------------------------

    def verify(self) -> None:
        with self._lock:
            verify_chain(self._records)

    @property
    def digest(self) -> str:
        with self._lock:
            return head_digest(self._records)

    @property
    def version(self) -> int:
        """The current sequence — the ledger's version number."""
        with self._lock:
            return len(self._records)

    def __len__(self) -> int:
        return self.version

    def __iter__(self) -> Iterator[LedgerRecord]:
        return iter(self.read())


def replay_into(store: LedgerReader, subscribers: Sequence[object], *, since: int = 0) -> int:
    """Feed a ledger's history to objects with a ``handle`` method.

    Rebuilding a read model is exactly this, which is why recovery and schema
    migration are the same operation here.
    """
    delivered = 0
    for record in store.read(since=since):
        for subscriber in subscribers:
            subscriber.handle(record.event)  # type: ignore[attr-defined]
            delivered += 1
    return delivered
