"""L1 — what was read, recorded as facts that later layers may cite.

The discovery layer does no analysis. Its whole job is to turn a batch of raw
`Observation`s into the first link of every chain: each observation becomes an
`Enrichment` whose `derived_from` is the id of the evidence that produced it,
and the evidence itself is indexed on the context so a walk can terminate in a
file and a line rather than in a dangling reference.

That indexing is the point of the layer existing at all. Without it, L4 could
cite an evidence id and the explainer would have nothing to resolve it to — an
explanation that reaches a hash and stops is not better than no explanation, it
is worse, because it looks like provenance.

An observation with no evidence is **not** promoted. It is counted as a gap and
named, because the alternative — letting it through with an empty
`derived_from` — is exactly the ungrounded assertion `LayerContext.add` refuses.
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from .layer import BaseLayer, LayerContext


class DiscoveryLayer(BaseLayer):
    """Raw observations in; grounded first-order facts out."""

    name = "discovery"
    number = LayerNumber.DISCOVERY

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        ungrounded = 0
        sources: set[str] = set()
        kinds: dict[str, int] = {}

        for observation in context.observations:
            evidence = getattr(observation, "evidence", None)
            if evidence is None:
                ungrounded += 1
                continue

            reference = context.remember(evidence)
            sources.add(evidence.location.uri)
            kinds[observation.kind] = kinds.get(observation.kind, 0) + 1

            emit(self.enrich(
                observation.subject,
                f"observed:{observation.kind}",
                observation.object or True,
                derived_from=(reference,),
                confidence=evidence.base_confidence,
                rationale=(
                    f"{evidence.extractor} read {evidence.location.reference} "
                    f"and recorded {observation.kind}"
                ),
            ))

        context.facts["observations"] = len(context.observations)
        context.facts["sources"] = sorted(sources)
        context.facts["kinds"] = dict(sorted(kinds.items()))
        context.facts["ungrounded_observations"] = ungrounded

        step(ReasoningStep(
            claim=(
                f"read {len(context.observations)} observations from "
                f"{len(sources)} file(s)"
            ),
            layer=self.name, operation="discover",
            evidence=tuple(context.evidence.values())[:8],
            confidence=1.0 if context.observations else 0.0,
        ))

        if ungrounded:
            # Stated as a step rather than swallowed: a discoverer emitting
            # observations without evidence is a defect in that discoverer, and
            # the count is how anybody finds out.
            step(ReasoningStep(
                claim=(
                    f"{ungrounded} observation(s) cited no evidence and were "
                    f"not promoted; a discoverer that cannot say where it read "
                    f"something has not observed it"
                ),
                layer=self.name, operation="discover", confidence=0.0,
            ))
