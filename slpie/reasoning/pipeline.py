"""The pipeline — layers in order, over one shared context, gaps travelling out.

Three rules, and each one is a decision that could have gone the other way:

**Order is the layer number, not registration order.** A pipeline assembled in
the wrong order would produce enrichments citing enrichments that do not exist
yet, and the chain would have holes that only show up in an explanation
somebody reads months later. So the layers sort themselves.

**A raising layer abstains; the pipeline continues.** `BaseLayer.run` catches
and reports rather than propagating, and the pipeline records the abstention as
a `Gap`. Aborting the run would mean one bad layer costs the other nine, and a
`slpie ask` that returns nothing is worse than one that returns eight layers'
worth of answer plus an honest note about the ninth.

**Every result carries its gaps.** `PipelineResult.gaps` is derived
mechanically — abstentions, unresolved observations, values that could not be
normalised, observations that cited nothing. Nothing is assembled by hand, so
nothing gets forgotten when a new failure mode appears; it appears as a gap
because the layer reported it, not because somebody remembered to.

The explanation surface is `explain(enrichment_id)`, which walks `derived_from`
backwards and renders a `ReasoningPath` whose evidence has files and line
numbers in it. `ReasoningPath.grounded` is false when that walk fails to reach
raw evidence — which is the check that keeps the whole architecture honest, and
is asserted directly by the test suite rather than assumed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from ..domain.evidence import Evidence
from ..domain.finding import Gap, GapKind
from ..domain.reasoning import (
    Enrichment,
    LayerNumber,
    ReasoningPath,
    ReasoningStep,
    trace_to_evidence,
)
from .l1_discovery import DiscoveryLayer
from .l2_normalization import NormalizationLayer
from .l3_graph import GraphConstructionLayer
from .l4_linking import SemanticLinkingLayer
from .layer import Layer, LayerContext, LayerError, LayerResult


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """What the whole run produced, and everything it could not see."""

    context: LayerContext
    results: tuple[LayerResult, ...] = ()
    duration: float = 0.0

    @property
    def enrichments(self) -> tuple[Enrichment, ...]:
        return tuple(self.context.enrichments.values())

    @property
    def steps(self) -> tuple[ReasoningStep, ...]:
        return tuple(step for result in self.results for step in result.steps)

    @property
    def abstained(self) -> tuple[LayerResult, ...]:
        return tuple(result for result in self.results if result.abstained)

    @property
    def clean(self) -> bool:
        return all(result.clean for result in self.results)

    @property
    def gaps(self) -> tuple[Gap, ...]:
        """What limits every answer built on this run.

        Derived from what the layers reported, never assembled by hand — a gap
        that has to be remembered is a gap that eventually is not.
        """
        found: list[Gap] = []

        for result in self.abstained:
            found.append(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject=f"L{result.number} {result.layer}",
                detail=(
                    f"the {result.layer} layer abstained: "
                    f"{result.errors[0] if result.errors else 'unknown failure'}"
                ),
                remediation=(
                    "everything this layer would have concluded is missing from "
                    "the answer; the other layers ran"
                ),
                confidence_impact=0.2,
            ))

        ungrounded = int(self.context.facts.get("ungrounded_observations", 0))
        if ungrounded:
            found.append(Gap(
                kind=GapKind.PARSE_FAILURE,
                subject=self.context.element or "discovery",
                detail=(
                    f"{ungrounded} observation(s) cited no evidence and were "
                    f"discarded rather than promoted ungrounded"
                ),
                remediation="fix the discoverer so it reports a file and a line",
                confidence_impact=0.1,
            ))

        unresolved = int(self.context.facts.get("unresolved", 0))
        if unresolved:
            found.append(Gap(
                kind=GapKind.UNRESOLVED_DEPENDENCY,
                subject=self.context.element or "graph",
                detail=f"{unresolved} observation(s) had no placeable identity",
                remediation="check that the discoverer emits a purl or an SLPIE urn",
                confidence_impact=0.15,
            ))

        for refusal in self.context.facts.get("normalisation_refusals", ()):
            found.append(Gap(
                kind=GapKind.PARSE_FAILURE,
                subject=str(refusal).split(":", 1)[0],
                detail=str(refusal),
                remediation="record the exact value; the platform refuses to guess it",
                confidence_impact=0.1,
            ))

        for abstention in self.context.facts.get("linker_abstentions", ()):
            found.append(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject="semantic_linking",
                detail=str(abstention),
                remediation="the remaining linkers ran; this join was not made",
                confidence_impact=0.1,
            ))

        return tuple(found)

    def of_layer(self, layer: str) -> LayerResult | None:
        for result in self.results:
            if result.layer == layer:
                return result
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.context.element,
            "layers": [result.to_dict() for result in self.results],
            "enrichments": len(self.context.enrichments),
            "facts": dict(self.context.facts),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "clean": self.clean,
            "duration": round(self.duration, 4),
        }

    def __str__(self) -> str:
        return (
            f"{len(self.results)} layers, {len(self.context.enrichments)} "
            f"enrichments, {len(self.gaps)} gaps"
            + (f", {len(self.abstained)} abstained" if self.abstained else "")
        )


class Pipeline:
    """The ten layers, of which phase 9 delivers four.

    Layers are added rather than hard-coded so that L5–L10 attach without this
    file changing, and so a third-party layer registered through the plugin
    registry runs by the identical path a built-in does — the seam proven by its
    own use rather than asserted.
    """

    def __init__(self, layers: Iterable[Layer] | None = None) -> None:
        self._layers: list[Layer] = list(layers) if layers is not None else [
            DiscoveryLayer(),
            NormalizationLayer(),
            GraphConstructionLayer(),
            SemanticLinkingLayer(),
        ]

    def add(self, layer: Layer) -> Layer:
        """Register a layer. Ordering is by number, so insertion order is free."""
        if not hasattr(layer, "number") or not hasattr(layer, "name"):
            raise LayerError(
                f"{layer!r} is not a layer: a layer states its name and its "
                f"number, and the number is what orders the pipeline"
            )
        self._layers.append(layer)
        return layer

    @property
    def layers(self) -> tuple[Layer, ...]:
        """In execution order — by layer number, never by registration order."""
        return tuple(sorted(self._layers, key=lambda layer: int(layer.number)))

    def run(
        self,
        observations: Sequence[Any],
        *,
        element: str = "",
        context: LayerContext | None = None,
    ) -> PipelineResult:
        started = time.monotonic()
        active = context or LayerContext()
        active.element = element or active.element
        active.observations = tuple(observations)
        active.started_at = time.time()

        results: list[LayerResult] = []
        for layer in self.layers:
            results.append(layer.run(active))

        return PipelineResult(
            context=active, results=tuple(results),
            duration=time.monotonic() - started,
        )

    # -- explanation -----------------------------------------------------

    def explain(
        self,
        result: PipelineResult,
        enrichment_id: str,
        *,
        question: str = "",
    ) -> ReasoningPath:
        """Why the platform believes one thing, ending in files and line numbers.

        The path is built from the enrichment chain rather than from the layers'
        narrative steps, because the chain is the thing the append-only rule
        guarantees. A step is a sentence somebody wrote; the chain is structure.
        """
        context = result.context
        enrichment = context.enrichments.get(enrichment_id)
        if enrichment is None:
            return ReasoningPath(question=question or enrichment_id)

        ordered = self._chain(context, enrichment_id)
        steps: list[ReasoningStep] = []
        for item in ordered:
            evidence = trace_to_evidence(
                item, enrichments=context.enrichments, evidence=context.evidence,
            )
            steps.append(ReasoningStep(
                claim=item.rationale or str(item),
                layer=item.layer,
                evidence=evidence,
                inputs=item.derived_from,
                confidence=item.confidence or 1.0,
                operation="derive",
            ))

        return ReasoningPath(
            steps=tuple(steps),
            question=question or f"why is {enrichment.subject} {enrichment.attribute}?",
        )

    def _chain(self, context: LayerContext, enrichment_id: str) -> tuple[Enrichment, ...]:
        """The enrichments behind one conclusion, earliest layer first."""
        collected: dict[str, Enrichment] = {}
        frontier = [enrichment_id]
        seen: set[str] = set()

        while frontier:
            reference = frontier.pop()
            if reference in seen:
                continue
            seen.add(reference)
            enrichment = context.enrichments.get(reference)
            if enrichment is None:
                continue
            collected[reference] = enrichment
            frontier.extend(enrichment.derived_from)

        return tuple(sorted(
            collected.values(),
            key=lambda item: (_order(item.layer), item.subject, item.attribute),
        ))

    def evidence_for(self, result: PipelineResult, enrichment_id: str) -> tuple[Evidence, ...]:
        """The raw evidence one conclusion rests on. Empty means ungrounded."""
        enrichment = result.context.enrichments.get(enrichment_id)
        if enrichment is None:
            return ()
        return trace_to_evidence(
            enrichment,
            enrichments=result.context.enrichments,
            evidence=result.context.evidence,
        )

    def __len__(self) -> int:
        return len(self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(self.layers)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Pipeline {[layer.name for layer in self.layers]}>"


def _order(layer: str) -> int:
    """A layer name back to its number, so a chain renders in pipeline order."""
    try:
        return int(LayerNumber[layer.upper()])
    except KeyError:
        return 99
