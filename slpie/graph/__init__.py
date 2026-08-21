"""The knowledge graph — a bitemporal projection of the ledger, traversed in SQL.

Nodes and edges here hold no truth of their own. They are a read model rebuilt
from events, which is why a schema change is a rebuild rather than a migration
and why `graph.clear()` is a safe operation rather than data loss.
"""

from __future__ import annotations

from .interest import Elision, Field, Interest, Signals, Surveyor
from .query import GraphProjection
from .schema import SCHEMA, SCHEMA_VERSION
from .snapshot import GraphDiff, Snapshot, SnapshotStore, root_digest
from .sqlite_graph import SqliteGraph
from .store import GraphStore, GraphView, Traversal
from .traversal import Cycle, Impacted, ImpactResult, Path, Traverser

__all__ = [
    "SqliteGraph", "GraphStore", "GraphView", "Traversal",
    "GraphProjection", "SCHEMA", "SCHEMA_VERSION",
    "Snapshot", "SnapshotStore", "GraphDiff", "root_digest",
    "Traverser", "ImpactResult", "Impacted", "Cycle", "Path",
    "Surveyor", "Field", "Interest", "Signals", "Elision",
]
