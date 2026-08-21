"""L4 — the joins no single file could have made.

By L3 the graph holds what each discoverer saw. L4 holds what nobody saw,
because it spans two files: that `import yaml` is `PyYAML`, that the pin in the
lockfile is the answer to the range in the manifest, that the image Kubernetes
schedules is the one this Dockerfile builds.

Every join arrives already carrying an `Enrichment` from its linker, so this
layer's job is composition rather than reasoning: run the set, keep the
enrichments, and turn contradictions into steps that say what disagrees with
what. A linker that raises abstains inside `LinkerSet`, and the abstention is
recorded here rather than discarded — one piece of cross-file knowledge being
wrong must not cost the other four, and must not be invisible either.

This is the layer the provenance test walks back from. An L4 conclusion cites
its linker's evidence ids directly, those resolve through the context's evidence
index, and the walk terminates at a URI with a line number. If that ever stops
being true, the platform's central claim stops being true with it.
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import ReasoningError
from ..linking.linkers import Joined, LinkerSet
from .layer import BaseLayer, LayerContext


class SemanticLinkingLayer(BaseLayer):
    """Cross-file knowledge, applied to a resolved graph."""

    name = "semantic_linking"
    number = LayerNumber.SEMANTIC_LINKING

    def __init__(self, linkers: LinkerSet | None = None) -> None:
        self.linkers = linkers or LinkerSet()

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        if context.resolution is None:
            raise ReasoningError(
                "semantic linking needs a resolved graph; L3 either did not run "
                "or abstained, and linking guesses would fill the hole with "
                "joins nothing supports"
            )

        joined = self.linkers.run(context.resolution)
        context.joined = tuple(joined)

        by_linker: dict[str, int] = {}
        for entry in joined:
            by_linker[entry.linker] = by_linker.get(entry.linker, 0) + 1
            if entry.enrichment.grounded:
                emit(entry.enrichment)

        context.facts["links"] = len(joined)
        context.facts["links_by_linker"] = dict(sorted(by_linker.items()))
        context.facts["linker_abstentions"] = list(self.linkers.errors)

        step(ReasoningStep(
            claim=(
                f"{len(joined)} cross-file link(s) from "
                f"{len(by_linker)} of {len(self.linkers)} linkers"
            ),
            layer=self.name, operation="match",
            evidence=tuple(context.evidence.values())[:8],
        ))

        for entry in self.linkers.contradictions(joined):
            step(ReasoningStep(
                claim=entry.contradiction,
                layer=self.name, operation="compare", confidence=1.0,
                inputs=(entry.enrichment.id,),
            ))

        for abstention in self.linkers.errors:
            step(ReasoningStep(
                claim=(
                    f"{abstention}; the remaining linkers ran, and this gap "
                    f"travels with every answer they contributed to"
                ),
                layer=self.name, operation="match", confidence=0.0,
            ))
