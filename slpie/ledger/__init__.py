"""The immutable metadata ledger — hash-chained, append-only, replayable.

This is the event store, and therefore the single source of truth. Everything
else in the platform is a projection of it: the graph, the findings view, the
station registry, the enterprise artifacts. That is what makes "complete
historical evolution" a property of the storage rather than a feature somebody
has to remember to maintain.
"""

from __future__ import annotations

from .chain import GENESIS, canonical, head_digest, record_hash, verify_chain
from .projection import (
    Attachment,
    CountingProjection,
    FindingProjection,
    Projection,
    ProjectionSet,
    SourceProjection,
    StationProjection,
    rebuildable,
)
from .record import LedgerRecord
from .sqlite_ledger import SqliteLedger, open_ledger
from .store import InMemoryLedger, LedgerReader, LedgerStore, replay_into

__all__ = [
    "LedgerRecord", "LedgerStore", "LedgerReader", "InMemoryLedger", "SqliteLedger",
    "open_ledger", "replay_into",
    "GENESIS", "canonical", "record_hash", "verify_chain", "head_digest",
    "Projection", "ProjectionSet", "StationProjection", "FindingProjection",
    "SourceProjection", "CountingProjection", "Attachment", "rebuildable",
]
