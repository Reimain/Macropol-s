"""Rows to domain objects — the mapping, once.

Extracted from :mod:`slpie.graph.sqlite_graph` when a second store arrived. The
mapping is **schema-specific, not SQLite-specific**: both stores build the same
tables from :mod:`slpie.graph.schema`, so a node row is a node row whichever
engine returned it, and two copies of this would be two copies that eventually
disagree about what a retired node looks like.

Every function takes a mapping-like row — `sqlite3.Row` and psycopg's
`dict_row` both answer `row["column"]` — and an `evidence` callable, because
reconstructing a node needs the evidence attached to it and only the store
knows how to fetch that.

Public on purpose. Ring 1 imports ring 0's published API and nothing private
(§22), and this is the seam a second persistence layer needs.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.lifecycle import ArchitectureClass, ComplianceState, LifecycleState, RiskClass
from ..domain.node import OPEN, Node, NodeKind
from ..domain.identity import parse_identity

Row = Mapping[str, Any]
Evidences = Callable[[str], tuple[Evidence, ...]]


def placeholder_evidence(subject: str) -> Evidence:
    """A marker for a row whose evidence rows are missing.

    Reconstructing a node with no evidence would raise, so a corrupted read
    model would take the whole platform down on a read. Instead the row comes
    back carrying a `name_heuristic` marker naming itself — visibly wrong, at
    the lowest possible confidence, and impossible to mistake for a real
    observation.
    """
    return Evidence(
        kind=EvidenceKind.NAME_HEURISTIC,
        location=SourceLocation(f"slpie://graph/orphaned-row/{subject}"),
        extractor="graph.projection",
        excerpt="evidence rows missing for this subject; rebuild the projection",
    )


def to_node(row: Row, evidence: Evidences) -> Node:
    found = evidence(row["id"])
    return Node(
        kind=NodeKind(row["kind"]),
        identity=parse_identity(row["identity"]),
        evidence=found or (placeholder_evidence(row["identity"]),),
        properties=_json(row["properties"], {}),
        lifecycle=LifecycleState(row["lifecycle"]),
        risk=RiskClass(row["risk"]),
        compliance=ComplianceState(row["compliance"]),
        architecture=ArchitectureClass(row["architecture"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"] or OPEN,
        observed_at=row["observed_at"],
        superseded_at=row["superseded_at"] or OPEN,
    )


def to_edge(row: Row, evidence: Evidences) -> Edge:
    found = evidence(row["id"])
    return Edge(
        kind=EdgeKind(row["kind"]),
        src=row["src"],
        dst=row["dst"],
        evidence=found or (placeholder_evidence(row["id"]),),
        properties=_json(row["properties"], {}),
        qualifier=row["qualifier"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"] or OPEN,
        observed_at=row["observed_at"],
        superseded_at=row["superseded_at"] or OPEN,
    )


def to_evidence(row: Row) -> Evidence:
    return Evidence(
        kind=EvidenceKind(row["kind"]),
        location=SourceLocation(row["uri"], line=row["line"]),
        extractor=row["extractor"],
        content_digest=row["content_digest"],
        excerpt=row["excerpt"],
        observed_at=row["observed_at"],
        labels=labels(row),
    )


def labels(row: Row) -> dict[str, str]:
    """Evidence labels, tolerating a row written before the column existed."""
    try:
        raw = row["labels"]
    except (IndexError, KeyError):
        return {}
    decoded = _json(raw, {})
    return {str(k): str(v) for k, v in decoded.items()} if isinstance(decoded, dict) else {}


def _json(raw: Any, fallback: Any) -> Any:
    """Decode a JSON column, tolerating a driver that already did.

    psycopg returns `jsonb` as a Python object and `text` as a string; SQLite
    always returns a string. Accepting both is what lets one mapper serve two
    drivers without either of them caring which columns the other chose.
    """
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback
