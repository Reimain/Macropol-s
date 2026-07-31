"""L6 — do the declared ranges actually hold together?

The solver already exists and already explains its failures by naming the
conflicting pair and both windows. This layer's only job is to run it over what
L3 resolved and put the result where later layers and the explainer can reach it.

The index is what was **observed**, never a network lookup. A coordinate this tree
has never seen yields no candidates, which becomes a named conflict rather than an
assumption that some satisfying version must exist somewhere — an assumption that
would turn every unresolvable dependency into a silent pass.
"""

from __future__ import annotations

from typing import Any, Callable

from ..domain.finding import Gap, GapKind
from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from .constraints.model import StaticIndex, requirements_from
from .constraints.solver import BacktrackingSolver
from .layer import BaseLayer, LayerContext


class ConstraintSolvingLayer(BaseLayer):
    """Runs the solver over the resolution, and records what it decided."""

    name = "constraint_solving"
    number = LayerNumber.CONSTRAINT_SOLVING

    def __init__(self, *, max_steps: int = 10_000) -> None:
        self.max_steps = max_steps

    def execute(
        self,
        context: LayerContext,
        emit: Callable[[Enrichment], None],
        step: Callable[[ReasoningStep], None],
    ) -> None:
        resolution = context.resolution
        if resolution is None:
            raise ValueError(
                "constraint solving needs a resolved graph; solving over nothing "
                "returns satisfiable, which is the most misleading possible answer"
            )

        requirements = requirements_from(resolution)
        if not requirements:
            context.facts["constraints"] = {"requirements": 0}
            # A gap, not a zero-confidence step. "There is nothing to solve" is a
            # certain statement about coverage, and a path's confidence is the
            # minimum across its steps — so expressing that certainty as doubt
            # drove every answer over a range-free tree to confidence 0.0.
            context.limited_by(Gap(
                kind=GapKind.NOT_IMPLEMENTED,
                subject=context.element or "constraint_solving",
                detail=(
                    "no version ranges were declared, so there is nothing to "
                    "solve and no conflict can be reported either way"
                ),
                remediation=(
                    "declare ranges in a manifest; pins alone say what was "
                    "installed, not what was asked for"
                ),
                confidence_impact=0.1,
            ))
            step(ReasoningStep(
                claim="no version ranges were declared, so there is nothing to solve",
                layer=self.name, operation="solve",
            ))
            return

        index = StaticIndex()
        for entry in resolution.resolved:
            for version in (*entry.versions, entry.pinned):
                if version:
                    index.add(entry.coordinate, version)

        solver = BacktrackingSolver(max_steps=self.max_steps)
        solution = solver.solve(requirements, index)
        context.facts["constraints"] = {
            "requirements": len(requirements),
            "satisfiable": solution.satisfiable,
            "conflicts": len(solution.conflicts),
            "exhausted": solution.exhausted,
        }
        context.facts["solution"] = solution

        for conflict in solution.conflicts:
            emit(self.enrich(
                conflict.coordinate, "constraint_conflict", conflict.explain(),
                derived_from=tuple(dict.fromkeys(
                    reference
                    for side in (conflict.left, conflict.right)
                    if side is not None
                    for reference in side.derived_from
                )),
                rationale=conflict.explain(),
            ))

        for assignment in solution.assignments:
            if not assignment.derived_from:
                continue
            emit(self.enrich(
                assignment.coordinate, "resolves_to", assignment.version,
                derived_from=assignment.derived_from,
                rationale=(
                    f"{assignment.name} resolves to {assignment.version}, "
                    f"satisfying {len(assignment.satisfies)} declared range(s)"
                ),
            ))

        for item in solver.steps(solution):
            step(item)
