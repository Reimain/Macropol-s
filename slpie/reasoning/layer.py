"""What a layer is, and the rule that makes the pipeline explainable.

Ten layers, each reading what came before and appending what it worked out. The
whole design rests on one constraint:

**A layer appends; it never mutates a prior layer's output.**

That is what makes an explanation possible. Every `Enrichment` cites the
evidence ids and prior enrichment ids it was derived from, so walking
`derived_from` backwards from any conclusion terminates in raw evidence with a
file and a line. If a layer could overwrite what an earlier one said, the chain
would lead to something that no longer exists, and "why does the platform
believe this?" would have no answer — which is the failure mode this whole
architecture is built to avoid.

The second rule is about failure. A layer that raises **abstains**: its error is
recorded, the pipeline continues, and every answer built afterwards carries the
gap. Aborting would mean one bad layer costs the other nine, and swallowing the
error silently would produce an answer that looks complete and is not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.evidence import Evidence
from ..domain.reasoning import Enrichment, LayerNumber, ReasoningStep
from ..errors import SlpieError


class LayerError(SlpieError):
    """A layer was declared or composed wrongly. Runtime failures abstain."""


@dataclass(slots=True)
class LayerContext:
    """What a layer reads, and where it puts what it worked out.

    Deliberately mutable and deliberately shared: layers accumulate into one
    context so that L4 can read what L2 concluded. What they may *not* do is
    replace anything already there — `add` refuses to overwrite an enrichment
    id, which is the append-only rule enforced rather than trusted.
    """

    element: str = ""
    observations: tuple[Any, ...] = ()
    resolution: Any = None
    joined: tuple[Any, ...] = ()
    enrichments: dict[str, Enrichment] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0

    def remember(self, evidence: Evidence) -> str:
        """Index one piece of raw evidence by id, and return that id.

        The chain is walkable only if the far end can be looked up. L1 indexes
        everything it reads here, so `roots()` returns ids that resolve to a
        file and a line rather than to nothing.
        """
        self.evidence[evidence.id] = evidence
        return evidence.id

    def add(self, enrichment: Enrichment) -> Enrichment:
        """Append one derived fact. Refuses to overwrite, and refuses to float.

        An enrichment with no `derived_from` is refused outright: a layer that
        cannot say what it read to reach a conclusion has not reasoned, it has
        asserted, and the explanation would dead-end there.
        """
        if not enrichment.grounded:
            raise LayerError(
                f"{enrichment.layer} asserted {enrichment.attribute!r} on "
                f"{enrichment.subject} without citing anything; an enrichment "
                f"that cites nothing breaks the chain every explanation walks"
            )
        existing = self.enrichments.get(enrichment.id)
        if existing is not None:
            return existing
        self.enrichments[enrichment.id] = enrichment
        return enrichment

    def of_layer(self, layer: str) -> tuple[Enrichment, ...]:
        return tuple(e for e in self.enrichments.values() if e.layer == layer)

    def about(self, subject: str) -> tuple[Enrichment, ...]:
        return tuple(e for e in self.enrichments.values() if e.subject == subject)

    def attribute(self, subject: str, attribute: str) -> Any:
        for enrichment in self.enrichments.values():
            if enrichment.subject == subject and enrichment.attribute == attribute:
                return enrichment.value
        return None

    def trace(self, enrichment_id: str, *, depth: int = 0) -> tuple[str, ...]:
        """Walk `derived_from` backwards. The chain, as ids, roots last.

        Bounded because a malformed chain could otherwise cycle: an enrichment
        citing itself, or two citing each other. The bound is generous enough
        that no legitimate chain reaches it.
        """
        if depth > 32:
            return ()
        enrichment = self.enrichments.get(enrichment_id)
        if enrichment is None:
            return (enrichment_id,)
        chain: list[str] = [enrichment_id]
        for parent in enrichment.derived_from:
            chain.extend(self.trace(parent, depth=depth + 1))
        return tuple(chain)

    def roots(self, enrichment_id: str) -> tuple[str, ...]:
        """The ids at the end of the chain — the raw evidence it rests on."""
        return tuple(
            item for item in self.trace(enrichment_id)
            if item not in self.enrichments
        )


@dataclass(frozen=True, slots=True)
class LayerResult:
    """What one layer did, and what it could not do."""

    layer: str
    number: int
    enrichments: tuple[Enrichment, ...] = ()
    steps: tuple[ReasoningStep, ...] = ()
    errors: tuple[str, ...] = ()
    abstained: bool = False
    duration: float = 0.0

    @property
    def clean(self) -> bool:
        return not self.errors and not self.abstained

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer, "number": self.number,
            "enrichments": len(self.enrichments), "steps": len(self.steps),
            "errors": list(self.errors), "abstained": self.abstained,
            "clean": self.clean, "duration": round(self.duration, 4),
        }

    def __str__(self) -> str:
        if self.abstained:
            return f"L{self.number} {self.layer}: abstained — {self.errors[0] if self.errors else ''}"
        return f"L{self.number} {self.layer}: {len(self.enrichments)} enrichments"


@runtime_checkable
class Layer(Protocol):
    """One stage of the pipeline."""

    name: str
    number: LayerNumber

    def run(self, context: LayerContext) -> LayerResult:
        ...


class BaseLayer:
    """Shared plumbing so each layer states only what it actually does."""

    name: str = "base"
    number: LayerNumber = LayerNumber.DISCOVERY

    def run(self, context: LayerContext) -> LayerResult:
        started = time.monotonic()
        added: list[Enrichment] = []
        steps: list[ReasoningStep] = []
        errors: list[str] = []

        try:
            self.execute(context, added.append, steps.append)
        except Exception as error:  # noqa: BLE001 - a layer may be third-party
            # Abstain: one layer failing must not cost the other nine, and the
            # gap travels with every answer built afterwards.
            return LayerResult(
                layer=self.name, number=int(self.number), abstained=True,
                errors=(f"{type(error).__name__}: {error}",),
                duration=time.monotonic() - started,
            )

        for enrichment in added:
            try:
                context.add(enrichment)
            except LayerError as error:
                errors.append(str(error))

        return LayerResult(
            layer=self.name, number=int(self.number),
            enrichments=tuple(added), steps=tuple(steps),
            errors=tuple(errors), duration=time.monotonic() - started,
        )

    def execute(
        self,
        context: LayerContext,
        emit: Any,
        step: Any,
    ) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def enrich(self, subject: str, attribute: str, value: Any, *,
               derived_from: Iterable[str], rationale: str = "",
               confidence: float = 0.0) -> Enrichment:
        return Enrichment(
            subject=subject, attribute=attribute, value=value,
            layer=self.name, derived_from=tuple(derived_from),
            rationale=rationale, confidence=confidence,
            derived_at=int(time.time()),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<{type(self).__name__} L{int(self.number)} {self.name}>"
