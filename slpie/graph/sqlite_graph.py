"""The graph, projected into SQLite and traversed with recursive CTEs.

Blast radius, reachability and cycle detection all run *inside* the database.
Pulling edges into Python and walking them there would mean one query per node
visited — on a real ecosystem that is tens of thousands of round trips for a
question the query planner answers in one, with the indexes it already has.

Two things the traversal must get right, and both are in the CTE rather than
bolted on afterwards:

* **Cycle safety.** A dependency graph has cycles. The path string carries the
  visited set, and `instr` rejects a node already on the current path, so a cycle
  terminates that branch instead of the query.
* **Confidence along the path.** Reachability propagates the *minimum* edge
  confidence, so a component reached only through a 0.4 dynamic load is reported
  as reachable at 0.4 — not merged into the certain results and not silently
  dropped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.identity import parse_identity
from ..domain.lifecycle import (
    ArchitectureClass,
    ComplianceState,
    LifecycleState,
    RiskClass,
)
from ..domain.node import OPEN, Node, NodeKind
from ..errors import GraphError, NodeNotFound
from . import rows
from .schema import FTS_SCHEMA, SCHEMA, SCHEMA_VERSION, has_fts5

# Reverse reachability: who breaks if `:root` changes. Follows edges backwards
# (from dst to src) because `a depends_on b` means a change in b reaches a.
BLAST_RADIUS_SQL = """
WITH RECURSIVE reach(node_id, depth, path, min_conf) AS (
    SELECT :root, 0, '>' || :root || '>', 1.0
  UNION
    SELECT e.src, r.depth + 1, r.path || e.src || '>',
           MIN(r.min_conf, e.confidence)
    FROM edge e
    JOIN reach r ON e.dst = r.node_id
    WHERE e.valid_to IS NULL
      AND e.propagates = 1
      AND r.depth < :max_depth
      AND e.confidence >= :min_confidence
      AND instr(r.path, '>' || e.src || '>') = 0
)
SELECT node_id, MIN(depth) AS distance, MAX(min_conf) AS path_confidence
FROM reach
WHERE node_id != :root
GROUP BY node_id
ORDER BY distance, node_id
"""

# Forward reachability: what `:root` depends on, transitively.
REACHABLE_SQL = """
WITH RECURSIVE reach(node_id, depth, path, min_conf) AS (
    SELECT :root, 0, '>' || :root || '>', 1.0
  UNION
    SELECT e.dst, r.depth + 1, r.path || e.dst || '>',
           MIN(r.min_conf, e.confidence)
    FROM edge e
    JOIN reach r ON e.src = r.node_id
    WHERE e.valid_to IS NULL
      AND e.propagates = 1
      AND r.depth < :max_depth
      AND e.confidence >= :min_confidence
      AND instr(r.path, '>' || e.dst || '>') = 0
)
SELECT node_id, MIN(depth) AS distance, MAX(min_conf) AS path_confidence
FROM reach
WHERE node_id != :root
GROUP BY node_id
ORDER BY distance, node_id
"""

# A cycle is a path that returns to where it started. The same walk as
# reachability, but reporting the closing step instead of pruning it.
CYCLES_SQL = """
WITH RECURSIVE walk(start, node_id, depth, path) AS (
    SELECT n.id, n.id, 0, '>' || n.id || '>'
    FROM node n
    WHERE n.valid_to IS NULL
      AND EXISTS (SELECT 1 FROM edge e WHERE e.src = n.id AND e.valid_to IS NULL)
  UNION
    SELECT w.start, e.dst, w.depth + 1, w.path || e.dst || '>'
    FROM edge e
    JOIN walk w ON e.src = w.node_id
    WHERE e.valid_to IS NULL
      AND e.propagates = 1
      AND w.depth < :max_depth
      AND instr(w.path, '>' || e.dst || '>') = 0
)
SELECT DISTINCT w.path || w.start || '>' AS cycle, w.depth + 1 AS length
FROM walk w
JOIN edge e ON e.src = w.node_id AND e.dst = w.start
WHERE e.valid_to IS NULL AND e.propagates = 1 AND w.depth > 0
ORDER BY length, cycle
"""

# Every path between two nodes, shortest first — what `graph_explanation`
# renders when asked how A comes to depend on B.
PATHS_SQL = """
WITH RECURSIVE walk(node_id, depth, path, min_conf) AS (
    SELECT :src, 0, '>' || :src || '>', 1.0
  UNION
    SELECT e.dst, w.depth + 1, w.path || e.dst || '>',
           MIN(w.min_conf, e.confidence)
    FROM edge e
    JOIN walk w ON e.src = w.node_id
    WHERE e.valid_to IS NULL
      AND w.depth < :max_depth
      AND e.confidence >= :min_confidence
      AND instr(w.path, '>' || e.dst || '>') = 0
)
SELECT path, depth, min_conf FROM walk
WHERE node_id = :dst
ORDER BY depth, min_conf DESC
LIMIT :limit
"""


class SqliteGraph:
    """The graph projection. Rebuildable from the ledger at any moment."""

    def __init__(self, path: str | Path | None = None, *, timeout: float = 30.0) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._fts = False
        self._initialise()

    # -- connection ------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        if self.path is None:
            # An in-memory graph cannot be reopened per thread — there would be
            # nothing shared to open. One connection, guarded by the lock.
            if self._shared is None:
                self._shared = self._connect(":memory:")
            return self._shared
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = self._connect(str(self.path))
            self._local.connection = existing
        return existing

    def _connect(self, target: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            target, timeout=self._timeout, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        if target != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=%d" % int(self._timeout * 1000))
        # Recursive CTEs on a large graph are the one place a runaway query
        # could exhaust memory; SQLite's own limit is cheaper than watching for it.
        connection.execute("PRAGMA cache_size=-32000")
        return connection

    def _initialise(self) -> None:
        with self._lock:
            connection = self.connection
            connection.executescript(SCHEMA)
            self._fts = has_fts5(connection)
            if self._fts:
                connection.executescript(FTS_SCHEMA)
            connection.execute(
                "INSERT INTO graph_meta (key, value) VALUES ('schema_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None

    # -- writing ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Batch writes into one transaction.

        Connections run in autocommit, which is right for the occasional single
        assertion and badly wrong for discovery — a lockfile yields thousands of
        nodes, and committing each one separately means a disk sync per package.
        Nesting is a no-op so callers can wrap freely without coordinating.
        """
        with self._lock:
            if getattr(self._local, "in_transaction", False):
                yield
                return
            connection = self.connection
            connection.execute("BEGIN")
            self._local.in_transaction = True
            try:
                yield
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                self._local.in_transaction = False

    def assert_nodes(self, nodes: Iterable[Node], *, sequence: int = 0) -> None:
        with self.transaction():
            for node in nodes:
                self.assert_node(node, sequence=sequence)

    def assert_edges(self, edges: Iterable[Edge], *, sequence: int = 0) -> None:
        with self.transaction():
            for edge in edges:
                self.assert_edge(edge, sequence=sequence)

    def assert_node(self, node: Node, *, sequence: int = 0) -> None:
        """Insert or supersede a node, carrying its evidence with it.

        Upsert rather than insert because the same node is asserted repeatedly —
        once per discoverer that meets it — and each assertion may bring new
        evidence that raises its confidence.
        """
        with self._lock:
            connection = self.connection
            cursor = connection.execute(
                "INSERT INTO node (id, kind, identity, coordinate, name, version, display,"
                " properties, lifecycle, risk, compliance, architecture, confidence,"
                " validation, valid_from, valid_to, observed_at, superseded_at, sequence,"
                " first_sequence)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                # first_sequence is deliberately absent from the update list: it
                # records when this node first entered the graph, and a
                # re-assertion is not a new arrival.
                " ON CONFLICT(id) DO UPDATE SET"
                "   confidence = excluded.confidence,"
                "   validation = excluded.validation,"
                "   lifecycle = excluded.lifecycle,"
                "   risk = excluded.risk,"
                "   compliance = excluded.compliance,"
                "   architecture = excluded.architecture,"
                "   properties = excluded.properties,"
                "   valid_to = excluded.valid_to,"
                "   superseded_at = excluded.superseded_at,"
                "   sequence = excluded.sequence"
                " RETURNING rowid",
                (
                    node.id, node.kind.value, node.identity.to_string(), node.coordinate,
                    node.name, node.version, node.display, json.dumps(dict(node.properties)),
                    node.lifecycle.value, node.risk.value, node.compliance.value,
                    node.architecture.value, node.confidence, node.validation.value,
                    node.valid_from, _nullable(node.valid_to), node.observed_at,
                    _nullable(node.superseded_at), sequence, sequence,
                ),
            )
            self._store_evidence(node.evidence)
            connection.executemany(
                "INSERT OR IGNORE INTO node_evidence (node_id, evidence_id) VALUES (?,?)",
                [(node.id, item.id) for item in node.evidence],
            )
            if self._fts:
                # Keyed by rowid, not by the id column. `id` is UNINDEXED in the
                # FTS table, so deleting by it scans the entire index — which
                # turns bulk discovery into quadratic work.
                row = cursor.fetchone()
                rowid = row["rowid"] if row else connection.execute(
                    "SELECT rowid FROM node WHERE id = ?", (node.id,)
                ).fetchone()["rowid"]
                connection.execute("DELETE FROM node_search WHERE rowid = ?", (rowid,))
                connection.execute(
                    "INSERT INTO node_search (rowid, id, name, identity, display, kind)"
                    " VALUES (?,?,?,?,?,?)",
                    (rowid, node.id, node.name, node.identity.to_string(), node.display,
                     node.kind.value),
                )

    def assert_edge(self, edge: Edge, *, sequence: int = 0) -> None:
        with self._lock:
            connection = self.connection
            connection.execute(
                "INSERT INTO edge (id, kind, src, dst, qualifier, properties, confidence,"
                " validation, propagates, valid_from, valid_to, observed_at, superseded_at,"
                " sequence, first_sequence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                "   confidence = excluded.confidence,"
                "   validation = excluded.validation,"
                "   properties = excluded.properties,"
                "   valid_to = excluded.valid_to,"
                "   superseded_at = excluded.superseded_at,"
                "   sequence = excluded.sequence",
                (
                    edge.id, edge.kind.value, edge.src, edge.dst, edge.qualifier,
                    json.dumps(dict(edge.properties)), edge.confidence, edge.validation.value,
                    1 if edge.kind.propagates_impact else 0,
                    edge.valid_from, _nullable(edge.valid_to), edge.observed_at,
                    _nullable(edge.superseded_at), sequence, sequence,
                ),
            )
            self._store_evidence(edge.evidence)
            connection.executemany(
                "INSERT OR IGNORE INTO edge_evidence (edge_id, evidence_id) VALUES (?,?)",
                [(edge.id, item.id) for item in edge.evidence],
            )

    def _store_evidence(self, evidence: Sequence[Evidence]) -> None:
        self.connection.executemany(
            "INSERT OR IGNORE INTO evidence (id, kind, uri, line, extractor, content_digest,"
            " excerpt, observed_at, base_confidence, labels)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    item.id, item.kind.value, item.location.uri, item.location.line,
                    item.extractor, item.content_digest, item.excerpt[:500],
                    item.observed_at, item.base_confidence,
                    json.dumps(dict(item.labels), sort_keys=True),
                )
                for item in evidence
            ],
        )

    def retire_node(self, node_id: str, *, valid_to: int, sequence: int = 0) -> bool:
        """Retire a node and every live edge touching it.

        The cascade is not a convenience. An edge whose endpoint no longer
        exists is not a relationship, and leaving it live would let traversal
        walk into a retired node and report it as impacted.
        """
        with self._lock:
            connection = self.connection
            cursor = connection.execute(
                "UPDATE node SET valid_to = ?, lifecycle = 'retired', sequence = ?,"
                " retired_sequence = ? WHERE id = ? AND valid_to IS NULL",
                (valid_to or 1, sequence, sequence, node_id),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE edge SET valid_to = ?, sequence = ?, retired_sequence = ?"
                    " WHERE valid_to IS NULL AND (src = ? OR dst = ?)",
                    (valid_to or 1, sequence, sequence, node_id, node_id),
                )
            return cursor.rowcount > 0

    def retire_edge(self, edge_id: str, *, valid_to: int, sequence: int = 0) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE edge SET valid_to = ?, sequence = ?, retired_sequence = ?"
                " WHERE id = ? AND valid_to IS NULL",
                (valid_to or 1, sequence, sequence, edge_id),
            )
            return cursor.rowcount > 0

    def record_enrichment(self, enrichment: Any, *, sequence: int = 0) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO enrichment (id, subject, attribute, value, layer,"
            " derived_from, confidence, rationale, derived_at, sequence)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                enrichment.id, enrichment.subject, enrichment.attribute,
                json.dumps(enrichment.value, default=str), enrichment.layer,
                json.dumps(list(enrichment.derived_from)), enrichment.confidence,
                enrichment.rationale, enrichment.derived_at, sequence,
            ),
        )

    def clear(self) -> None:
        """Drop the whole read model. The first half of every rebuild."""
        with self._lock:
            connection = self.connection
            for table in (
                "node", "edge", "evidence", "node_evidence", "edge_evidence", "enrichment",
            ):
                connection.execute(f"DELETE FROM {table}")
            if self._fts:
                connection.execute("DELETE FROM node_search")

    # -- reading ---------------------------------------------------------

    def node(self, node_id: str) -> Node | None:
        row = self.connection.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def require(self, node_id: str) -> Node:
        node = self.node(node_id)
        if node is None:
            raise NodeNotFound(node_id)
        return node

    def nodes(
        self, *, kind: NodeKind | None = None, live: bool = True, limit: int = 0
    ) -> tuple[Node, ...]:
        query = "SELECT * FROM node WHERE 1=1"
        parameters: list[Any] = []
        if live:
            query += " AND valid_to IS NULL"
        if kind is not None:
            query += " AND kind = ?"
            parameters.append(kind.value)
        query += " ORDER BY kind, name, version"
        if limit:
            query += " LIMIT ?"
            parameters.append(limit)
        return tuple(self._row_to_node(row) for row in self.connection.execute(query, parameters))

    def by_coordinate(self, coordinate: str, *, live: bool = True) -> tuple[Node, ...]:
        """Every version of one package — what duplicate detection compares."""
        query = "SELECT * FROM node WHERE coordinate = ?"
        if live:
            query += " AND valid_to IS NULL"
        return tuple(
            self._row_to_node(row) for row in self.connection.execute(query, (coordinate,))
        )

    def edge(self, edge_id: str) -> Edge | None:
        row = self.connection.execute("SELECT * FROM edge WHERE id = ?", (edge_id,)).fetchone()
        return self._row_to_edge(row) if row else None

    def edges_from(
        self, node_id: str, *, kind: EdgeKind | None = None, live: bool = True
    ) -> tuple[Edge, ...]:
        return self._edges("src", node_id, kind=kind, live=live)

    def edges_to(
        self, node_id: str, *, kind: EdgeKind | None = None, live: bool = True
    ) -> tuple[Edge, ...]:
        return self._edges("dst", node_id, kind=kind, live=live)

    def _edges(
        self, column: str, node_id: str, *, kind: EdgeKind | None, live: bool
    ) -> tuple[Edge, ...]:
        query = f"SELECT * FROM edge WHERE {column} = ?"
        parameters: list[Any] = [node_id]
        if live:
            query += " AND valid_to IS NULL"
        if kind is not None:
            query += " AND kind = ?"
            parameters.append(kind.value)
        return tuple(self._row_to_edge(row) for row in self.connection.execute(query, parameters))

    def edges(self, *, live: bool = True, limit: int = 0) -> tuple[Edge, ...]:
        query = "SELECT * FROM edge" + (" WHERE valid_to IS NULL" if live else "")
        if limit:
            query += f" LIMIT {int(limit)}"
        return tuple(self._row_to_edge(row) for row in self.connection.execute(query))

    def evidence_for(self, subject_id: str) -> tuple[Evidence, ...]:
        """Evidence for a node or an edge — the caller need not know which."""
        rows = self.connection.execute(
            "SELECT e.* FROM evidence e"
            " JOIN node_evidence ne ON ne.evidence_id = e.id AND ne.node_id = :id"
            " UNION"
            " SELECT e.* FROM evidence e"
            " JOIN edge_evidence ee ON ee.evidence_id = e.id AND ee.edge_id = :id",
            {"id": subject_id},
        )
        return tuple(_row_to_evidence(row) for row in rows)

    def evidence_by_uri(self, uri: str) -> tuple[str, ...]:
        """The invalidation lookup: what did this file justify?"""
        rows = self.connection.execute("SELECT id FROM evidence WHERE uri = ?", (uri,))
        return tuple(row["id"] for row in rows)

    def subjects_of_evidence(self, evidence_ids: Iterable[str]) -> tuple[tuple[str, str], ...]:
        """Which nodes and edges rest on this evidence, as (kind, id) pairs."""
        ids = list(evidence_ids)
        if not ids:
            return ()
        placeholders = ",".join("?" * len(ids))
        rows = self.connection.execute(
            f"SELECT 'node' AS what, node_id AS id FROM node_evidence"
            f" WHERE evidence_id IN ({placeholders})"
            f" UNION SELECT 'edge', edge_id FROM edge_evidence"
            f" WHERE evidence_id IN ({placeholders})",
            [*ids, *ids],
        )
        return tuple((row["what"], row["id"]) for row in rows)

    def search(self, text: str, *, limit: int = 20) -> tuple[Node, ...]:
        """Name search, full-text where available and a scan where it is not."""
        cleaned = text.strip()
        if not cleaned:
            return ()
        if self._fts:
            try:
                rows = self.connection.execute(
                    "SELECT n.* FROM node_search s JOIN node n ON n.id = s.id"
                    " WHERE node_search MATCH ? AND n.valid_to IS NULL LIMIT ?",
                    (f'"{cleaned}"*', limit),
                )
                return tuple(self._row_to_node(row) for row in rows)
            except sqlite3.OperationalError:
                pass  # a query FTS5 cannot parse falls through to the scan
        rows = self.connection.execute(
            "SELECT * FROM node WHERE valid_to IS NULL"
            " AND (name LIKE ? OR identity LIKE ? OR display LIKE ?) LIMIT ?",
            (f"%{cleaned}%", f"%{cleaned}%", f"%{cleaned}%", limit),
        )
        return tuple(self._row_to_node(row) for row in rows)

    # -- traversal -------------------------------------------------------

    def blast_radius(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]:
        """Everything that breaks if this node changes, with path confidence."""
        return self._traverse(BLAST_RADIUS_SQL, node_id, max_depth, min_confidence)

    def reachable(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]:
        """Everything this node transitively depends on."""
        return self._traverse(REACHABLE_SQL, node_id, max_depth, min_confidence)

    def _traverse(
        self, sql: str, node_id: str, max_depth: int, min_confidence: float
    ) -> tuple[tuple[str, int, float], ...]:
        rows = self.connection.execute(
            sql,
            {"root": node_id, "max_depth": max_depth, "min_confidence": min_confidence},
        )
        return tuple((row["node_id"], row["distance"], row["path_confidence"]) for row in rows)

    def cycles(self, *, max_depth: int = 12) -> tuple[tuple[str, ...], ...]:
        """Circular dependencies, shortest first and deduplicated by rotation.

        `a → b → a` and `b → a → b` are one cycle seen from two starting points.
        Reporting both would double every count in the architecture view.
        """
        seen: set[frozenset[str]] = set()
        found: list[tuple[str, ...]] = []
        for row in self.connection.execute(CYCLES_SQL, {"max_depth": max_depth}):
            nodes = tuple(part for part in row["cycle"].split(">") if part)
            signature = frozenset(nodes)
            if signature in seen:
                continue
            seen.add(signature)
            found.append(nodes)
        return tuple(found)

    def paths(
        self, src: str, dst: str, *, max_depth: int = 8, min_confidence: float = 0.0,
        limit: int = 10,
    ) -> tuple[tuple[tuple[str, ...], float], ...]:
        """How does src come to reach dst? Shortest and most confident first."""
        rows = self.connection.execute(
            PATHS_SQL,
            {
                "src": src, "dst": dst, "max_depth": max_depth,
                "min_confidence": min_confidence, "limit": limit,
            },
        )
        return tuple(
            (tuple(part for part in row["path"].split(">") if part), row["min_conf"])
            for row in rows
        )

    def neighbours(self, node_id: str, *, live: bool = True) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT dst AS other FROM edge WHERE src = :id"
            + (" AND valid_to IS NULL" if live else "")
            + " UNION SELECT src FROM edge WHERE dst = :id"
            + (" AND valid_to IS NULL" if live else ""),
            {"id": node_id},
        )
        return tuple(row["other"] for row in rows if row["other"] != node_id)

    def orphans(self) -> tuple[str, ...]:
        """Live nodes nothing points at and which point at nothing."""
        rows = self.connection.execute(
            "SELECT id FROM node n WHERE n.valid_to IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM edge e WHERE e.valid_to IS NULL"
            "                 AND (e.src = n.id OR e.dst = n.id))"
        )
        return tuple(row["id"] for row in rows)

    # -- statistics ------------------------------------------------------

    def counts(self) -> dict[str, int]:
        connection = self.connection
        return {
            "nodes": connection.execute(
                "SELECT COUNT(*) c FROM node WHERE valid_to IS NULL").fetchone()["c"],
            "edges": connection.execute(
                "SELECT COUNT(*) c FROM edge WHERE valid_to IS NULL").fetchone()["c"],
            "evidence": connection.execute(
                "SELECT COUNT(*) c FROM evidence").fetchone()["c"],
            "retired_nodes": connection.execute(
                "SELECT COUNT(*) c FROM node WHERE valid_to IS NOT NULL").fetchone()["c"],
            "retired_edges": connection.execute(
                "SELECT COUNT(*) c FROM edge WHERE valid_to IS NOT NULL").fetchone()["c"],
            "enrichments": connection.execute(
                "SELECT COUNT(*) c FROM enrichment").fetchone()["c"],
        }

    def by_kind(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT kind, COUNT(*) c FROM node WHERE valid_to IS NULL GROUP BY kind"
        )
        return {row["kind"]: row["c"] for row in rows}

    def confidence_distribution(self) -> dict[str, int]:
        """How much of the graph is settled versus inferred versus guessed."""
        rows = self.connection.execute(
            "SELECT CASE"
            "   WHEN confidence >= 0.9 THEN 'settled'"
            "   WHEN confidence >= 0.6 THEN 'probable'"
            "   ELSE 'weak' END AS band, COUNT(*) c"
            " FROM edge WHERE valid_to IS NULL GROUP BY band"
        )
        return {row["band"]: row["c"] for row in rows}

    # -- row mapping -----------------------------------------------------

    # The mapping moved to `rows.py` when a second store arrived: a node row is
    # a node row whichever engine returned it, and two copies would eventually
    # disagree about what a retired node looks like.
    def _row_to_node(self, row: sqlite3.Row) -> Node:
        return rows.to_node(row, self.evidence_for)

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        return rows.to_edge(row, self.evidence_for)

    def __len__(self) -> int:
        return self.counts()["nodes"]

    def __repr__(self) -> str:  # pragma: no cover - display only
        counts = self.counts()
        return f"<SqliteGraph {counts['nodes']} nodes {counts['edges']} edges>"


def _nullable(value: int) -> int | None:
    """OPEN becomes NULL, so the partial indexes on `valid_to IS NULL` apply."""
    return None if value == OPEN else value


def _row_to_evidence(row: sqlite3.Row) -> Evidence:
    return rows.to_evidence(row)


def _placeholder_evidence(subject: str) -> Evidence:
    return rows.placeholder_evidence(subject)
