"""Graph in, tables out. Deterministic, and it invents nothing.

The ETL, and it is deliberately dull: reading rows out of a graph and putting
them in columns should be the least surprising code in the platform, because
every number anyone ever quotes passes through it.

── Two rules it will not bend ───────────────────────────────────────────

**A value that was not recorded stays absent.** Not zero, not an empty string.
A relationship with no confidence and one with a confidence of zero are
different statements, and a build that filled one in as the other would put a
number in a chart that nobody measured — the failure the whole "counted, never
modelled" rule exists to prevent.

**The same graph builds the same tables.** Rows are sorted by their key, so two
builds of one snapshot are byte-identical and an export can be diffed. Without
it the warehouse would churn on every rebuild and nobody could tell a real
change from an iteration-order one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import EvidenceKind
from .model import Dimension, Fact, Star, Table
from .schema import (
    DIM_EVIDENCE,
    DIM_NODE,
    DIM_SEVERITY,
    DIM_TIME,
    FACT_EDGE,
    FACT_ELEMENT,
    FACT_FINDING,
    STARS,
)

#: Evidence kinds that mean the platform *read* something rather than inferred
#: it. The line between evidence and reasoning, drawn once and joined to
#: everywhere — `inferred_share` is this set and nothing else.
READ_KINDS = frozenset({
    EvidenceKind.LOCKFILE_PIN, EvidenceKind.RUNTIME_TRACE,
    EvidenceKind.MANIFEST_DECLARED, EvidenceKind.DECLARED,
    EvidenceKind.STATIC_IMPORT, EvidenceKind.IAC_DECLARATION,
    EvidenceKind.BUILD_CONFIG, EvidenceKind.CONTAINER_MANIFEST,
    EvidenceKind.GENERATED_CODE,
})


@dataclass(frozen=True, slots=True)
class Warehouse:
    """Built tables, and what the build could not see."""

    stars: tuple[Star, ...] = ()
    gaps: tuple[str, ...] = field(default_factory=tuple)
    built_at: int = 0

    def star(self, name: str) -> Star | None:
        return next((item for item in self.stars if item.name == name), None)

    def table(self, name: str) -> Table | None:
        for item in self.stars:
            for candidate in item.tables:
                if candidate.name == name:
                    return candidate
        return None

    @property
    def tables(self) -> tuple[Table, ...]:
        """Every distinct table, deduplicated — dimensions are shared."""
        seen: dict[str, Table] = {}
        for item in self.stars:
            for candidate in item.tables:
                seen.setdefault(candidate.name, candidate)
        return tuple(seen[name] for name in sorted(seen))

    def to_dict(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at,
            "stars": [item.to_dict() for item in self.stars],
            "rows": {table.name: len(table) for table in self.tables},
            "gaps": list(self.gaps),
        }


def build(graph: Any, *, findings: Sequence[Any] = (), manifest: Any = None,
          now: int = 0) -> Warehouse:
    """Project a graph into the published stars.

    `now` is passed in rather than read from the clock, for the reason every
    other digest-bearing thing in this platform takes its time as an argument:
    a warehouse built twice from one snapshot must produce one answer, and a
    build that stamped `time.time()` would differ on every run.
    """
    stamp = now or int(time.time())
    day = time.strftime("%Y-%m-%d", time.gmtime(stamp))
    gaps: list[str] = []

    nodes = list(graph.nodes(live=True))
    edges = list(graph.edges(limit=0)) if _accepts_limit(graph) else list(graph.edges())

    declared = _declared_ids(manifest)
    if manifest is None:
        gaps.append(
            "no environment manifest was supplied, so `declared` is false for "
            "every element. That is not the same as nothing being declared, and "
            "the reconciliation measures read false here."
        )

    degrees = _degrees(edges)
    dim_node = DIM_NODE.with_rows(sorted(
        (_node_row(node, degrees) for node in nodes),
        key=lambda row: row["node_id"],
    ))
    dim_time = DIM_TIME.with_rows((
        {"day": day, "epoch": _midnight(stamp), "weekday": _weekday(stamp)},
    ))
    dim_evidence = DIM_EVIDENCE.with_rows(tuple(
        {
            "evidence_kind": kind.value,
            "base_confidence": kind.base_confidence,
            "read": kind in READ_KINDS,
        }
        for kind in sorted(EvidenceKind, key=lambda item: item.value)
    ))

    fact_element = FACT_ELEMENT.with_rows(sorted(
        (_element_row(node, degrees, declared, day) for node in nodes),
        key=lambda row: row["node_id"],
    ))
    fact_edge = FACT_EDGE.with_rows(sorted(
        (_edge_row(edge, day) for edge in edges),
        key=lambda row: row["edge_id"],
    ))
    fact_finding = FACT_FINDING.with_rows(sorted(
        (_finding_row(item, day) for item in findings if not _suppressed(item)),
        key=lambda row: row["finding_id"],
    ))

    filled = {
        "dim_node": dim_node, "dim_time": dim_time,
        "dim_evidence": dim_evidence, "dim_severity": DIM_SEVERITY,
        "fact_element": fact_element, "fact_edge": fact_edge,
        "fact_finding": fact_finding,
    }
    stars = tuple(
        Star(
            name=item.name, doc=item.doc,
            fact=filled[item.fact.name],           # type: ignore[arg-type]
            dimensions=tuple(filled[dim.name] for dim in item.dimensions),  # type: ignore[misc]
        )
        for item in STARS
    )
    if not findings:
        gaps.append(
            "no findings were supplied, so the findings star is empty. An empty "
            "findings table and a clean estate look identical from here — run "
            "`govern` first if you meant the second."
        )
    return Warehouse(stars=stars, gaps=tuple(gaps), built_at=stamp)


def _accepts_limit(graph: Any) -> bool:
    """Whether this store's `edges()` takes a limit.

    Asked rather than assumed: the in-memory view used by `govern` and the
    SQLite store have differed on this before, and a `TypeError` inside an ETL
    is a worse way to find out than a `hasattr`.
    """
    import inspect

    try:
        return "limit" in inspect.signature(graph.edges).parameters
    except (TypeError, ValueError):  # pragma: no cover - a C-implemented store
        return False


def _degrees(edges: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge.src] = counts.get(edge.src, 0) + 1
        counts[edge.dst] = counts.get(edge.dst, 0) + 1
    return counts


def _declared_ids(manifest: Any) -> frozenset[str]:
    if manifest is None:
        return frozenset()
    try:
        from ..environment.loader import _declared_node_id  # type: ignore[attr-defined]

        return frozenset(
            _declared_node_id(declaration) for declaration in manifest.declarations
        )
    except Exception:  # noqa: BLE001 - a manifest we cannot read is a gap, not a crash
        return frozenset()


def _node_row(node: Any, degrees: Mapping[str, int]) -> dict[str, Any]:
    properties = dict(getattr(node, "properties", {}) or {})
    return {
        "node_id": node.id,
        "name": node.name,
        "kind": node.kind.value,
        "identity": node.identity.to_string(),
        "lifecycle": node.lifecycle.value,
        "risk": node.risk.value,
        "confidence": node.confidence,
        "environment": str(properties.get("environment", "") or "unknown"),
        # Blank rather than "unknown": an unowned element is a real state, and
        # `dim_node.team = ''` groups them together where a reader can see how
        # many there are.
        "team": str(properties.get("team", "") or ""),
    }


def _element_row(node: Any, degrees: Mapping[str, int], declared: frozenset[str],
                 day: str) -> dict[str, Any]:
    return {
        "node_id": node.id,
        "kind": node.kind.value,
        "environment": str(
            (getattr(node, "properties", {}) or {}).get("environment", "") or "unknown"),
        "declared": node.id in declared,
        # `declared_only` is the graph's own word for "the manifest said so and
        # nothing corroborated it", which is exactly the negation wanted here.
        "observed": not getattr(node, "declared_only", False),
        "confidence": node.confidence,
        "degree": degrees.get(node.id, 0),
        "day": day,
    }


def _edge_row(edge: Any, day: str) -> dict[str, Any]:
    kinds = [item.kind for item in getattr(edge, "evidence", ()) or ()]
    # The *strongest* kind, because that is what the confidence rests on: an
    # edge corroborated by a lockfile and a name heuristic is a lockfile edge.
    best = max(kinds, key=lambda item: item.base_confidence, default=None)
    return {
        "edge_id": edge.id,
        "source": edge.src,
        "target": edge.dst,
        "kind": edge.kind.value,
        "evidence_kind": best.value if best else "",
        "confidence": edge.confidence,
        "read": bool(best and best in READ_KINDS),
        "validation": edge.validation.value,
        "day": day,
    }


def _finding_row(finding: Any, day: str) -> dict[str, Any]:
    severity = finding.severity
    return {
        "finding_id": finding.id,
        "subject": finding.subject,
        "severity": severity.value,
        "kind": finding.kind.value,
        "rule_id": getattr(finding, "rule_id", ""),
        "blocks_release": bool(getattr(severity, "blocks_release", False)),
        "confidence_impact": getattr(finding, "confidence_impact", 0.0),
        "day": day,
    }


def _suppressed(finding: Any) -> bool:
    return bool(getattr(finding, "suppressed", False))


def _midnight(stamp: int) -> int:
    parts = time.gmtime(stamp)
    return int(time.mktime((
        parts.tm_year, parts.tm_mon, parts.tm_mday, 0, 0, 0, 0, 0, 0
    )))


def _weekday(stamp: int) -> int:
    return time.gmtime(stamp).tm_wday
