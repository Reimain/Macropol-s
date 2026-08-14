"""Snapshots — a content-addressed name for the graph at a moment.

A snapshot id is a digest over the ordered set of live node and edge ids at a
given ledger version and valid time. That makes it reproducible: run discovery
twice over an unchanged ecosystem and the second snapshot id equals the first,
which is the acceptance criterion for the incremental engine and the only
practical way to prove a rescan changed nothing.

The id deliberately covers *identity*, not confidence. New corroborating evidence
for an existing edge raises its confidence without changing what the graph
contains — and a snapshot id that moved on every re-read of the same file would
be useless for exactly the comparison it exists to serve.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.node import OPEN


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The graph's state at one point, named by its content."""

    id: str
    ledger_version: int
    root_digest: str
    node_count: int = 0
    edge_count: int = 0
    label: str = ""
    valid_time: int = 0
    sealed_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "ledger_version": self.ledger_version,
            "root_digest": self.root_digest, "node_count": self.node_count,
            "edge_count": self.edge_count, "valid_time": self.valid_time,
            "sealed_at": self.sealed_at,
        }

    def __str__(self) -> str:
        return f"{self.label or 'snapshot'}@{self.id[:12]}"


@dataclass(frozen=True, slots=True)
class GraphDiff:
    """What changed between two snapshots.

    Split into added/removed *and* changed, because they mean different things
    to a reviewer: an added dependency is a decision, while a changed confidence
    is usually just discovery catching up with reality.
    """

    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    added_edges: tuple[str, ...] = ()
    removed_edges: tuple[str, ...] = ()
    changed_confidence: tuple[tuple[str, float, float], ...] = ()
    from_snapshot: str = ""
    to_snapshot: str = ""

    @property
    def empty(self) -> bool:
        return not (
            self.added_nodes or self.removed_nodes
            or self.added_edges or self.removed_edges
        )

    @property
    def structural(self) -> bool:
        """Whether the shape changed, ignoring confidence movement."""
        return not self.empty

    def summary(self) -> str:
        if self.empty and not self.changed_confidence:
            return "no change"
        parts = []
        if self.added_nodes:
            parts.append(f"+{len(self.added_nodes)} nodes")
        if self.removed_nodes:
            parts.append(f"-{len(self.removed_nodes)} nodes")
        if self.added_edges:
            parts.append(f"+{len(self.added_edges)} edges")
        if self.removed_edges:
            parts.append(f"-{len(self.removed_edges)} edges")
        if self.changed_confidence:
            parts.append(f"{len(self.changed_confidence)} confidence changes")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_snapshot, "to": self.to_snapshot,
            "summary": self.summary(), "empty": self.empty,
            "added_nodes": list(self.added_nodes),
            "removed_nodes": list(self.removed_nodes),
            "added_edges": list(self.added_edges),
            "removed_edges": list(self.removed_edges),
            "changed_confidence": [
                {"id": i, "from": a, "to": b} for i, a, b in self.changed_confidence
            ],
        }


def root_digest(node_ids: Iterable[str], edge_ids: Iterable[str]) -> str:
    """A digest over the ordered live id sets.

    Sorted before hashing so that discovery order — which varies with filesystem
    iteration and plugin scheduling — cannot change the result.
    """
    hasher = hashlib.sha256()
    for node_id in sorted(node_ids):
        hasher.update(b"n")
        hasher.update(node_id.encode("utf-8"))
    for edge_id in sorted(edge_ids):
        hasher.update(b"e")
        hasher.update(edge_id.encode("utf-8"))
    return hasher.hexdigest()


class SnapshotStore:
    """Seals and compares snapshots against a graph."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def seal(self, *, ledger_version: int, label: str = "", valid_time: int = 0) -> Snapshot:
        node_ids = [row["id"] for row in self.graph.connection.execute(
            "SELECT id FROM node WHERE valid_to IS NULL"
        )]
        edge_ids = [row["id"] for row in self.graph.connection.execute(
            "SELECT id FROM edge WHERE valid_to IS NULL"
        )]
        digest = root_digest(node_ids, edge_ids)
        # The snapshot id covers the content *and* the ledger version it was
        # taken at, so two identical graphs at different points in history remain
        # distinguishable when you need to compare them.
        identifier = hashlib.sha256(
            f"{digest}:{ledger_version}:{valid_time}".encode("utf-8")
        ).hexdigest()[:40]

        snapshot = Snapshot(
            id=identifier, ledger_version=ledger_version, root_digest=digest,
            node_count=len(node_ids), edge_count=len(edge_ids), label=label,
            valid_time=valid_time, sealed_at=time.time_ns(),
        )
        self.graph.connection.execute(
            "INSERT OR REPLACE INTO snapshot (id, label, ledger_version, valid_time,"
            " root_digest, node_count, edge_count, sealed_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                snapshot.id, snapshot.label, snapshot.ledger_version, snapshot.valid_time,
                snapshot.root_digest, snapshot.node_count, snapshot.edge_count,
                snapshot.sealed_at,
            ),
        )
        return snapshot

    def get(self, identifier: str) -> Snapshot | None:
        row = self.graph.connection.execute(
            "SELECT * FROM snapshot WHERE id = ? OR label = ? ORDER BY sealed_at DESC LIMIT 1",
            (identifier, identifier),
        ).fetchone()
        return _row_to_snapshot(row) if row else None

    def list(self) -> tuple[Snapshot, ...]:
        rows = self.graph.connection.execute(
            "SELECT * FROM snapshot ORDER BY sealed_at DESC"
        )
        return tuple(_row_to_snapshot(row) for row in rows)

    def diff_against_current(self, snapshot: Snapshot) -> GraphDiff:
        """Compare a sealed snapshot with the graph as it stands now."""
        return diff_ids(
            before=_ids_at(self.graph, snapshot.ledger_version),
            after=_current_ids(self.graph),
            from_snapshot=snapshot.id,
            to_snapshot="HEAD",
        )

    def diff(self, earlier: Snapshot, later: Snapshot) -> GraphDiff:
        """Compare two sealed snapshots, reconstructing each from `sequence`.

        Reconstruction from the recorded sequence is why snapshots need store
        nothing but a digest — the graph rows already carry the ledger position
        that produced them.
        """
        return diff_ids(
            before=_ids_at(self.graph, earlier.ledger_version),
            after=_ids_at(self.graph, later.ledger_version),
            from_snapshot=earlier.id,
            to_snapshot=later.id,
        )


def diff_ids(
    *,
    before: tuple[set[str], set[str], dict[str, float]],
    after: tuple[set[str], set[str], dict[str, float]],
    from_snapshot: str = "",
    to_snapshot: str = "",
) -> GraphDiff:
    before_nodes, before_edges, before_confidence = before
    after_nodes, after_edges, after_confidence = after

    # Only report movement where both sides genuinely recorded a confidence.
    # A missing historical value is unknown, not zero, and treating it as zero
    # would report every surviving edge as having changed.
    changed = tuple(
        (edge_id, before_confidence[edge_id], after_confidence[edge_id])
        for edge_id in sorted(before_edges & after_edges)
        if edge_id in before_confidence and edge_id in after_confidence
        and abs(before_confidence[edge_id] - after_confidence[edge_id]) > 1e-9
    )
    return GraphDiff(
        added_nodes=tuple(sorted(after_nodes - before_nodes)),
        removed_nodes=tuple(sorted(before_nodes - after_nodes)),
        added_edges=tuple(sorted(after_edges - before_edges)),
        removed_edges=tuple(sorted(before_edges - after_edges)),
        changed_confidence=changed,
        from_snapshot=from_snapshot,
        to_snapshot=to_snapshot,
    )


def _current_ids(graph: Any) -> tuple[set[str], set[str], dict[str, float]]:
    nodes = {row["id"] for row in graph.connection.execute(
        "SELECT id FROM node WHERE valid_to IS NULL")}
    edges: dict[str, float] = {
        row["id"]: row["confidence"] for row in graph.connection.execute(
            "SELECT id, confidence FROM edge WHERE valid_to IS NULL")
    }
    return nodes, set(edges), edges


def _ids_at(graph: Any, ledger_version: int) -> tuple[set[str], set[str], dict[str, float]]:
    """The live id sets as of a ledger version.

    Existence is bounded by ``first_sequence`` and ``retired_sequence`` rather
    than by ``sequence``, which an upsert overwrites — re-reading an unchanged
    lockfile must not make every node look newly arrived.

    The confidence map is deliberately empty: the read model holds only current
    confidence, so claiming to know an edge's historical confidence would be an
    invention. Confidence movement is reported only against HEAD.
    """
    nodes = {row["id"] for row in graph.connection.execute(
        "SELECT id FROM node WHERE first_sequence <= :v"
        " AND (retired_sequence IS NULL OR retired_sequence > :v)",
        {"v": ledger_version})}
    edges = {row["id"] for row in graph.connection.execute(
        "SELECT id FROM edge WHERE first_sequence <= :v"
        " AND (retired_sequence IS NULL OR retired_sequence > :v)",
        {"v": ledger_version})}
    return nodes, edges, {}


def _row_to_snapshot(row: Any) -> Snapshot:
    return Snapshot(
        id=row["id"], label=row["label"], ledger_version=row["ledger_version"],
        valid_time=row["valid_time"], root_digest=row["root_digest"],
        node_count=row["node_count"], edge_count=row["edge_count"],
        sealed_at=row["sealed_at"],
    )
