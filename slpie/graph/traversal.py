"""Traversal results, shaped for explanation rather than for computation.

The store returns tuples because that is what SQL yields. This module turns them
into answers a person can read: an impact result knows its own weakest link, a
cycle knows whether it is structural, and both can say which evidence they rest
on. Every one of these carries confidence, because "42 components are affected"
and "42 components are affected, 11 of them only through dynamic loading" are
different findings and only the second is honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..domain.evidence import SETTLED
from ..domain.node import Node


@dataclass(frozen=True, slots=True)
class Impacted:
    """One node reached by a traversal, with how far and how certainly."""

    node_id: str
    distance: int
    confidence: float
    display: str = ""
    kind: str = ""

    @property
    def certain(self) -> bool:
        return self.confidence >= SETTLED

    @property
    def direct(self) -> bool:
        return self.distance == 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "distance": self.distance,
            "confidence": self.confidence, "display": self.display,
            "kind": self.kind, "certain": self.certain,
        }

    def __str__(self) -> str:
        return f"{self.display or self.node_id[:12]} (depth {self.distance}, {self.confidence:.2f})"


@dataclass(frozen=True, slots=True)
class ImpactResult:
    """A blast radius, with the honesty about uncertainty built in."""

    root: str
    impacted: tuple[Impacted, ...] = ()
    max_depth: int = 0
    min_confidence: float = 0.0
    truncated: bool = False

    @property
    def total(self) -> int:
        return len(self.impacted)

    @property
    def certain(self) -> tuple[Impacted, ...]:
        return tuple(item for item in self.impacted if item.certain)

    @property
    def uncertain(self) -> tuple[Impacted, ...]:
        """Reached only through weak edges — reported separately, never merged."""
        return tuple(item for item in self.impacted if not item.certain)

    @property
    def direct(self) -> tuple[Impacted, ...]:
        return tuple(item for item in self.impacted if item.direct)

    def deepest(self) -> int:
        return max((item.distance for item in self.impacted), default=0)

    def summary(self) -> str:
        if not self.impacted:
            return "nothing depends on this"
        text = f"{self.total} affected, {len(self.direct)} directly"
        if self.uncertain:
            text += f"; {len(self.uncertain)} only via low-confidence paths"
        if self.truncated:
            text += f" (truncated at depth {self.max_depth})"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root, "total": self.total, "summary": self.summary(),
            "max_depth": self.max_depth, "deepest": self.deepest(),
            "truncated": self.truncated,
            "impacted": [item.to_dict() for item in self.impacted],
            "certain": len(self.certain), "uncertain": len(self.uncertain),
        }

    def __len__(self) -> int:
        return len(self.impacted)

    def __iter__(self):
        return iter(self.impacted)


@dataclass(frozen=True, slots=True)
class Cycle:
    """A circular dependency, normalised so it compares equal from any entry."""

    nodes: tuple[str, ...]
    displays: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        # The closing node repeats the opening one, so it is not counted twice.
        return len(self.nodes) - 1 if self._closed else len(self.nodes)

    @property
    def _closed(self) -> bool:
        return len(self.nodes) > 1 and self.nodes[0] == self.nodes[-1]

    @property
    def signature(self) -> frozenset[str]:
        return frozenset(self.nodes)

    def render(self) -> str:
        names = self.displays or self.nodes
        return " → ".join(name[:20] for name in names)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes), "length": self.length, "render": self.render()}

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True, slots=True)
class Path:
    """One route between two nodes, with the confidence of its weakest hop."""

    nodes: tuple[str, ...]
    confidence: float = 0.0
    displays: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        return max(len(self.nodes) - 1, 0)

    def render(self) -> str:
        names = self.displays or self.nodes
        return " → ".join(name[:20] for name in names)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(self.nodes), "length": self.length,
            "confidence": self.confidence, "render": self.render(),
        }


class Traverser:
    """Turns a graph's raw traversal output into explainable results.

    A thin layer on purpose: the work happens in SQL, and this only decorates it
    with the display names and the certain/uncertain split that an explanation
    needs.
    """

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def impact(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> ImpactResult:
        """What breaks if this changes."""
        rows = self.graph.blast_radius(
            node_id, max_depth=max_depth, min_confidence=min_confidence
        )
        return self._to_result(node_id, rows, max_depth, min_confidence)

    def dependencies(
        self, node_id: str, *, max_depth: int = 10, min_confidence: float = 0.0
    ) -> ImpactResult:
        """What this transitively rests on."""
        rows = self.graph.reachable(
            node_id, max_depth=max_depth, min_confidence=min_confidence
        )
        return self._to_result(node_id, rows, max_depth, min_confidence)

    def _to_result(
        self, root: str, rows: Sequence[tuple[str, int, float]],
        max_depth: int, min_confidence: float,
    ) -> ImpactResult:
        displays = self._displays(row[0] for row in rows)
        impacted = tuple(
            Impacted(
                node_id=node_id, distance=distance, confidence=confidence,
                display=displays.get(node_id, ("", ""))[0],
                kind=displays.get(node_id, ("", ""))[1],
            )
            for node_id, distance, confidence in rows
        )
        return ImpactResult(
            root=root, impacted=impacted, max_depth=max_depth,
            min_confidence=min_confidence,
            truncated=any(item.distance >= max_depth for item in impacted),
        )

    def cycles(self, *, max_depth: int = 12) -> tuple[Cycle, ...]:
        raw = self.graph.cycles(max_depth=max_depth)
        displays = self._displays(node for cycle in raw for node in cycle)
        return tuple(
            Cycle(
                nodes=cycle,
                displays=tuple(displays.get(node, (node, ""))[0] or node for node in cycle),
            )
            for cycle in raw
        )

    def paths(
        self, src: str, dst: str, *, max_depth: int = 8, limit: int = 10,
        min_confidence: float = 0.0,
    ) -> tuple[Path, ...]:
        raw = self.graph.paths(
            src, dst, max_depth=max_depth, limit=limit, min_confidence=min_confidence
        )
        displays = self._displays(node for nodes, _ in raw for node in nodes)
        return tuple(
            Path(
                nodes=nodes, confidence=confidence,
                displays=tuple(displays.get(node, (node, ""))[0] or node for node in nodes),
            )
            for nodes, confidence in raw
        )

    def _displays(self, node_ids: Iterable[str]) -> dict[str, tuple[str, str]]:
        """One query for every label, rather than one per result row."""
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.graph.connection.execute(
            f"SELECT id, display, kind FROM node WHERE id IN ({placeholders})", ids
        )
        return {row["id"]: (row["display"], row["kind"]) for row in rows}
