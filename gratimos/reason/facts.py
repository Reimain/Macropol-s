"""Facts and patterns — the working memory both reasoning directions share.

A fact is a triple with provenance: ``(subject, predicate, object)`` plus where
it came from and how much it is worth. Provenance is not decoration — walking
``derived_from`` backwards from any conclusion must terminate in something
observed, or the conclusion is unfounded and the engine should say so rather
than assert it.

A pattern is a fact with holes in it. ``("?pkg", "provides", "retry")`` matches
every fact whose predicate and object agree, binding ``?pkg`` to each subject.
That single mechanism serves both directions: forward chaining matches patterns
against known facts, backward chaining matches a goal against rule heads.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError


class ReasoningError(GratimosError):
    """The inference engine could not complete, or was asked something malformed."""


class Provenance(str, Enum):
    """How a fact came to be known. The ordering is the trust model.

    Mirrors SLPIE's evidence kinds deliberately: the two systems exchange
    claims, and a fact that crosses the boundary should not change value on the
    way.
    """

    OBSERVED = "observed"          # read from a real artifact
    DECLARED = "declared"          # stated by an operator or a manifest
    VALIDATED = "validated"        # an agent checked it and agreed
    DERIVED = "derived"            # the engine inferred it
    DISTILLED = "distilled"        # remembered from an earlier validation
    ASSUMED = "assumed"            # a default that nothing has contradicted

    @property
    def base_confidence(self) -> float:
        return _BASE[self]

    @property
    def is_inferred(self) -> bool:
        return self in (Provenance.DERIVED, Provenance.DISTILLED, Provenance.ASSUMED)


_BASE: dict[Provenance, float] = {
    Provenance.OBSERVED: 0.98,
    Provenance.VALIDATED: 0.95,
    Provenance.DECLARED: 0.90,
    Provenance.DISTILLED: 0.75,
    Provenance.DERIVED: 0.70,
    Provenance.ASSUMED: 0.40,
}

#: A term beginning with `?` is a variable rather than a literal.
VARIABLE = "?"

#: Ceiling for anything the engine worked out for itself. Certainty stays with
#: observation, exactly as it does in SLPIE — a chain of good inferences is
#: still a chain of inferences.
INFERENCE_CEILING = 0.95


def is_variable(term: str) -> bool:
    return isinstance(term, str) and term.startswith(VARIABLE)


@dataclass(frozen=True, slots=True)
class Fact:
    """One grounded assertion, with its justification."""

    subject: str
    predicate: str
    object: str
    provenance: Provenance = Provenance.DERIVED
    confidence: float = 0.0
    derived_from: tuple[str, ...] = ()
    rule: str = ""
    source: str = ""
    asserted_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.subject or not self.predicate:
            raise ReasoningError("a fact needs at least a subject and a predicate")
        if any(is_variable(term) for term in (self.subject, self.predicate, self.object)):
            raise ReasoningError(
                f"a fact cannot contain variables: {self.subject} {self.predicate} {self.object}"
            )
        if not self.confidence:
            object.__setattr__(self, "confidence", self.provenance.base_confidence)
        if not self.asserted_at:
            object.__setattr__(self, "asserted_at", time.time())

    @property
    def id(self) -> str:
        """Content-addressed on the claim itself, not on how it was reached.

        Two routes to the same conclusion are one fact — which is what lets the
        forward chainer reach a fixpoint instead of rediscovering the same
        triple by every available path.
        """
        material = f"{self.subject}\x1f{self.predicate}\x1f{self.object}"
        return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()

    @property
    def triple(self) -> tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)

    @property
    def grounded(self) -> bool:
        """False for an inference that cites nothing."""
        return not self.provenance.is_inferred or bool(self.derived_from)

    def stronger_than(self, other: "Fact") -> bool:
        return self.confidence > other.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "subject": self.subject, "predicate": self.predicate,
            "object": self.object, "provenance": self.provenance.value,
            "confidence": round(self.confidence, 4), "rule": self.rule,
            "derived_from": list(self.derived_from), "source": self.source,
            "asserted_at": self.asserted_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Fact":
        return cls(
            subject=data["subject"], predicate=data["predicate"],
            object=data.get("object", ""),
            provenance=Provenance(data.get("provenance", "derived")),
            confidence=float(data.get("confidence", 0.0)),
            derived_from=tuple(data.get("derived_from", ())),
            rule=data.get("rule", ""), source=data.get("source", ""),
            asserted_at=float(data.get("asserted_at", 0.0)),
        )

    def __str__(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}"


@dataclass(frozen=True, slots=True)
class Pattern:
    """A fact with variables in it. The matching primitive for both directions."""

    subject: str
    predicate: str
    object: str = ""

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(
            term for term in (self.subject, self.predicate, self.object)
            if is_variable(term)
        )

    @property
    def is_ground(self) -> bool:
        return not self.variables

    def match(self, fact: Fact, bindings: Mapping[str, str] | None = None) -> dict[str, str] | None:
        """Unify against a fact, extending bindings. None when it cannot.

        Extends rather than replaces, so a rule with a repeated variable —
        ``(?a depends-on ?b), (?b depends-on ?a)`` — binds consistently across
        its antecedents instead of matching each in isolation.
        """
        result = dict(bindings or {})
        for term, value in zip(
            (self.subject, self.predicate, self.object), fact.triple
        ):
            if is_variable(term):
                bound = result.get(term)
                if bound is not None and bound != value:
                    return None
                result[term] = value
            elif term != value:
                return None
        return result

    def bind(self, bindings: Mapping[str, str]) -> "Pattern":
        """Substitute what is known, leaving the rest as variables."""
        return Pattern(
            subject=bindings.get(self.subject, self.subject),
            predicate=bindings.get(self.predicate, self.predicate),
            object=bindings.get(self.object, self.object),
        )

    def as_fact(
        self,
        bindings: Mapping[str, str],
        **attributes: Any,
    ) -> Fact:
        """Ground the pattern into a fact. Raises if anything stayed unbound."""
        bound = self.bind(bindings)
        if not bound.is_ground:
            raise ReasoningError(
                f"cannot ground {self}: {', '.join(bound.variables)} unbound"
            )
        return Fact(
            subject=bound.subject, predicate=bound.predicate, object=bound.object,
            **attributes,
        )

    def __str__(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}".strip()


class FactBase:
    """Working memory, indexed for the lookups the engine actually performs.

    Three indexes because those are the three shapes a pattern takes: subject
    known, object known, or predicate alone. A linear scan is fine at a hundred
    facts and quadratic at a hundred thousand, and the crawler produces the
    latter.
    """

    def __init__(self, facts: Iterable[Fact] = ()) -> None:
        self._facts: dict[str, Fact] = {}
        self._by_subject: dict[str, set[str]] = {}
        self._by_object: dict[str, set[str]] = {}
        self._by_predicate: dict[str, set[str]] = {}
        for fact in facts:
            self.assert_fact(fact)

    def assert_fact(self, fact: Fact) -> Fact:
        """Add a fact, or keep whichever version is better justified.

        Re-asserting the same triple with weaker provenance must not downgrade
        what is already known — otherwise a guess arriving after an observation
        would quietly overwrite it.
        """
        existing = self._facts.get(fact.id)
        if existing is not None:
            if not fact.stronger_than(existing):
                return existing
            self._facts[fact.id] = fact
            return fact

        self._facts[fact.id] = fact
        self._by_subject.setdefault(fact.subject, set()).add(fact.id)
        self._by_object.setdefault(fact.object, set()).add(fact.id)
        self._by_predicate.setdefault(fact.predicate, set()).add(fact.id)
        return fact

    def extend(self, facts: Iterable[Fact]) -> int:
        before = len(self._facts)
        for fact in facts:
            self.assert_fact(fact)
        return len(self._facts) - before

    def retract(self, fact_id: str) -> bool:
        fact = self._facts.pop(fact_id, None)
        if fact is None:
            return False
        self._by_subject.get(fact.subject, set()).discard(fact_id)
        self._by_object.get(fact.object, set()).discard(fact_id)
        self._by_predicate.get(fact.predicate, set()).discard(fact_id)
        return True

    def match(self, pattern: Pattern) -> tuple[Fact, ...]:
        """Every fact the pattern unifies with, using the narrowest index."""
        candidates: set[str] | None = None

        if not is_variable(pattern.subject):
            candidates = set(self._by_subject.get(pattern.subject, ()))
        if pattern.object and not is_variable(pattern.object):
            by_object = set(self._by_object.get(pattern.object, ()))
            candidates = by_object if candidates is None else candidates & by_object
        if not is_variable(pattern.predicate):
            by_predicate = set(self._by_predicate.get(pattern.predicate, ()))
            candidates = by_predicate if candidates is None else candidates & by_predicate

        pool = (
            (self._facts[i] for i in candidates)
            if candidates is not None else self._facts.values()
        )
        return tuple(fact for fact in pool if pattern.match(fact) is not None)

    def holds(self, pattern: Pattern) -> bool:
        return bool(self.match(pattern))

    def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    def about(self, subject: str) -> tuple[Fact, ...]:
        return tuple(self._facts[i] for i in self._by_subject.get(subject, ()))

    def with_predicate(self, predicate: str) -> tuple[Fact, ...]:
        return tuple(self._facts[i] for i in self._by_predicate.get(predicate, ()))

    @property
    def facts(self) -> tuple[Fact, ...]:
        return tuple(self._facts.values())

    def ungrounded(self) -> tuple[Fact, ...]:
        """Inferences that cite nothing — the engine reasoning from thin air."""
        return tuple(fact for fact in self._facts.values() if not fact.grounded)

    def explain(self, fact_id: str, *, depth: int = 12) -> tuple[Fact, ...]:
        """Walk ``derived_from`` back to the observations underneath a claim.

        Returned root-first, which is the order an explanation reads in.
        """
        root = self._facts.get(fact_id)
        if root is None:
            return ()

        chain: list[Fact] = []
        seen: set[str] = set()
        frontier = [(root, 0)]
        while frontier:
            fact, level = frontier.pop(0)
            if fact.id in seen or level > depth:
                continue
            seen.add(fact.id)
            chain.append(fact)
            for parent_id in fact.derived_from:
                parent = self._facts.get(parent_id)
                if parent is not None:
                    frontier.append((parent, level + 1))
        return tuple(reversed(chain))

    def to_dict(self) -> dict[str, Any]:
        return {"facts": [fact.to_dict() for fact in self._facts.values()]}

    def __len__(self) -> int:
        return len(self._facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def __contains__(self, item: object) -> bool:
        if isinstance(item, Fact):
            return item.id in self._facts
        if isinstance(item, Pattern):
            return self.holds(item)
        return False

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<FactBase {len(self._facts)} facts>"


def combine_confidence(values: Sequence[float], *, factor: float = 1.0) -> float:
    """Confidence for a conclusion drawn from several premises.

    The **minimum**, scaled by the rule's own strength — not the product and not
    the mean. A chain is as strong as its weakest premise; multiplying would
    make a five-step derivation from certainties look like a guess, and
    averaging would let one bad premise hide behind four good ones.
    """
    if not values:
        return 0.0
    return round(min(min(values) * factor, INFERENCE_CEILING), 4)
