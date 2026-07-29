"""What travels down the pipe.

A shell pipe carries bytes, which is why `curl | jq | grep` can tell you a value
and never why. It is the right trade for text processing and the wrong one here:
the whole platform rests on every answer carrying the path that produced it, and
a pipe that dropped that would break invariant 5 at exactly the point where
composition makes it most valuable.

So the pipe carries a **`Flow`**: a value, its kind, the reasoning path behind
it, and the gaps that limit it. Composing **accumulates** provenance rather than
discarding it — `scan | link | findings` ends with findings that still carry the
capability `scan` was refused, four stages back. That is the property that makes
a long pipeline trustworthy instead of merely convenient.

The second job of this module is the **kind**, and it does more work than it
looks like. Kinds are what let a composition be checked before it runs, what let
the manual say "these verbs can follow that one", and what give the offline
planner a search space instead of a guess. Two of them are special:

* `ANY` — a verb that accepts anything. The shell filters (`head`, `sort`,
  `count`, `json`) are polymorphic for the same reason `wc` is.
* `SAME` — a verb that produces whatever it was given. Declaring passthrough
  explicitly is what stops a filter from erasing the kind of the thing flowing
  through it, which would silently break every downstream type check.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..domain.finding import Gap
from ..domain.identity import digest
from ..domain.reasoning import ReasoningPath, ReasoningStep


#: Operations that rearrange an answer without asserting anything about the world.
#: Named once here so `Flow.grounded` can judge derivations only — a filter that
#: kept three of eighty is a real operation with nothing to cite, and letting it
#: fail the groundedness check would flag every piped answer as unfounded.
SHAPING = frozenset({"shape", "explain", "render"})


class Kind(str, Enum):
    """What a stage of a pipeline holds. The type system of the composition."""

    NOTHING = "nothing"              # a source verb consumes this
    ANY = "any"                      # a polymorphic filter accepts this
    SAME = "same"                     # ... and produces this

    MANIFEST = "manifest"
    ELEMENTS = "elements"
    OBSERVATIONS = "observations"
    RESOLUTION = "resolution"
    ENRICHMENTS = "enrichments"
    NODES = "nodes"
    EDGES = "edges"
    GRAPH = "graph"
    IMPACT = "impact"
    REQUIREMENTS = "requirements"
    SOLUTION = "solution"
    FINDINGS = "findings"
    GAPS = "gaps"
    JUDGEMENTS = "judgements"
    PLAN = "plan"
    REPORT = "report"
    TEXT = "text"

    @property
    def label(self) -> str:
        return self.value.upper()

    @property
    def polymorphic(self) -> bool:
        return self in (Kind.ANY, Kind.SAME)

    def accepts(self, other: "Kind") -> bool:
        """Whether a verb consuming `self` can be handed a `other`.

        `ANY` accepts everything, which is what makes the shell filters work.
        Nothing else is lenient: a near-miss that were allowed through would
        surface as an attribute error three stages later, and the whole point of
        typing the pipe is that the failure arrives before anything runs.
        """
        if self is Kind.ANY:
            return True
        if self is Kind.NOTHING:
            return other is Kind.NOTHING
        return self is other


@dataclass(frozen=True, slots=True)
class Flow:
    """One value in transit, with everything needed to justify it.

    Immutable, and extended rather than mutated: `then()` returns a new `Flow`
    carrying the accumulated reasoning and gaps. A stage that wanted to *remove*
    a gap cannot, which is deliberate — a pipeline must not be able to launder
    away the thing that limits its own answer.
    """

    kind: Kind
    value: Any = None
    reasoning: ReasoningPath = field(default_factory=ReasoningPath)
    gaps: tuple[Gap, ...] = ()
    stages: tuple[str, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls) -> "Flow":
        """The empty flow a source verb is handed."""
        return cls(kind=Kind.NOTHING)

    def then(
        self,
        kind: Kind,
        value: Any,
        *,
        stage: str,
        steps: Iterable[ReasoningStep] = (),
        gaps: Iterable[Gap] = (),
        facts: Mapping[str, Any] | None = None,
    ) -> "Flow":
        """The next flow: new value, everything explanatory carried forward.

        Gaps accumulate and are deduplicated by id — a gap reported by two
        stages is one gap, but a gap reported by one stage is never dropped by
        the next.
        """
        merged: dict[str, Gap] = {gap.id: gap for gap in self.gaps}
        for gap in gaps:
            merged.setdefault(gap.id, gap)

        path = self.reasoning
        for step in steps:
            path = path.then(step)

        combined = dict(self.facts)
        combined.update(facts or {})

        return Flow(
            kind=self.kind if kind is Kind.SAME else kind,
            value=value,
            reasoning=path,
            gaps=tuple(merged.values()),
            stages=(*self.stages, stage),
            facts=combined,
        )

    # -- inspection ------------------------------------------------------

    @property
    def empty(self) -> bool:
        if self.value is None:
            return True
        if isinstance(self.value, (tuple, list, dict, str)):
            return len(self.value) == 0
        return False

    @property
    def size(self) -> int:
        """How many things this holds. 1 for a scalar, so `count` reads sanely."""
        if self.value is None:
            return 0
        if isinstance(self.value, (tuple, list, dict, str)):
            return len(self.value)
        return 1

    @property
    def items(self) -> tuple[Any, ...]:
        """The value as a sequence, so the shaping verbs need no special cases."""
        if self.value is None:
            return ()
        if isinstance(self.value, tuple):
            return self.value
        if isinstance(self.value, list):
            return tuple(self.value)
        return (self.value,)

    @property
    def confidence(self) -> float:
        """The path's confidence, discounted by what the gaps cost.

        Gaps do not merely annotate an answer, they lower it. An answer built
        over a refused capability is genuinely less certain, and reporting the
        raw path confidence would present it as though nothing were missing.
        """
        base = self.reasoning.confidence if len(self.reasoning) else 1.0
        for gap in self.gaps:
            base *= (1.0 - min(0.9, max(0.0, gap.confidence_impact)))
        return round(max(0.0, min(1.0, base)), 4)

    @property
    def substantive(self) -> tuple[ReasoningStep, ...]:
        """The steps that assert something about the world.

        A filter keeping the first three of eighty is a real operation and belongs
        in the audit trail, but it derives no claim — so demanding that it cite
        evidence is a category error, and letting it fail `grounded` would flag
        every piped answer as unfounded. `SHAPING` names those operations once so
        the distinction is explicit rather than implied by which verbs happen to
        pass evidence along.
        """
        return tuple(
            step for step in self.reasoning.steps
            if step.operation not in SHAPING
        )

    @property
    def grounded(self) -> bool:
        """Whether every claim about the world traces to evidence.

        Judged over the substantive steps. An empty flow is not grounded, because
        nothing has been established yet — reporting `True` there would mean an
        answer nobody derived passed the check that exists to catch exactly that.
        """
        steps = self.substantive
        return bool(steps) and all(step.grounded for step in steps)

    @property
    def digest(self) -> str:
        """Content-addressed over the answer, not over the run that produced it.

        Two surfaces executing the same composition must agree, and this is how
        that gets asserted rather than eyeballed. Timing is deliberately excluded:
        `duration`, `observed_at` and their kin measure *this run*, and including
        them meant two identical runs produced different digests — which made the
        value useless for the one thing it exists for.
        """
        return digest({
            "kind": self.kind.value,
            "stages": list(self.stages),
            "size": self.size,
            "gaps": sorted(gap.id for gap in self.gaps),
            "value": _stable(self.value, comparable=True),
        })

    def to_dict(self, *, limit: int = 200) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "size": self.size,
            "stages": list(self.stages),
            "confidence": self.confidence,
            "grounded": self.grounded,
            "digest": self.digest,
            "gaps": [gap.to_dict() for gap in self.gaps],
            "reasoning": self.reasoning.to_dict(),
            "value": _stable(self.value, limit=limit),
            "facts": dict(self.facts),
        }

    def __len__(self) -> int:
        return self.size

    def __iter__(self) -> Iterator[Any]:
        return iter(self.items)

    def __str__(self) -> str:
        head = f"{self.kind.label}[{self.size}]"
        route = " | ".join(self.stages) if self.stages else "start"
        tail = f", {len(self.gaps)} gap(s)" if self.gaps else ""
        return f"{head} via {route}{tail}"


#: Keys that measure the run rather than the answer. Dropped when computing a
#: digest so that two identical runs agree; kept in `to_dict` because a UI showing
#: how long something took is useful.
VOLATILE = frozenset({
    "duration", "duration_ns", "observed_at", "derived_at", "raised_at",
    "started_at", "occurred_at", "attached_at", "elapsed", "timestamp",
})


def _stable(
    value: Any,
    *,
    limit: int = 200,
    depth: int = 0,
    comparable: bool = False,
) -> Any:
    """A JSON-safe rendering, bounded so a 10k-node graph cannot blow the wire.

    `to_dict` is preferred wherever an object offers it, so domain types render
    the way the rest of the platform renders them rather than as `repr` strings.

    With `comparable`, keys in `VOLATILE` are dropped recursively — that is what
    makes a digest a property of the answer instead of a property of the clock.
    """
    if depth > 6:
        return "..."
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if hasattr(value, "to_dict"):
        try:
            rendered = value.to_dict()
        except Exception:  # noqa: BLE001 - a plugin's to_dict is not ours
            return str(value)
        # Recurse rather than returning it whole: a `to_dict` result nests, and
        # volatile keys hide at every level.
        return _stable(rendered, limit=limit, depth=depth + 1, comparable=comparable)

    if isinstance(value, Mapping):
        return {
            str(key): _stable(item, limit=limit, depth=depth + 1, comparable=comparable)
            for key, item in list(value.items())[:limit]
            if not (comparable and str(key) in VOLATILE)
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _stable(item, limit=limit, depth=depth + 1, comparable=comparable)
            for item in list(value)[:limit]
        ]
    return str(value)
