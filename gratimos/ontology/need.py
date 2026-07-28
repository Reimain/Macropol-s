"""A need — what the context requires, in a form that can be looked up.

The whole distillation loop turns on one property: **two ways of asking the same
question must produce the same key.** "I need to retry failed HTTP requests with
backoff" and "HTTP client retry, exponential" are one need, and if they hash
differently the knowledge base never hits and the agent is called every time.

So the signature is computed over *canonical concepts and constraints*, never
over the words. Free text goes in, concepts come out, and the words are kept
only so a human can read the need back.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .concepts import Ontology, base_ontology


class ConstraintKind(str, Enum):
    """The dimensions a need can constrain a candidate on."""

    LICENCE = "licence"          # what obligations we can absorb
    RUNTIME = "runtime"          # python>=3.11, node>=20
    ECOSYSTEM = "ecosystem"      # pypi, npm, cargo
    ASYNC = "async"              # sync or async — not interchangeable
    DEPENDENCY = "dependency"    # must not pull in X
    MATURITY = "maturity"        # released within, minimum age
    SIZE = "size"                # install footprint
    PLATFORM = "platform"        # linux, windows, wasm

    @property
    def is_hard(self) -> bool:
        """Whether violating it disqualifies rather than merely penalises.

        A licence we cannot absorb or a runtime we cannot run is fatal. A large
        install or a young project is a preference — ranking it down is right,
        excluding it silently is not.
        """
        return self in (
            ConstraintKind.LICENCE, ConstraintKind.RUNTIME,
            ConstraintKind.ECOSYSTEM, ConstraintKind.ASYNC,
            ConstraintKind.PLATFORM,
        )


@dataclass(frozen=True, slots=True)
class Constraint:
    """One requirement a candidate is measured against."""

    kind: ConstraintKind
    value: str
    negated: bool = False
    reason: str = ""

    @property
    def is_hard(self) -> bool:
        return self.kind.is_hard

    def key(self) -> str:
        """The canonical form that enters the signature."""
        return f"{'!' if self.negated else ''}{self.kind.value}={self.value.strip().lower()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "value": self.value,
            "negated": self.negated, "hard": self.is_hard, "reason": self.reason,
        }

    def __str__(self) -> str:
        return f"{'not ' if self.negated else ''}{self.kind.value}: {self.value}"


@dataclass(frozen=True, slots=True)
class Need:
    """What the context wants, keyed by meaning rather than by phrasing."""

    concepts: tuple[str, ...]
    constraints: tuple[Constraint, ...] = ()
    text: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.concepts:
            raise ValueError("a need must require at least one concept")

    @property
    def signature(self) -> str:
        """The cache key. Order-independent, phrasing-independent, stable.

        Sorted so that concept order cannot change the key, and built only from
        canonical concepts and constraint keys so that rewording the request
        cannot either. This is the value the whole loop is built on.
        """
        material = "|".join([
            "c:" + ",".join(sorted(self.concepts)),
            "k:" + ",".join(sorted(c.key() for c in self.constraints)),
        ])
        return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()

    @property
    def hard_constraints(self) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.is_hard)

    @property
    def soft_constraints(self) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if not c.is_hard)

    def constrained_on(self, kind: ConstraintKind) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.kind is kind)

    def urn(self) -> str:
        """A stable identifier, so a need can be a subject in the fact base."""
        return f"need:{self.signature[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "urn": self.urn(),
            "concepts": list(self.concepts),
            "constraints": [c.to_dict() for c in self.constraints],
            "text": self.text,
            "context": dict(self.context),
        }

    def __str__(self) -> str:
        return self.text or " + ".join(self.concepts)


#: Words that carry no concept and would only add noise to extraction.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "for", "from",
    "have", "how", "i", "if", "in", "is", "it", "me", "need", "of", "on", "or",
    "over", "so", "some", "something", "that", "the", "then", "there", "this",
    "to", "want", "was", "we", "what", "when", "which", "will", "with", "would",
})

_WORD = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def extract_concepts(text: str, ontology: Ontology | None = None) -> tuple[str, ...]:
    """Pull canonical concepts out of a free-text request.

    Deliberately conservative: it matches multi-word phrases first, then single
    terms, and returns **only concepts the ontology knows**. Inventing a concept
    from an unrecognised word would put a key in the cache that nothing else
    will ever produce again, which is worse than returning less.
    """
    lattice = ontology if ontology is not None else base_ontology()
    lowered = text.lower()
    found: list[str] = []

    # Longest phrases first, so "exponential backoff" is not consumed as
    # "backoff" with "exponential" left dangling.
    phrases = sorted(
        (
            phrase
            for concept in lattice
            for phrase in (concept.name, *concept.synonyms)
            if " " in phrase or "-" in phrase
        ),
        key=len,
        reverse=True,
    )
    remaining = lowered
    for phrase in phrases:
        spaced = phrase.replace("-", " ")
        for form in (phrase, spaced):
            if form and form in remaining:
                canonical = lattice.canonical(phrase)
                if canonical not in found:
                    found.append(canonical)
                remaining = remaining.replace(form, " ")
                break

    for word in _WORD.findall(remaining):
        if word in _STOPWORDS or len(word) < 3:
            continue
        concept = lattice.get(word)
        if concept is not None and concept.name not in found:
            found.append(concept.name)

    return tuple(found)


def need_from_text(
    text: str,
    *,
    ontology: Ontology | None = None,
    constraints: Iterable[Constraint] = (),
    context: Mapping[str, Any] | None = None,
) -> Need:
    """Build a need from a request in prose.

    Raises when nothing recognisable was found rather than returning an empty
    need — an empty need would match every candidate and cache under a key that
    means "anything", which is the worst possible entry to have.
    """
    concepts = extract_concepts(text, ontology)
    if not concepts:
        raise ValueError(
            f"no known concepts in {text!r}; extend the ontology or state them directly"
        )
    return Need(
        concepts=concepts,
        constraints=tuple(constraints),
        text=text.strip(),
        context=dict(context or {}),
    )
