"""L5 — where what was declared meets what is actually there.

The delta is first-class intelligence, not a diff nobody reads. Four shapes, and
each says something different about the codebase:

* **phantom** — imported, never declared. It works on this machine because
  something else pulls it in transitively, and breaks on a clean install the day
  that provider drops it. The most common real defect this layer finds.
* **unused** — declared, never imported. Dead weight in the install and, more
  importantly, in the vulnerability surface: every declared dependency is
  something a scanner reports CVEs against and somebody has to triage.
* **contradicted** — the declaration and the observation disagree about a version.
  Somebody's build is not what they think it is.
* **undeclared boundary crossing** — a module in a declared security boundary
  reaching outside it.

The layer is deliberately **offline**: it reads declarations out of the
observations themselves rather than requiring a manifest, so it runs on a bare
checkout. A validation layer that only worked once somebody had written an
environment manifest would never run on the tree that most needs it — the one
nobody has described yet.

Where a declaration cannot be found at all, the layer says so rather than
concluding everything is undeclared. Reporting a hundred phantoms because the
manifest was not read is worse than reporting nothing.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import ReasoningError
from .layer import BaseLayer, LayerContext

#: Observation kinds that state a dependency was *declared* by a manifest.
DECLARING = frozenset({"declares", "depends_on", "requires"})

#: Observation kinds that state a dependency is *used* by code.
USING = frozenset({"imports", "uses", "calls", "references"})


class ArchitectureValidationLayer(BaseLayer):
    """Declared versus observed, with each delta named for what it means."""

    name = "architecture_validation"
    number = LayerNumber.ARCHITECTURE_VALIDATION

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        resolution = context.resolution
        if resolution is None:
            raise ReasoningError(
                "architecture validation needs a resolved graph; without one it "
                "would compare declarations against nothing and report every "
                "dependency as missing"
            )

        declared, used, evidence_of = self._sides(context)

        # A tree with no declarations at all has not been read, it has been
        # skipped. Reporting a hundred phantoms because no manifest was parsed
        # would be worse than reporting nothing.
        if not declared:
            context.limited_by(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject=context.element or "architecture_validation",
                detail=(
                    "nothing declared a dependency in this tree, so declared "
                    "versus observed cannot be compared"
                ),
                remediation=(
                    "no delta is reported rather than reporting everything as "
                    "undeclared; point the scan at a tree with a manifest in it"
                ),
                confidence_impact=0.2,
            ))
            step(ReasoningStep(
                claim=(
                    "nothing declared a dependency in this tree, so declared "
                    "versus observed cannot be compared; no delta is reported "
                    "rather than reporting everything as undeclared"
                ),
                layer=self.name, operation="compare",
            ))
            context.facts["validation"] = {"comparable": False}
            return

        phantom = sorted(used - declared)
        contradicted = [
            entry for entry in resolution.resolved if entry.contradicted
        ]

        # "Unused" is only decidable per ecosystem, and only where code that
        # *could* import it was actually read. A tree of manifests with no source
        # files scanned would otherwise report every dependency as unused — a
        # confident conclusion drawn from having looked at nothing, which is the
        # same over-reach the audit judge exists to refuse.
        scanned = {_ecosystem(item) for item in used}
        undecidable = {
            item for item in (declared - used) if _ecosystem(item) not in scanned
        }
        unused = sorted((declared - used) - undecidable)

        for coordinate in phantom:
            emit(self.enrich(
                coordinate, "phantom_dependency", True,
                derived_from=evidence_of.get(coordinate, ()),
                rationale=(
                    f"{coordinate} is used but never declared. It resolves here "
                    f"because something else pulls it in, and a clean install "
                    f"breaks the day that provider drops it"
                ),
            ))

        for coordinate in unused:
            emit(self.enrich(
                coordinate, "unused_declaration", True,
                derived_from=evidence_of.get(coordinate, ()),
                rationale=(
                    f"{coordinate} is declared but nothing imports it. It is "
                    f"installed, and every declared dependency is something a "
                    f"scanner reports CVEs against"
                ),
            ))

        for entry in contradicted:
            emit(self.enrich(
                entry.node_id, "declaration_contradicted", tuple(entry.versions),
                derived_from=tuple(item.id for item in entry.evidence),
                rationale=(
                    f"{entry.identity} is pinned to "
                    f"{' and '.join(entry.versions)} by different sources; the "
                    f"build is not what the declaration says"
                ),
            ))

        context.facts["validation"] = {
            "comparable": True,
            "declared": len(declared),
            "used": len(used),
            "phantom": len(phantom),
            "unused": len(unused),
            "undecidable": len(undecidable),
            "contradicted": len(contradicted),
        }

        if undecidable:
            ecosystems = ", ".join(sorted({_ecosystem(item) for item in undecidable}))
            claim = (
                f"{len(undecidable)} declared dependenc(ies) cannot be judged "
                f"unused: no code in {ecosystems} was read, so nothing here "
                f"could have imported them"
            )
            # A gap, not a zero-confidence step. The layer is *certain* it cannot
            # decide, and expressing that certainty as doubt would drag the whole
            # answer's confidence to nothing over one ecosystem it did not reach.
            context.limited_by(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject=context.element or "architecture_validation",
                detail=claim,
                remediation=(
                    f"scan the {ecosystems} source alongside its manifests, and "
                    f"the unused-declaration question becomes answerable"
                ),
                confidence_impact=0.1,
            ))
            step(ReasoningStep(claim=claim, layer=self.name, operation="compare"))

        step(ReasoningStep(
            claim=(
                f"compared {len(declared)} declared against {len(used)} used: "
                f"{len(phantom)} phantom, {len(unused)} unused, "
                f"{len(contradicted)} contradicted"
            ),
            layer=self.name, operation="compare",
            evidence=tuple(context.evidence.values())[:6],
        ))

        for coordinate in phantom[:5]:
            step(ReasoningStep(
                claim=f"{coordinate} is imported but never declared",
                layer=self.name, operation="compare",
                evidence=tuple(
                    context.evidence[item]
                    for item in evidence_of.get(coordinate, ())
                    if item in context.evidence
                )[:2],
            ))

    def _sides(
        self, context: LayerContext,
    ) -> tuple[set[str], set[str], dict[str, tuple[str, ...]]]:
        """What was declared, what is used, and the evidence for each.

        Compared on **coordinates**, not on identities: a manifest declaring
        `^4.17.0` and an import naming the package would never match on identity,
        and every dependency in the tree would read as both phantom and unused.
        """
        from ..normalize.purl import canonical_purl

        declared: set[str] = set()
        used: set[str] = set()
        evidence: dict[str, list[str]] = {}

        for observation in context.observations:
            if observation.evidence is None:
                continue
            target = observation.object or observation.subject
            coordinate = _coordinate(target)
            if not coordinate:
                continue

            evidence.setdefault(coordinate, []).append(observation.evidence.id)
            if observation.kind in DECLARING:
                declared.add(coordinate)
            elif observation.kind in USING:
                used.add(coordinate)

        return (
            declared, used,
            {name: tuple(dict.fromkeys(items)) for name, items in evidence.items()},
        )


def _ecosystem(coordinate: str) -> str:
    """The ecosystem a coordinate belongs to. `pkg:npm/lodash` → `npm`."""
    if not coordinate.startswith("pkg:"):
        return ""
    return coordinate[4:].split("/", 1)[0]


def _coordinate(identity: str) -> str:
    """The version-independent coordinate, or empty for anything unplaceable."""
    if not identity or not identity.startswith("pkg:"):
        return ""
    try:
        from ..normalize.purl import canonical_purl

        return canonical_purl(identity).coordinate
    except Exception:  # noqa: BLE001 - an unparseable purl is simply not compared
        return ""
