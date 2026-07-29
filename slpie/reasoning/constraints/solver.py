"""Backtracking resolution, shaped so PubGrub can replace it.

The plan defers PubGrub and ships this. That is a defensible trade only if two
properties hold, and both are load-bearing here:

**The failure is explained.** PubGrub's real contribution is not speed, it is
that it derives a human-readable cause instead of "resolution impossible". So
this solver records why each coordinate ran out of candidates, and returns the
two requirements at odds together with the window each demands. The
implementation is dumber than PubGrub's conflict-driven clause learning; the
*output* answers the same question, which is the part users depend on.

**Nothing outside sees the algorithm.** The `Solver` protocol takes requirements
and an index and returns a `Solution`. Swapping in PubGrub changes one class.

Two smaller decisions worth stating. Candidates are tried **newest first**,
because a resolver that picks the oldest satisfying version technically succeeds
and produces a lockfile nobody wants. And the coordinate with the **fewest
viable candidates** is decided first — the standard most-constrained-variable
heuristic, which finds contradictions near the top of the tree instead of after
exploring a subtree that was doomed from its root.

The step budget exists because backtracking is exponential in the worst case.
When it runs out the result is `exhausted`, and — this is the part that matters
— `satisfiable` is false while `conflicts` may be empty, because giving up is
not a proof of unsatisfiability and must not be reported as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from ...domain.reasoning import ReasoningStep
from .compat import overlaps
from .model import (
    Assignment,
    Conflict,
    PackageIndex,
    Requirement,
    Solution,
    StaticIndex,
)

#: How many assignment attempts before the solver stops and says it gave up.
DEFAULT_MAX_STEPS = 10_000


@runtime_checkable
class Solver(Protocol):
    """Requirements plus an index, in; a `Solution` that explains itself, out."""

    name: str

    def solve(
        self,
        requirements: Iterable[Requirement],
        index: PackageIndex | None = None,
    ) -> Solution:
        ...


@dataclass(slots=True)
class _Search:
    """Mutable bookkeeping for one solve. Not part of the public surface."""

    attempts: int = 0
    exhausted: bool = False
    failures: list[Conflict] = field(default_factory=list)

    def record(self, conflict: Conflict) -> None:
        key = (conflict.coordinate, conflict.pair)
        for existing in self.failures:
            if (existing.coordinate, existing.pair) == key:
                return
        self.failures.append(conflict)


class BacktrackingSolver:
    """Depth-first assignment with most-constrained-first ordering.

    Correct and comprehensible; not fast on pathological inputs, which is why
    the step budget is real and its exhaustion is reported honestly rather than
    dressed up as an answer.
    """

    name = "backtracking"

    def __init__(self, *, max_steps: int = DEFAULT_MAX_STEPS,
                 prefer_newest: bool = True) -> None:
        self.max_steps = max_steps
        self.prefer_newest = prefer_newest

    # -- public surface --------------------------------------------------

    def solve(
        self,
        requirements: Iterable[Requirement],
        index: PackageIndex | None = None,
    ) -> Solution:
        wanted = tuple(requirements)
        index = index if index is not None else StaticIndex()

        # A range we cannot parse is set aside rather than treated as `*`. It
        # travels back on the solution so the caller can report it as a gap;
        # silently widening it would produce a lockfile built on a constraint
        # that was never applied.
        unreadable = tuple(r for r in wanted if not r.readable)
        readable = tuple(r for r in wanted if r.readable)

        demands: dict[str, tuple[Requirement, ...]] = {}
        for requirement in readable:
            if requirement.optional:
                continue
            demands[requirement.coordinate] = (
                demands.get(requirement.coordinate, ()) + (requirement,)
            )

        # The cheap decisive pass: two root requirements whose windows provably
        # cannot both hold. Catching it here yields the clearest possible
        # explanation, before any search muddies which pair was at fault.
        for coordinate, group in sorted(demands.items()):
            conflict = self._disjoint(coordinate, group, index)
            if conflict is not None:
                return Solution(
                    satisfiable=False, conflicts=(conflict,),
                    unreadable=unreadable, attempts=0,
                )

        state = _Search()
        assigned: dict[str, str] = {}
        satisfied: dict[str, tuple[Requirement, ...]] = {}
        found = self._recurse(demands, assigned, satisfied, index, state)

        if found is None:
            return Solution(
                satisfiable=False,
                conflicts=tuple(self._ranked(state.failures)),
                unreadable=unreadable, attempts=state.attempts,
                exhausted=state.exhausted,
            )

        depths = self._depths(satisfied)
        assignments = tuple(sorted(
            (
                Assignment(
                    coordinate=coordinate, version=version,
                    satisfies=satisfied.get(coordinate, ()),
                    depth=depths.get(coordinate, 0),
                )
                for coordinate, version in found.items()
            ),
            key=lambda item: (item.depth, item.coordinate),
        ))
        return Solution(
            satisfiable=True, assignments=assignments,
            unreadable=unreadable, attempts=state.attempts,
        )

    def steps(self, solution: Solution) -> tuple[ReasoningStep, ...]:
        """The solve, rendered as explanation lines for the reasoning path."""
        if not solution.satisfiable:
            return tuple(
                ReasoningStep(
                    claim=conflict.explain(), layer="constraint_solving",
                    operation="solve", confidence=1.0,
                    inputs=tuple(conflict.left.derived_from),
                )
                for conflict in solution.conflicts
            ) or (
                ReasoningStep(
                    claim=solution.explain(), layer="constraint_solving",
                    operation="solve", confidence=0.0,
                ),
            )
        return tuple(
            ReasoningStep(
                claim=(
                    f"{assignment.name} resolves to {assignment.version}"
                    + (
                        f", satisfying {len(assignment.satisfies)} requirement(s)"
                        if assignment.satisfies else ""
                    )
                ),
                layer="constraint_solving", operation="solve",
                inputs=assignment.derived_from,
            )
            for assignment in solution.assignments
        )

    # -- search ----------------------------------------------------------

    def _recurse(
        self,
        demands: Mapping[str, tuple[Requirement, ...]],
        assigned: dict[str, str],
        satisfied: dict[str, tuple[Requirement, ...]],
        index: PackageIndex,
        state: _Search,
    ) -> dict[str, str] | None:
        state.attempts += 1
        if state.attempts > self.max_steps:
            state.exhausted = True
            return None

        unassigned = [name for name in demands if name not in assigned]
        if not unassigned:
            for coordinate in assigned:
                satisfied[coordinate] = demands.get(coordinate, ())
            return dict(assigned)

        # Most-constrained first, and a coordinate with *no* viable candidate
        # fails the whole branch immediately rather than after the subtree
        # beneath it has been explored.
        scored: list[tuple[int, str, tuple[str, ...]]] = []
        for coordinate in sorted(unassigned):
            group = demands[coordinate]
            available = tuple(index.versions_of(coordinate))
            viable = tuple(
                version for version in available
                if all(requirement.allows(version) for requirement in group)
            )
            if not viable:
                state.record(self._conflict(coordinate, group, available))
                return None
            scored.append((len(viable), coordinate, viable))

        scored.sort(key=lambda item: (item[0], item[1]))
        _, coordinate, viable = scored[0]
        candidates = viable if self.prefer_newest else tuple(reversed(viable))

        for version in candidates:
            nested = dict(demands)
            for requirement in index.dependencies_of(coordinate, version):
                if requirement.optional:
                    continue
                nested[requirement.coordinate] = (
                    nested.get(requirement.coordinate, ()) + (requirement,)
                )

            assigned[coordinate] = version
            found = self._recurse(nested, assigned, satisfied, index, state)
            if found is not None:
                return found
            del assigned[coordinate]
            if state.exhausted:
                return None

        return None

    # -- explanation -----------------------------------------------------

    def _disjoint(
        self,
        coordinate: str,
        group: Sequence[Requirement],
        index: PackageIndex,
    ) -> Conflict | None:
        """A provably impossible pair among these demands, if there is one."""
        if len(group) < 2:
            return None
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                verdict = overlaps(left.range, right.range, ecosystem=left.ecosystem)
                if verdict.certain and not verdict.overlaps:
                    return Conflict(
                        coordinate=coordinate, left=left, right=right,
                        available=tuple(index.versions_of(coordinate)),
                        reason=verdict.reason,
                    )
        return None

    def _conflict(
        self,
        coordinate: str,
        group: Sequence[Requirement],
        available: Sequence[str],
    ) -> Conflict:
        """Why this coordinate had nothing left, said as specifically as possible."""
        if not available:
            return Conflict(
                coordinate=coordinate, left=group[0],
                right=group[1] if len(group) > 1 else None,
                available=(),
                reason=(
                    "no versions of it were observed anywhere in the ecosystem; "
                    "either it is not declared, or the discovery that would have "
                    "found it was refused"
                ),
            )

        # Prefer a pair that jointly excludes everything: that is the actual
        # contradiction. Reporting one arbitrary requirement instead would name
        # a requirement that is individually satisfiable and send the reader
        # looking in the wrong place.
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if any(
                    left.allows(version) and right.allows(version)
                    for version in available
                ):
                    continue
                verdict = overlaps(left.range, right.range, ecosystem=left.ecosystem)
                return Conflict(
                    coordinate=coordinate, left=left, right=right,
                    available=tuple(available),
                    reason=(
                        verdict.reason if verdict.certain
                        else "no observed version satisfies both windows"
                    ),
                )

        unmet = next(
            (r for r in group if not any(r.allows(v) for v in available)),
            group[0],
        )
        return Conflict(
            coordinate=coordinate, left=unmet,
            right=None, available=tuple(available),
            reason="no observed version falls inside the required window",
        )

    def _ranked(self, failures: Sequence[Conflict]) -> tuple[Conflict, ...]:
        """Most informative first: a named pair beats a lone unmet requirement."""
        return tuple(sorted(
            failures,
            key=lambda conflict: (conflict.right is None, conflict.coordinate),
        ))

    def _depths(self, satisfied: Mapping[str, tuple[Requirement, ...]]) -> dict[str, int]:
        """How far each package sits from the root. Cycle-safe by construction."""
        depths: dict[str, int] = {}

        def walk(coordinate: str, seen: frozenset[str]) -> int:
            if coordinate in depths:
                return depths[coordinate]
            if coordinate in seen:
                return 0
            group = satisfied.get(coordinate, ())
            if not group:
                return 0
            found = min(
                (
                    0 if requirement.requested_by in ("", "root")
                    else 1 + walk(requirement.requested_by, seen | {coordinate})
                )
                for requirement in group
            )
            depths[coordinate] = found
            return found

        for coordinate in satisfied:
            walk(coordinate, frozenset())
        return depths

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<BacktrackingSolver max_steps={self.max_steps}>"


def solve(
    requirements: Iterable[Requirement],
    index: PackageIndex | None = None,
    **options: Any,
) -> Solution:
    """The default solve. One call, for the common case."""
    return BacktrackingSolver(**options).solve(requirements, index)
