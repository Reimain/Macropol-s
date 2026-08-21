"""L3 — observations collapse into nodes and edges, and disagreement surfaces.

The resolver does the merging; this layer records what it decided and, more
importantly, what it *found* while deciding. Two facts come out of the merge
that no single discoverer could have produced:

* **Corroboration.** Two independent files reaching the same identity is
  stronger evidence than one file reaching it twice, and the resolver counts by
  file for exactly that reason. The enrichment says how many distinct sources
  agreed, which is what the confidence derivation upstream consumes.
* **Contradiction.** Two lockfiles pinning the same package to different
  versions is not a merge conflict to be resolved by picking one. It is a
  finding, and this layer emits it as one — with both versions named, because
  "there is a contradiction" without saying between what is unactionable.

The layer is otherwise thin on purpose. Graph construction is the resolver's
job; if this file started making identity decisions there would be two notions
of "the same package" in the codebase, and the second one always drifts.
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..linking.resolver import Resolver
from .layer import BaseLayer, LayerContext


class GraphConstructionLayer(BaseLayer):
    """Merge on identity, count corroboration, name contradictions."""

    name = "graph_construction"
    number = LayerNumber.GRAPH_CONSTRUCTION

    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver = resolver or Resolver()

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        first_order = {
            enrichment.subject: enrichment
            for enrichment in context.of_layer("discovery")
        }

        resolution = self.resolver.resolve(context.observations)
        context.resolution = resolution

        for entry in resolution.resolved:
            chain = tuple(dict.fromkeys(
                [
                    first_order[observation.subject].id
                    for observation in entry.observations
                    if observation.subject in first_order
                ]
                + [item.id for item in entry.evidence]
            ))
            if not chain:
                continue

            emit(self.enrich(
                entry.node_id, "identity", entry.identity,
                derived_from=chain,
                rationale=(
                    f"{len(entry.observations)} observation(s) across "
                    f"{len({e.location.uri for e in entry.evidence})} file(s) "
                    f"resolve to this identity"
                ),
            ))

            if entry.corroborated:
                sources = sorted({e.location.uri for e in entry.evidence})
                emit(self.enrich(
                    entry.node_id, "corroborated_by", tuple(sources),
                    derived_from=chain,
                    rationale=(
                        f"{len(sources)} independent files name it; corroboration "
                        f"is counted per file so re-reading one never compounds"
                    ),
                ))

            if entry.contradicted:
                emit(self.enrich(
                    entry.node_id, "contradiction", tuple(entry.versions),
                    derived_from=chain,
                    rationale=(
                        f"{entry.identity} is pinned to "
                        f"{' and '.join(entry.versions)} in different lockfiles; "
                        f"somebody's build is not what they think it is"
                    ),
                ))

        for link in resolution.links:
            reference = context.remember(link.evidence)
            emit(self.enrich(
                link.id, f"edge:{link.kind}", link.target,
                derived_from=(reference,),
                confidence=link.evidence.base_confidence,
                rationale=(
                    f"{link.evidence.location.reference} states this "
                    f"{link.kind} relationship"
                ),
            ))

        context.facts["nodes"] = len(resolution.resolved)
        context.facts["edges"] = len(resolution.links)
        context.facts["merged"] = resolution.merged
        context.facts["unresolved"] = len(resolution.unresolved)
        context.facts["contradictions"] = len(resolution.contradictions())

        step(ReasoningStep(
            claim=(
                f"{len(resolution.resolved)} nodes and {len(resolution.links)} "
                f"edges; {resolution.merged} observation(s) merged onto an "
                f"identity that was already known"
            ),
            layer=self.name, operation="resolve",
            evidence=tuple(context.evidence.values())[:8],
        ))

        for entry in resolution.contradictions():
            step(ReasoningStep(
                claim=(
                    f"{entry.identity} is pinned to "
                    f"{' and '.join(entry.versions)} by different sources"
                ),
                layer=self.name, operation="compare",
                evidence=entry.evidence[:4], confidence=1.0,
            ))

        if resolution.unresolved:
            observation, reason = resolution.unresolved[0]
            step(ReasoningStep(
                claim=(
                    f"{len(resolution.unresolved)} observation(s) could not be "
                    f"placed on the graph; the first was {observation.subject}: "
                    f"{reason}"
                ),
                layer=self.name, operation="resolve", confidence=0.0,
            ))
