"""The graph, in Postgres. Same protocol, same answers.

`GraphStore` and `GraphView` are declared in `slpie/graph/store.py` and this
implements them; nothing here is new surface. The row mapping is `slpie.graph.
rows`, shared with the SQLite store, so a node read from either comes back the
same object — which is the only way "the same manifest answers identically
through Postgres as through SQLite" can be a test rather than a hope.

**The four walks are the SQLite ones, translated.** They are not rewritten. The
SQL lives in `slpie.graph.sqlite_graph` as the single statement of what a blast
radius *is*, and `dialect.translate` moves it across — so a change to the
traversal happens once, in ring 0, and both stores get it. Copying the CTEs here
would have been faster to write and would have produced two definitions of
reachability that drift.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from slpie.domain.edge import Edge, EdgeKind
from slpie.domain.evidence import Evidence
from slpie.domain.node import Node, NodeKind
from slpie.graph import rows
from slpie.graph.sqlite_graph import (
    BLAST_RADIUS_SQL,
    CYCLES_SQL,
    PATHS_SQL,
    REACHABLE_SQL,
)

from .dialect import translate
from .engine import Database

#: The four walks, translated once at import rather than per call. They are
#: constant, and re-running four regex passes on every traversal would be work
#: done a million times to produce the same string.
BLAST_RADIUS = translate(BLAST_RADIUS_SQL)
REACHABLE = translate(REACHABLE_SQL)
CYCLES = translate(CYCLES_SQL)
PATHS = translate(PATHS_SQL)


class PostgresGraph:
    """A `GraphStore` over Postgres."""

    def __init__(self, database: Database | str = "") -> None:
        self.db = database if isinstance(database, Database) else Database(str(database))
        self.db.build()

    # -- writing ---------------------------------------------------------

    def assert_node(self, node: Node, *, sequence: int = 0) -> None:
        self._store_evidence(node.evidence)
        with self.db.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO node (
                    id, kind, identity, coordinate, name, version, display,
                    properties, lifecycle, risk, compliance, architecture,
                    confidence, validation, valid_from, valid_to, observed_at,
                    superseded_at, sequence, first_sequence
                ) VALUES (
                    %(id)s, %(kind)s, %(identity)s, %(coordinate)s, %(name)s,
                    %(version)s, %(display)s, %(properties)s, %(lifecycle)s,
                    %(risk)s, %(compliance)s, %(architecture)s, %(confidence)s,
                    %(validation)s, %(valid_from)s, %(valid_to)s,
                    %(observed_at)s, %(superseded_at)s, %(sequence)s, %(sequence)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    identity = EXCLUDED.identity,
                    coordinate = EXCLUDED.coordinate,
                    name = EXCLUDED.name,
                    version = EXCLUDED.version,
                    display = EXCLUDED.display,
                    properties = EXCLUDED.properties,
                    lifecycle = EXCLUDED.lifecycle,
                    risk = EXCLUDED.risk,
                    compliance = EXCLUDED.compliance,
                    architecture = EXCLUDED.architecture,
                    confidence = EXCLUDED.confidence,
                    validation = EXCLUDED.validation,
                    valid_from = EXCLUDED.valid_from,
                    valid_to = EXCLUDED.valid_to,
                    observed_at = EXCLUDED.observed_at,
                    superseded_at = EXCLUDED.superseded_at,
                    sequence = EXCLUDED.sequence
                """,
                {
                    "id": node.id, "kind": node.kind.value,
                    "identity": str(node.identity), "coordinate": node.coordinate,
                    "name": node.name, "version": node.version,
                    "display": node.display,
                    "properties": json.dumps(dict(node.properties)),
                    "lifecycle": node.lifecycle.value, "risk": node.risk.value,
                    "compliance": node.compliance.value,
                    "architecture": node.architecture.value,
                    "confidence": node.confidence,
                    "validation": node.validation.value,
                    "valid_from": node.valid_from,
                    "valid_to": node.valid_to or None,
                    "observed_at": node.observed_at,
                    "superseded_at": node.superseded_at or None,
                    "sequence": sequence,
                },
            )
            for evidence in node.evidence:
                cursor.execute(
                    "INSERT INTO node_evidence (node_id, evidence_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (node.id, evidence.id),
                )

    def assert_edge(self, edge: Edge, *, sequence: int = 0) -> None:
        self._store_evidence(edge.evidence)
        with self.db.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO edge (
                    id, kind, src, dst, qualifier, properties, confidence,
                    validation, propagates, valid_from, valid_to, observed_at,
                    superseded_at, sequence, first_sequence
                ) VALUES (
                    %(id)s, %(kind)s, %(src)s, %(dst)s, %(qualifier)s,
                    %(properties)s, %(confidence)s, %(validation)s,
                    %(propagates)s, %(valid_from)s, %(valid_to)s,
                    %(observed_at)s, %(superseded_at)s, %(sequence)s, %(sequence)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    kind = EXCLUDED.kind, src = EXCLUDED.src, dst = EXCLUDED.dst,
                    qualifier = EXCLUDED.qualifier,
                    properties = EXCLUDED.properties,
                    confidence = EXCLUDED.confidence,
                    validation = EXCLUDED.validation,
                    propagates = EXCLUDED.propagates,
                    valid_from = EXCLUDED.valid_from,
                    valid_to = EXCLUDED.valid_to,
                    observed_at = EXCLUDED.observed_at,
                    superseded_at = EXCLUDED.superseded_at,
                    sequence = EXCLUDED.sequence
                """,
                {
                    "id": edge.id, "kind": edge.kind.value,
                    "src": edge.src, "dst": edge.dst,
                    "qualifier": edge.qualifier,
                    "properties": json.dumps(dict(edge.properties)),
                    "confidence": edge.confidence,
                    "validation": edge.validation.value,
                    "propagates": bool(edge.kind.propagates_impact),
                    "valid_from": edge.valid_from,
                    "valid_to": edge.valid_to or None,
                    "observed_at": edge.observed_at,
                    "superseded_at": edge.superseded_at or None,
                    "sequence": sequence,
                },
            )
            for evidence in edge.evidence:
                cursor.execute(
                    "INSERT INTO edge_evidence (edge_id, evidence_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (edge.id, evidence.id),
                )

    def assert_nodes(self, nodes: Iterable[Node], *, sequence: int = 0) -> None:
        for node in nodes:
            self.assert_node(node, sequence=sequence)

    def assert_edges(self, edges: Iterable[Edge], *, sequence: int = 0) -> None:
        for edge in edges:
            self.assert_edge(edge, sequence=sequence)

    def _store_evidence(self, evidence: Sequence[Evidence]) -> None:
        if not evidence:
            return
        with self.db.transaction() as cursor:
            for item in evidence:
                cursor.execute(
                    """
                    INSERT INTO evidence (
                        id, kind, uri, line, extractor, content_digest, excerpt,
                        observed_at, base_confidence, labels
                    ) VALUES (
                        %(id)s, %(kind)s, %(uri)s, %(line)s, %(extractor)s,
                        %(digest)s, %(excerpt)s, %(observed_at)s, %(base)s,
                        %(labels)s
                    ) ON CONFLICT (id) DO NOTHING
                    """,
                    {
                        "id": item.id, "kind": item.kind.value,
                        "uri": item.location.uri, "line": item.location.line,
                        "extractor": item.extractor,
                        "digest": item.content_digest,
                        "excerpt": item.excerpt,
                        "observed_at": item.observed_at,
                        "base": item.kind.base_confidence,
                        "labels": json.dumps(dict(item.labels or {})),
                    },
                )

    def retire_node(self, node_id: str, *, valid_to: int, sequence: int = 0) -> bool:
        with self.db.transaction() as cursor:
            cursor.execute(
                "UPDATE node SET valid_to = %s, retired_sequence = %s "
                "WHERE id = %s AND valid_to IS NULL",
                (valid_to, sequence, node_id),
            )
            return cursor.rowcount > 0

    def retire_edge(self, edge_id: str, *, valid_to: int, sequence: int = 0) -> bool:
        with self.db.transaction() as cursor:
            cursor.execute(
                "UPDATE edge SET valid_to = %s, retired_sequence = %s "
                "WHERE id = %s AND valid_to IS NULL",
                (valid_to, sequence, edge_id),
            )
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self.db.transaction() as cursor:
            cursor.execute(
                "TRUNCATE node, edge, evidence, node_evidence, edge_evidence, "
                "enrichment, snapshot"
            )

    # -- reading ---------------------------------------------------------

    def _all(self, sql: str, parameters: Any = None) -> list[dict[str, Any]]:
        with self.db.connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return list(cursor.fetchall())

    def node(self, node_id: str) -> Node | None:
        found = self._all("SELECT * FROM node WHERE id = %s", (node_id,))
        return rows.to_node(found[0], self.evidence_for) if found else None

    def nodes(
        self, *, kind: NodeKind | None = None, live: bool = True, limit: int = 0
    ) -> tuple[Node, ...]:
        sql = "SELECT * FROM node WHERE TRUE"
        values: list[Any] = []
        if live:
            sql += " AND valid_to IS NULL"
        if kind is not None:
            sql += " AND kind = %s"
            values.append(kind.value)
        sql += " ORDER BY kind, name, version"
        if limit:
            sql += " LIMIT %s"
            values.append(limit)
        return tuple(rows.to_node(row, self.evidence_for) for row in self._all(sql, values))

    def by_coordinate(self, coordinate: str, *, live: bool = True) -> tuple[Node, ...]:
        sql = "SELECT * FROM node WHERE coordinate = %s"
        if live:
            sql += " AND valid_to IS NULL"
        return tuple(
            rows.to_node(row, self.evidence_for) for row in self._all(sql, (coordinate,))
        )

    def edge(self, edge_id: str) -> Edge | None:
        found = self._all("SELECT * FROM edge WHERE id = %s", (edge_id,))
        return rows.to_edge(found[0], self.evidence_for) if found else None

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
        sql = f"SELECT * FROM edge WHERE {column} = %s"
        values: list[Any] = [node_id]
        if live:
            sql += " AND valid_to IS NULL"
        if kind is not None:
            sql += " AND kind = %s"
            values.append(kind.value)
        return tuple(rows.to_edge(row, self.evidence_for) for row in self._all(sql, values))

    def edges(self, *, live: bool = True, limit: int = 0) -> tuple[Edge, ...]:
        sql = "SELECT * FROM edge" + (" WHERE valid_to IS NULL" if live else "")
        values: list[Any] = []
        if limit:
            sql += " LIMIT %s"
            values.append(limit)
        return tuple(rows.to_edge(row, self.evidence_for) for row in self._all(sql, values))

    def evidence_for(self, subject_id: str) -> tuple[Evidence, ...]:
        found = self._all(
            """
            SELECT e.* FROM evidence e
            JOIN node_evidence ne ON ne.evidence_id = e.id AND ne.node_id = %(id)s
            UNION
            SELECT e.* FROM evidence e
            JOIN edge_evidence ee ON ee.evidence_id = e.id AND ee.edge_id = %(id)s
            """,
            {"id": subject_id},
        )
        return tuple(rows.to_evidence(row) for row in found)

    def evidence_by_uri(self, uri: str) -> tuple[str, ...]:
        return tuple(
            row["id"] for row in self._all("SELECT id FROM evidence WHERE uri = %s", (uri,))
        )

    def search(self, text: str, *, limit: int = 20) -> tuple[Node, ...]:
        found = self._all(
            "SELECT * FROM node WHERE valid_to IS NULL "
            "AND (name ILIKE %(like)s OR display ILIKE %(like)s "
            "OR identity ILIKE %(like)s) "
            "ORDER BY name LIMIT %(limit)s",
            {"like": f"%{text}%", "limit": limit},
        )
        return tuple(rows.to_node(row, self.evidence_for) for row in found)

    def counts(self) -> dict[str, int]:
        row = self._all(
            """
            SELECT
              (SELECT count(*) FROM node WHERE valid_to IS NULL)     AS nodes,
              (SELECT count(*) FROM edge WHERE valid_to IS NULL)     AS edges,
              (SELECT count(*) FROM evidence)                        AS evidence,
              (SELECT count(*) FROM node WHERE valid_to IS NOT NULL) AS retired_nodes,
              (SELECT count(*) FROM edge WHERE valid_to IS NOT NULL) AS retired_edges,
              (SELECT count(*) FROM enrichment)                      AS enrichments
            """
        )[0]
        return {key: int(value) for key, value in row.items()}

    # -- traversal, translated from ring 0's own SQL ---------------------

    def blast_radius(
        self, root: str, *, max_depth: int = 8, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]:
        return self._walk(BLAST_RADIUS, root, max_depth, min_confidence)

    def reachable(
        self, root: str, *, max_depth: int = 8, min_confidence: float = 0.0
    ) -> tuple[tuple[str, int, float], ...]:
        return self._walk(REACHABLE, root, max_depth, min_confidence)

    def _walk(
        self, sql: str, root: str, max_depth: int, min_confidence: float
    ) -> tuple[tuple[str, int, float], ...]:
        found = self._all(sql, {
            "root": root, "max_depth": max_depth, "min_confidence": min_confidence,
        })
        return tuple(
            (row["node_id"], int(row["distance"]), float(row["path_confidence"]))
            for row in found
        )

    def cycles(self, *, max_depth: int = 12) -> tuple[tuple[str, ...], ...]:
        found = self._all(CYCLES, {"max_depth": max_depth})
        return tuple(
            tuple(part for part in str(row["cycle"]).split(">") if part) for row in found
        )

    def paths(
        self, src: str, dst: str, *, max_depth: int = 8,
        min_confidence: float = 0.0, limit: int = 10,
    ) -> tuple[tuple[tuple[str, ...], int, float], ...]:
        found = self._all(PATHS, {
            "src": src, "dst": dst, "max_depth": max_depth,
            "min_confidence": min_confidence, "limit": limit,
        })
        return tuple(
            (
                tuple(part for part in str(row["path"]).split(">") if part),
                int(row["depth"]),
                float(row["min_conf"]),
            )
            for row in found
        )

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self.db.close()

    def __len__(self) -> int:
        return self.counts()["nodes"]

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<PostgresGraph {self.db.url!r}>"
