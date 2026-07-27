"""Reasoning primitives — how a conclusion traces back to a line in a file.

The pipeline's ten layers enrich without ever mutating a prior layer. That is
only useful if the chain is walkable: every :class:`Enrichment` names what it was
``derived_from``, and following those references backwards from any conclusion
terminates in raw evidence with a URI and a line number. If a walk terminates
anywhere else, the conclusion is unfounded — and :func:`ReasoningPath.grounded`
says so rather than letting it pass.

The other half of an honest answer lives here too. :class:`Guidance` is what the
console returns: not a value, but a value *plus* the path that produced it, the
gaps that limit it, and the questions worth asking next. When the question
itself is ambiguous the console returns a :class:`Clarification` instead — it
asks back rather than guessing which `payments` you meant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .evidence import Evidence, SourceLocation
from .finding import Gap
from .identity import digest


class LayerNumber(int, Enum):
    """The ten layers, named. Ordering is the pipeline's execution order."""

    DISCOVERY = 1
    NORMALIZATION = 2
    GRAPH_CONSTRUCTION = 3
    SEMANTIC_LINKING = 4
    ARCHITECTURE_VALIDATION = 5
    CONSTRAINT_SOLVING = 6
    IMPACT = 7
    OPTIMIZATION = 8
    GOVERNANCE = 9
    RECOMMENDATION = 10

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


@dataclass(frozen=True, slots=True)
class Enrichment:
    """One derived fact, and the chain it came from.

    ``derived_from`` holds evidence ids and prior enrichment ids. It is the whole
    mechanism: a layer may add anything it likes as long as it can say what it
    read to get there.
    """

    subject: str                       # node or edge id
    attribute: str
    value: Any
    layer: str
    derived_from: tuple[str, ...] = ()
    confidence: float = 0.0
    rationale: str = ""
    derived_at: int = 0

    @property
    def id(self) -> str:
        return digest({
            "subject": self.subject,
            "attribute": self.attribute,
            "layer": self.layer,
            "derived_from": sorted(self.derived_from),
        })

    @property
    def grounded(self) -> bool:
        """False for a fact asserted out of nowhere by a layer."""
        return bool(self.derived_from)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "subject": self.subject, "attribute": self.attribute,
            "value": self.value, "layer": self.layer,
            "derived_from": list(self.derived_from), "confidence": self.confidence,
            "rationale": self.rationale, "derived_at": self.derived_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Enrichment":
        return cls(
            subject=data["subject"], attribute=data["attribute"], value=data.get("value"),
            layer=data["layer"], derived_from=tuple(data.get("derived_from", ())),
            confidence=float(data.get("confidence", 0.0)),
            rationale=data.get("rationale", ""), derived_at=int(data.get("derived_at", 0)),
        )

    def __str__(self) -> str:
        return f"{self.subject}.{self.attribute} = {self.value!r} ({self.layer})"


@dataclass(frozen=True, slots=True)
class ReasoningStep:
    """One move in an explanation, rendered as one line in the UI.

    ``evidence`` is the literal observations this step rests on — the step shows
    its sources, so "because lodash appears in package-lock.json:1204" is the
    explanation rather than a paraphrase of one.
    """

    claim: str
    layer: str = ""
    evidence: tuple[Evidence, ...] = ()
    inputs: tuple[str, ...] = ()       # node/edge/enrichment ids this step consumed
    confidence: float = 1.0
    operation: str = ""                # "traverse", "resolve", "match", "solve", "compare"

    @property
    def grounded(self) -> bool:
        return bool(self.evidence)

    @property
    def locations(self) -> tuple[SourceLocation, ...]:
        return tuple(item.location for item in self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim, "layer": self.layer, "operation": self.operation,
            "confidence": self.confidence, "inputs": list(self.inputs),
            "grounded": self.grounded,
            "evidence": [
                {
                    "kind": item.kind.value,
                    "reference": item.location.reference,
                    "uri": item.location.uri,
                    "excerpt": item.excerpt[:300],
                }
                for item in self.evidence
            ],
        }

    def __str__(self) -> str:
        sources = ", ".join(item.location.reference for item in self.evidence)
        return f"{self.claim}" + (f" [{sources}]" if sources else "")


@dataclass(frozen=True, slots=True)
class ReasoningPath:
    """A complete explanation: ordered steps ending in evidence.

    Confidence over the path is the *minimum* across its steps, not the average.
    A chain is exactly as strong as its weakest link, and averaging would let one
    0.25 name-heuristic hop hide behind four lockfile pins.
    """

    steps: tuple[ReasoningStep, ...] = ()
    question: str = ""

    @property
    def confidence(self) -> float:
        return min((step.confidence for step in self.steps), default=0.0)

    @property
    def grounded(self) -> bool:
        """True only when every step cites evidence.

        An ungrounded path is the platform reasoning from its own conclusions,
        which is precisely what the provenance requirement exists to prevent.
        """
        return bool(self.steps) and all(step.grounded for step in self.steps)

    @property
    def weakest(self) -> ReasoningStep | None:
        return min(self.steps, key=lambda s: s.confidence, default=None)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        seen: dict[str, Evidence] = {}
        for step in self.steps:
            for item in step.evidence:
                seen.setdefault(item.id, item)
        return tuple(seen.values())

    @property
    def sources(self) -> tuple[str, ...]:
        """Distinct file references — what a reviewer opens to check the answer."""
        return tuple(dict.fromkeys(item.location.reference for item in self.evidence))

    def then(self, step: ReasoningStep) -> "ReasoningPath":
        return ReasoningPath(steps=(*self.steps, step), question=self.question)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "confidence": self.confidence,
            "grounded": self.grounded,
            "sources": list(self.sources),
            "steps": [step.to_dict() for step in self.steps],
        }

    def render(self) -> str:
        """Plain-text explanation, for the CLI and for logs."""
        lines = []
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"{index}. {step}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)


@dataclass(frozen=True, slots=True)
class Question:
    """A next question the console offers, ranked by what answering it would buy.

    ``information_gain`` is the expected confidence lift across the current
    answer's reasoning path. Ranking by it means the console offers the question
    that would most improve the answer, not the one that is easiest to generate.
    """

    text: str
    intent: str = ""                    # the tool this would route to
    information_gain: float = 0.0
    rationale: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return digest({"text": self.text, "intent": self.intent})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "intent": self.intent,
            "information_gain": self.information_gain, "rationale": self.rationale,
            "parameters": dict(self.parameters),
        }

    def __str__(self) -> str:
        return self.text


class ActionKind(str, Enum):
    """What the console can offer to *do*, not merely report."""

    DECLARE = "declare"          # add an element to the environment manifest
    ATTACH = "attach"            # register a declared element
    GRANT_CAPABILITY = "grant_capability"
    UPGRADE = "upgrade"
    PIN = "pin"
    REPLACE = "replace"
    SUPPRESS = "suppress"
    RUN_SCENARIO = "run_scenario"
    GENERATE = "generate"        # emit an artifact
    RESCAN = "rescan"

    @property
    def mutates_environment(self) -> bool:
        """Actions that change the target rather than SLPIE's own state.

        These are the ones the live-target guard gates.
        """
        return self in (ActionKind.UPGRADE, ActionKind.PIN, ActionKind.REPLACE)


@dataclass(frozen=True, slots=True)
class SuggestedAction:
    """Something concrete the operator can do next, offered with its consequence."""

    kind: ActionKind
    summary: str
    target: str = ""
    command: str = ""                   # the CLI equivalent, so the UI is never a dead end
    parameters: Mapping[str, Any] = field(default_factory=dict)
    closes_gap: str = ""                # gap id this would resolve
    breaking: bool = False

    @property
    def id(self) -> str:
        return digest({"kind": self.kind.value, "target": self.target, "summary": self.summary})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind.value, "summary": self.summary,
            "target": self.target, "command": self.command,
            "parameters": dict(self.parameters), "closes_gap": self.closes_gap,
            "breaking": self.breaking,
            "mutates_environment": self.kind.mutates_environment,
        }

    def __str__(self) -> str:
        return self.summary


@dataclass(frozen=True, slots=True)
class Guidance:
    """The console's answer. Never a bare value.

    Four parts, all required by the platform's fifth invariant: what the answer
    is, how it was reached, what limits it, and what to ask or do next. Returning
    only ``answer`` would make the platform a lookup table.
    """

    answer: Any
    reasoning: ReasoningPath = field(default_factory=ReasoningPath)
    gaps: tuple[Gap, ...] = ()
    next_questions: tuple[Question, ...] = ()
    actions: tuple[SuggestedAction, ...] = ()
    summary: str = ""

    @property
    def confidence(self) -> float:
        """The path's confidence, discounted by the gaps that limit it.

        Gaps are not cosmetic. An answer assembled from complete evidence about
        two thirds of an ecosystem is not a confident answer about the ecosystem,
        and this is where that shows up as a number.
        """
        base = self.reasoning.confidence
        for gap in self.gaps:
            base *= 1.0 - min(max(gap.confidence_impact, 0.0), 1.0)
        return round(base, 4)

    @property
    def complete(self) -> bool:
        """True when nothing limited this answer."""
        return not self.gaps and self.reasoning.grounded

    @property
    def actionable_gaps(self) -> tuple[Gap, ...]:
        return tuple(gap for gap in self.gaps if gap.actionable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "guidance",
            "answer": self.answer,
            "summary": self.summary,
            "confidence": self.confidence,
            "complete": self.complete,
            "reasoning": self.reasoning.to_dict(),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "next_questions": [question.to_dict() for question in self.next_questions],
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(frozen=True, slots=True)
class Option:
    """One disambiguation choice, with enough context to pick between them."""

    label: str
    value: str                          # node id or identity string
    detail: str = ""
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "detail": self.detail, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class Clarification:
    """A question back, when guessing would be worse than asking.

    Three `payments` nodes — a service, a package and a database — is not a
    situation where the platform should pick the most popular one. The options
    come from the graph, so the question is always answerable.
    """

    question: str
    options: tuple[Option, ...] = ()
    because: str = ""
    original_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "clarification",
            "question": self.question,
            "because": self.because,
            "original_question": self.original_question,
            "options": [option.to_dict() for option in self.options],
        }

    def __str__(self) -> str:
        return self.question


#: What a console turn resolves to — an answer, or a question back.
Outcome = Guidance | Clarification


def rank_questions(questions: Iterable[Question], *, limit: int = 5) -> tuple[Question, ...]:
    """Highest information gain first; ties broken stably by text."""
    ordered = sorted(questions, key=lambda q: (-q.information_gain, q.text))
    return tuple(ordered[:limit])


def trace_to_evidence(
    enrichment: Enrichment,
    *,
    enrichments: Mapping[str, Enrichment],
    evidence: Mapping[str, Evidence],
) -> tuple[Evidence, ...]:
    """Walk ``derived_from`` backwards until only raw evidence remains.

    Cycles are impossible by construction — an enrichment's id depends on its
    inputs, so it cannot reference itself — but the visited set keeps a corrupted
    ledger from hanging the explainer.
    """
    collected: dict[str, Evidence] = {}
    visited: set[str] = set()
    frontier: list[str] = list(enrichment.derived_from)

    while frontier:
        reference = frontier.pop()
        if reference in visited:
            continue
        visited.add(reference)
        if reference in evidence:
            collected.setdefault(reference, evidence[reference])
        elif reference in enrichments:
            frontier.extend(enrichments[reference].derived_from)
        # A dangling reference is silently skipped here; `grounded` on the
        # resulting path is what reports it, with the subject attached.

    return tuple(collected.values())
