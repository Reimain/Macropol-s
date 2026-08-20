"""Degree of interest — what a selection makes worth drawing.

The graph screen has always drawn a projection: every live node, laid out in
lanes by kind and wrapped at eleven rows. That reads well at the demo's
forty-one nodes and has no story at four thousand, and the instinct it invites
is the wrong one — *make the renderer faster*. No useful question about an
estate returns twenty thousand nodes, so optimising the everything-at-once view
optimises the case nobody asks for, and the reward for succeeding is a smear
that renders quickly.

**What should be drawn is the answer to a question, and the selection is the
question.** That is not a new idea imported for the occasion: every other screen
in this product is a saved composition and says so. The graph has been the one
screen that ignored it.

The mechanism is Furnas's, from 1986, and it is exactly right::

    DOI(node) = importance(node) - distance(node, focus)

**Distance is hops along the graph, never pixels.** Two nodes adjacent on a
screen are not related; two nodes six hops apart are, and the interface has to
agree with the graph rather than with the projection. That single choice is what
separates semantic zoom from scaling.

**Importance is read, never invented.** Degree, the worst finding raised against
a node, whether it sits on a declared security boundary, whether the manifest
declared it and discovery never met it, whether the observation contradicts the
declaration — all of it is already computed elsewhere in this platform, and this
module only weighs it. Every contribution names itself in ``reasons``, so a node
can say why it earned its place; a score with no sentence behind it is the
telemetry mistake §32 spends a paragraph refusing.

Three properties follow, and each replaces something the earlier design worked
around:

**The first frame is cheap by construction.** The field is computed once per
selection, not once per frame, and the render set is whatever cleared the
threshold. Performance stops being a rendering problem.

**Context survives.** What was not asked about does not vanish — it is elided,
with its count and the best score inside it, so the reader knows the size of
what they are not seeing. Focus *plus* context, rather than focus instead of it.

**Zoom moves the threshold.** Descending reveals more of the neighbourhood
rather than magnifying the same marks, which is the whole difference between
this and a scale factor.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import log2
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..domain.lifecycle import Severity
from ..domain.node import Node

#: Degree stops adding importance here. The difference between one dependent and
#: ten is enormous; the difference between two hundred and two hundred and ten is
#: nothing anybody acts on, and a linear weight would let one hub swamp the field.
DEGREE_SATURATION = 64

#: How far from the focus the walk goes. Beyond this a "neighbourhood" is the
#: estate again, which is the thing being escaped.
HORIZON = 6

#: How many nodes render as themselves. The rest elide.
BUDGET = 200

#: How many nodes the walk will examine at all. The bound is what makes the
#: first frame cheap by construction rather than cheap when the graph is small.
REACH = 2000

#: What each signal is worth on its own, in 0..1. Severity is highest because a
#: critical finding is the one thing a reader must not have to hunt for; a
#: declaration nobody corroborated is worth less than a contradiction, because
#: "we have not looked" is a weaker statement than "we looked and it disagrees".
WEIGHTS: Mapping[str, float] = {
    "degree": 0.55,
    "severity": 0.90,
    "boundary": 0.50,
    "contradicted": 0.50,
    "declared_only": 0.30,
}

#: One hop costs this much interest. Calibrated against the weights above rather
#: than picked: a node carrying a critical finding four hops out still outranks
#: a well-linked node next door, and loses to it at five. Severity travels
#: further than popularity, and exactly how much further is a number rather than
#: a feeling — `test_a_severe_node_stays_visible_further_than_a_popular_one`
#: pins it, so tuning this constant without meaning to fails the suite.
HOP_COST = 0.10


def _saturating(count: int, ceiling: int = DEGREE_SATURATION) -> float:
    """Logarithmic, so a hub cannot buy the whole field with its edge count."""
    if count <= 0:
        return 0.0
    return min(1.0, log2(1 + count) / log2(1 + ceiling))


@dataclass(frozen=True, slots=True)
class Signals:
    """What the platform already knows about a node, before anything is selected.

    Every field is read from somewhere that computed it: ``degree`` from the edge
    table, ``severity`` from the findings raised against the node, ``boundary``
    from the manifest's declared ``security.boundaries``, and the last two
    straight off :class:`~slpie.domain.node.Node`.
    """

    degree: int = 0
    severity: Severity | None = None
    boundary: bool = False
    contradicted: bool = False
    declared_only: bool = False

    @classmethod
    def of(
        cls,
        node: Node,
        *,
        degree: int = 0,
        severity: Severity | None = None,
        boundary: bool = False,
    ) -> "Signals":
        return cls(
            degree=degree,
            severity=severity,
            boundary=boundary,
            contradicted=node.contradicted,
            declared_only=node.declared_only,
        )

    def weigh(self) -> tuple[float, tuple[str, ...]]:
        """Importance in 0..1, and the sentence behind every part of it.

        Combined by noisy-OR — ``1 - product(1 - contribution)`` — which is the
        same rule §10 already uses to combine evidence into confidence. Reusing
        it is not tidiness: independent signals should *corroborate*, and an
        average does the opposite. A node with a critical finding **and** forty
        links is more interesting than either alone, whereas averaging would
        make the second signal drag the first back down towards the middle.

        The scale therefore does not move when a signal is added, and a single
        strong signal reaches near the top on its own, which is what lets a
        lone critical finding outrank a well-connected hub.
        """
        parts: list[tuple[str, float]] = []

        share = _saturating(self.degree)
        if share:
            plural = "" if self.degree == 1 else "s"
            parts.append((f"{self.degree} link{plural}", WEIGHTS["degree"] * share))
        if self.severity is not None:
            rank = self.severity.rank / Severity.CRITICAL.rank
            parts.append((f"{self.severity.value} finding", WEIGHTS["severity"] * rank))
        if self.boundary:
            parts.append(("on a declared boundary", WEIGHTS["boundary"]))
        if self.contradicted:
            parts.append(("observation contradicts the declaration", WEIGHTS["contradicted"]))
        if self.declared_only:
            parts.append(("declared, never observed", WEIGHTS["declared_only"]))

        remainder = 1.0
        for _reason, contribution in parts:
            remainder *= 1.0 - contribution
        return 1.0 - remainder, tuple(reason for reason, _contribution in parts)


@dataclass(frozen=True, slots=True)
class Interest:
    """One node, and why the selection made it worth drawing."""

    node_id: str
    display: str
    kind: str
    distance: int
    importance: float
    score: float
    reasons: tuple[str, ...] = ()

    @property
    def focused(self) -> bool:
        return self.distance == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "display": self.display, "kind": self.kind,
            "distance": self.distance, "importance": round(self.importance, 4),
            "score": round(self.score, 4), "reasons": list(self.reasons),
            "focused": self.focused,
        }

    def __str__(self) -> str:
        return f"{self.display or self.node_id[:12]} ({self.distance} hop(s), {self.score:.2f})"


@dataclass(frozen=True, slots=True)
class Elision:
    """What fell below the threshold — kept as a count, never dropped.

    This is the context half. A reader who cannot see how much is out of frame
    has been shown a focus and told it is the whole picture, which is the
    honesty failure this platform refuses everywhere else.
    """

    key: str
    count: int
    best: float
    nearest: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "count": self.count,
            "best": round(self.best, 4), "nearest": self.nearest,
        }

    def __str__(self) -> str:
        return f"{self.count} more {self.key}"


@dataclass(frozen=True, slots=True)
class Field:
    """A bounded render set: what this selection is worth drawing, and what it hides."""

    focus: tuple[str, ...] = ()
    rendered: tuple[Interest, ...] = ()
    elided: tuple[Elision, ...] = ()
    threshold: float = 0.0
    horizon: int = HORIZON
    budget: int = BUDGET
    considered: int = 0
    truncated: bool = False

    @property
    def hidden(self) -> int:
        return sum(mark.count for mark in self.elided)

    def summary(self) -> str:
        if not self.rendered:
            return "nothing within reach of this selection"
        text = f"{len(self.rendered)} of {self.considered} within {self.horizon} hop(s)"
        if self.hidden:
            text += f"; {self.hidden} elided into {len(self.elided)} mark(s)"
        if self.truncated:
            text += f" (the walk stopped at {REACH} nodes)"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "focus": list(self.focus), "summary": self.summary(),
            "threshold": round(self.threshold, 4), "horizon": self.horizon,
            "budget": self.budget, "considered": self.considered,
            "hidden": self.hidden, "truncated": self.truncated,
            "rendered": [item.to_dict() for item in self.rendered],
            "elided": [mark.to_dict() for mark in self.elided],
        }

    def __len__(self) -> int:
        return len(self.rendered)

    def __iter__(self):
        return iter(self.rendered)


def score(importance: float, distance: int, *, hop_cost: float = HOP_COST) -> float:
    """Furnas, verbatim: importance minus distance, distance measured in hops."""
    return importance - hop_cost * distance


def survey(
    candidates: Iterable[Interest],
    *,
    focus: Sequence[str] = (),
    budget: int = BUDGET,
    horizon: int = HORIZON,
    threshold: float | None = None,
    considered: int = 0,
    truncated: bool = False,
) -> Field:
    """Rank, cut, and elide the remainder — the whole of the render decision.

    The cut is deterministic: ties break on ``node_id``, so the same selection
    over the same tree yields the same set every time. A field that reshuffled
    between two identical questions would make the picture unciteable.

    The focus itself always renders, whatever it scores. A selection that
    dropped the thing you selected would be a bug wearing a threshold.
    """
    ranked = sorted(candidates, key=lambda item: (-item.score, item.node_id))
    chosen = set(focus)

    if threshold is None:
        head = [item for item in ranked if item.node_id not in chosen][: max(0, budget)]
        cut = head[-1].score if head else 0.0
    else:
        cut = threshold

    rendered: list[Interest] = []
    remainder: list[Interest] = []
    for item in ranked:
        if item.node_id in chosen or (item.score >= cut and len(rendered) < budget):
            rendered.append(item)
        else:
            remainder.append(item)

    grouped: dict[str, list[Interest]] = {}
    for item in remainder:
        grouped.setdefault(item.kind or "unknown", []).append(item)

    elided = tuple(
        Elision(
            key=key,
            count=len(items),
            best=max(item.score for item in items),
            nearest=min(item.distance for item in items),
        )
        for key, items in sorted(grouped.items())
    )

    return Field(
        focus=tuple(focus), rendered=tuple(rendered), elided=elided,
        threshold=cut, horizon=horizon, budget=budget,
        considered=considered or len(ranked), truncated=truncated,
    )


class Surveyor:
    """Reads the graph once per selection and answers with a bounded field.

    Severities and boundary membership are injected rather than looked up: the
    graph does not know what the governance layer found or what the manifest
    declared, and reaching for either from here would put a second copy of that
    knowledge in the one module whose whole job is to weigh what already exists.

    ``boundary`` is a predicate rather than a set of ids on purpose. The
    manifest already owns the membership rule (``Boundary.holds``), and asking
    it per node keeps the question bounded to what the walk reached — resolving
    a set up front would mean a full table scan on every selection, which is the
    shape of the defect §32 step 0 had just finished removing from the context
    index.
    """

    def __init__(
        self,
        graph: Any,
        *,
        severities: Mapping[str, Severity] | None = None,
        boundary: Callable[[Node], bool] | None = None,
    ) -> None:
        self.graph = graph
        self.severities = dict(severities or {})
        self.boundary = boundary

    def field(
        self,
        focus: Iterable[str],
        *,
        horizon: int = HORIZON,
        budget: int = BUDGET,
        threshold: float | None = None,
        reach: int = REACH,
    ) -> Field:
        roots = tuple(dict.fromkeys(item for item in focus if item))
        if not roots:
            return Field(horizon=horizon, budget=budget)

        hops, degrees, truncated = self._walk(roots, horizon=horizon, reach=reach)

        candidates: list[Interest] = []
        for node_id, distance in hops.items():
            node = self.graph.node(node_id)
            if node is None:
                continue
            signals = Signals.of(
                node,
                degree=degrees.get(node_id, 0),
                severity=self.severities.get(node_id),
                boundary=bool(self.boundary and self.boundary(node)),
            )
            weight, reasons = signals.weigh()
            candidates.append(Interest(
                node_id=node_id, display=node.display, kind=node.kind.value,
                distance=distance, importance=weight,
                score=score(weight, distance), reasons=reasons,
            ))

        return survey(
            candidates, focus=roots, budget=budget, horizon=horizon,
            threshold=threshold, considered=len(candidates), truncated=truncated,
        )

    def _walk(
        self, roots: Sequence[str], *, horizon: int, reach: int
    ) -> tuple[dict[str, int], dict[str, int], bool]:
        """Breadth-first in both directions, bounded by horizon and by reach.

        Both directions, because interest is not directional: what a node
        depends on and what depends on it are equally part of its
        neighbourhood, and a one-way walk would answer half the question while
        looking like it answered all of it.
        """
        hops: dict[str, int] = {root: 0 for root in roots}
        degrees: dict[str, int] = {}
        queue: deque[str] = deque(roots)
        truncated = False

        while queue:
            current = queue.popleft()
            distance = hops[current]
            neighbours = [edge.dst for edge in self.graph.edges_from(current)]
            neighbours += [edge.src for edge in self.graph.edges_to(current)]
            degrees[current] = len(neighbours)
            if distance >= horizon:
                continue
            for neighbour in neighbours:
                if neighbour in hops:
                    continue
                if len(hops) >= reach:
                    truncated = True
                    break
                hops[neighbour] = distance + 1
                queue.append(neighbour)
            if truncated:
                break

        # A node the walk reached but never popped still has a real degree, and
        # leaving it at zero would understate its importance silently — the one
        # thing a score with reasons attached must not do.
        for node_id in hops:
            if node_id not in degrees:
                degrees[node_id] = (
                    len(self.graph.edges_from(node_id)) + len(self.graph.edges_to(node_id))
                )

        return hops, degrees, truncated
