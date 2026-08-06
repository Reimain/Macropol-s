"""L8 — what could be better, with the cost of each option stated.

The temptation in an optimisation layer is to recommend. This one **enumerates**:
every upgrade from where you are, each labelled with what it changes and who it
breaks, ordered safest first. Which one to take is a judgement about the codebase
— how well tested it is, whether the major bump's breaking change is one it
actually uses — and the platform is not in a position to make that judgement for
somebody. It is in a position to say exactly what each option costs.

Three things this layer looks for, all computable offline from the resolution:

* **safe upgrades** — a newer observed version that every declared range still
  admits. The free ones.
* **duplicate versions** — the same package resolved twice in one tree. It bloats
  the install, and when the two differ across a security boundary it is a real
  finding rather than a tidiness one.
* **over-broad ranges** — a range so wide that the resolver's choice is
  effectively arbitrary. `*` is not a constraint, it is a hope.

Nothing here proposes a *breaking* change as though it were safe. `Change.MAJOR`
carries `may_break`, and the enrichment says so — reporting every major bump as a
certain break makes the report worthless and people stop reading it, but reporting
it as safe is worse.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import ReasoningError
from .constraints.compat import Change, UpgradeStep, classify, safe_upgrades
from .constraints.model import Requirement
from .layer import BaseLayer, LayerContext

#: A range this wide is not a constraint. `*` and `>=0` admit anything the
#: registry ever publishes, so what gets installed is whatever happened to be
#: newest on the day somebody ran the resolver.
UNCONSTRAINED = frozenset({"*", "", "any", ">=0", ">=0.0.0", "latest"})


class OptimizationLayer(BaseLayer):
    """Enumerates the options and their costs. Recommends nothing."""

    name = "optimization"
    number = LayerNumber.OPTIMIZATION

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        resolution = context.resolution
        if resolution is None:
            raise ReasoningError(
                "optimisation needs a resolved graph; suggesting upgrades without "
                "knowing what is installed would be guessing at both ends"
            )

        entries = tuple(getattr(resolution, "resolved", ()))
        if not entries:
            context.facts["optimization"] = {"candidates": 0}
            return

        upgrades = 0
        duplicates = 0
        unconstrained = 0

        by_coordinate: dict[str, list[Any]] = {}
        for entry in entries:
            by_coordinate.setdefault(entry.coordinate, []).append(entry)

        for entry in entries:
            citations = tuple(item.id for item in entry.evidence)

            # -- over-broad ranges ---------------------------------------
            for declared in entry.ranges:
                if declared.strip().lower() in UNCONSTRAINED:
                    unconstrained += 1
                    emit(self.enrich(
                        entry.node_id, "unconstrained_range", declared,
                        derived_from=citations,
                        rationale=(
                            f"{entry.identity} is declared as {declared!r}, which "
                            f"admits anything ever published — what gets installed "
                            f"is whatever was newest the day somebody resolved it"
                        ),
                    ))

            # -- safe upgrades -------------------------------------------
            current = entry.pinned
            candidates = [item for item in entry.versions if item and item != current]
            if not current or not candidates:
                continue

            demands = tuple(
                Requirement(
                    coordinate=entry.coordinate, range=declared,
                    requested_by=entry.identity,
                    derived_from=citations,
                )
                for declared in entry.ranges
            )
            steps = safe_upgrades(
                entry.coordinate, current, candidates, demands,
            )
            for option in steps:
                upgrades += 1
                emit(self.enrich(
                    entry.node_id,
                    "safe_upgrade" if option.safe else "upgrade_option",
                    option.target,
                    derived_from=citations,
                    confidence=1.0 - option.risk,
                    rationale=(
                        f"{entry.identity} could move {option.current} → "
                        f"{option.target} ({option.change.value})"
                        + (
                            ", and every declared range still admits it"
                            if option.safe else
                            f", but it no longer satisfies "
                            f"{', '.join(option.breaks) or 'a declared range'}"
                            + (
                                "; a major bump *may* break callers rather than "
                                "certainly will"
                                if option.change.may_break else ""
                            )
                        )
                    ),
                ))

        # -- duplicates --------------------------------------------------
        for coordinate, group in sorted(by_coordinate.items()):
            versions = sorted({
                version for entry in group for version in entry.versions if version
            })
            if len(versions) < 2:
                continue
            duplicates += 1
            emit(self.enrich(
                group[0].node_id, "duplicate_versions", tuple(versions),
                derived_from=tuple(
                    item.id for entry in group for item in entry.evidence
                )[:6],
                rationale=(
                    f"{coordinate} resolves to {' and '.join(versions)} in one "
                    f"tree; both are installed, and a caller gets whichever the "
                    f"resolver reached first"
                ),
            ))

        context.facts["optimization"] = {
            "candidates": len(entries),
            "upgrades": upgrades,
            "duplicates": duplicates,
            "unconstrained": unconstrained,
        }

        step(ReasoningStep(
            claim=(
                f"{upgrades} upgrade option(s) across {len(entries)} package(s), "
                f"{duplicates} duplicated, {unconstrained} unconstrained range(s)"
            ),
            layer=self.name, operation="compare",
            evidence=tuple(context.evidence.values())[:6],
        ))

        if upgrades:
            step(ReasoningStep(
                claim=(
                    "each option is listed with what it changes and who it "
                    "breaks; which to take is a judgement about this codebase, "
                    "not one the platform can make"
                ),
                layer=self.name, operation="compare",
                evidence=tuple(context.evidence.values())[:2],
            ))
