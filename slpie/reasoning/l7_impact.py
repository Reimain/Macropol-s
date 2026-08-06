"""L7 — what else is reached, and how confidently each hop is believed.

Blast radius, computed over the resolution's own links rather than over a graph
database. That matters for reach: `discover . | reason` answers "what depends on
this" on a bare checkout with no manifest, no ledger and no SQLite — and the
in-SQL traversal (§12) remains the right implementation once an environment
exists, over the same shape.

Two decisions carry the layer:

**Confidence propagates as a minimum, not a product.** A path reached only via a
0.4 dynamic load is a 0.4 path however many certain hops preceded it — a chain is
exactly as strong as its weakest link. Multiplying instead would make a long chain
of certainties decay towards zero, which reports well-evidenced structure as
doubt.

**Cycles are guarded by path, not by a visited set.** A visited set makes the
answer depend on traversal order: whichever branch reached a node first claims it,
and the other is silently truncated. Carrying the path lets every distinct route
be explored while still terminating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import ReasoningError
from .layer import BaseLayer, LayerContext

#: How far to walk. Beyond this a "blast radius" is the whole graph, which tells
#: nobody anything they can act on.
MAX_DEPTH = 8

#: Only nodes with at least this many dependents get an enrichment. A leaf's
#: blast radius is itself, and emitting one per package buries the hubs.
NOTABLE = 1


@dataclass(frozen=True, slots=True)
class Reach:
    """One node's blast radius: what it reaches, how far, how confidently."""

    node: str
    identity: str = ""
    dependents: tuple[str, ...] = ()
    distance: Mapping[str, int] = field(default_factory=dict)
    confidence: Mapping[str, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.dependents)

    @property
    def weakest(self) -> float:
        """The least confident path in the radius. What an operator should read."""
        return min(self.confidence.values(), default=1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node, "identity": self.identity, "size": self.size,
            "dependents": list(self.dependents[:50]),
            "weakest_path": round(self.weakest, 4),
            "furthest": max(self.distance.values(), default=0),
        }

    def __str__(self) -> str:
        return f"{self.identity or self.node}: {self.size} dependent(s)"


class ImpactLayer(BaseLayer):
    """Reverse reachability over what the resolution linked."""

    name = "impact"
    number = LayerNumber.IMPACT

    def __init__(self, *, max_depth: int = MAX_DEPTH, notable: int = NOTABLE) -> None:
        self.max_depth = max_depth
        self.notable = notable

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        resolution = context.resolution
        if resolution is None:
            raise ReasoningError(
                "impact needs a resolved graph; reverse reachability over nothing "
                "would report every package as reaching nobody"
            )

        links = tuple(getattr(resolution, "links", ()))
        if not links:
            context.limited_by(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject=context.element or "impact",
                detail=(
                    "nothing links to anything in this tree, so there is no "
                    "blast radius to compute"
                ),
                remediation=(
                    "impact needs edges; a tree of standalone files has none, "
                    "and no radius is reported rather than an empty one"
                ),
                confidence_impact=0.15,
            ))
            step(ReasoningStep(
                claim=(
                    "nothing links to anything in this tree, so there is no blast "
                    "radius to compute"
                ),
                layer=self.name, operation="traverse",
            ))
            context.facts["impact"] = {"nodes": 0}
            return

        incoming: dict[str, list[tuple[str, float, str]]] = {}
        for link in links:
            confidence = getattr(link.evidence, "base_confidence", 1.0)
            incoming.setdefault(link.target, []).append(
                (link.source, confidence, link.evidence.id),
            )

        identities = {
            entry.node_id: entry.identity
            for entry in getattr(resolution, "resolved", ())
        }
        evidence_of = {
            entry.node_id: tuple(item.id for item in entry.evidence)
            for entry in getattr(resolution, "resolved", ())
        }

        reaches: list[Reach] = []
        for node in sorted(incoming):
            reach = self._walk(node, incoming, identities)
            if reach.size < self.notable:
                continue
            reaches.append(reach)

            emit(self.enrich(
                node, "blast_radius", reach.size,
                derived_from=evidence_of.get(node, ()) or tuple(
                    item for _source, _confidence, item in incoming[node][:3]
                ),
                confidence=reach.weakest,
                rationale=(
                    f"{reach.size} thing(s) depend on {identities.get(node, node)}, "
                    f"the furthest {max(reach.distance.values(), default=0)} hop(s) "
                    f"away; the least certain path is believed at {reach.weakest:.2f}"
                ),
            ))

        reaches.sort(key=lambda item: (-item.size, item.node))
        context.facts["impact"] = {
            "nodes": len(reaches),
            "largest": reaches[0].size if reaches else 0,
            "hubs": [item.identity or item.node for item in reaches[:5]],
        }
        context.facts["reaches"] = tuple(reaches)

        step(ReasoningStep(
            claim=(
                f"reverse reachability over {len(links)} link(s): "
                f"{len(reaches)} node(s) have dependents"
                + (
                    f", the largest reaching {reaches[0].size}"
                    if reaches else ""
                )
            ),
            layer=self.name, operation="traverse",
            evidence=tuple(context.evidence.values())[:6],
        ))

        for reach in reaches[:3]:
            step(ReasoningStep(
                claim=(
                    f"{reach.identity or reach.node} is depended on by "
                    f"{reach.size}; changing it reaches all of them"
                ),
                layer=self.name, operation="traverse",
                confidence=reach.weakest,
                evidence=tuple(
                    context.evidence[item]
                    for item in evidence_of.get(reach.node, ())
                    if item in context.evidence
                )[:2],
            ))

    def _walk(
        self,
        root: str,
        incoming: Mapping[str, Sequence[tuple[str, float, str]]],
        identities: Mapping[str, str],
    ) -> Reach:
        """Breadth-first up the dependency edges, carrying the path.

        The path guard rather than a visited set: a visited set makes the result
        depend on traversal order, because whichever branch reaches a node first
        claims it and the other is truncated without saying so.
        """
        distance: dict[str, int] = {}
        confidence: dict[str, float] = {}
        frontier: list[tuple[str, int, float, tuple[str, ...]]] = [
            (root, 0, 1.0, (root,))
        ]

        while frontier:
            node, depth, carried, path = frontier.pop(0)
            if depth >= self.max_depth:
                continue
            for source, hop, _evidence in incoming.get(node, ()):
                if source in path:
                    continue      # a cycle; this route ends, others continue
                # Minimum, not product: a chain is as strong as its weakest link,
                # and multiplying would decay a long run of certainties to nothing.
                reached = min(carried, hop)
                previous = confidence.get(source)
                if previous is None or reached > previous:
                    confidence[source] = reached
                if source not in distance or depth + 1 < distance[source]:
                    distance[source] = depth + 1
                frontier.append((source, depth + 1, reached, (*path, source)))

        return Reach(
            node=root, identity=identities.get(root, ""),
            dependents=tuple(sorted(distance)),
            distance=distance, confidence=confidence,
        )
