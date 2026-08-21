"""A candidate — one crawled artifact, held against one need.

An `Artifact` is what a registry said. A `Candidate` is that artifact *in the
context of a question*, which is where the ranking signals live: how closely its
concepts match, what the reasoning engine concluded about it, how fresh it is,
how much it drags in.

The signals are computed once, at construction, and carried as data. That is
not micro-optimisation — it is so that the score a candidate received is
reconstructable later from the candidate itself, without re-running a crawl.
A ranking you cannot re-explain a week afterwards is a ranking nobody will
trust the second time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..crawl.source import Artifact
from ..ontology.concepts import Ontology, base_ontology
from ..ontology.need import Need
from ..reason.facts import Fact

#: How many lattice hops of generality still counts as a match at all. Beyond
#: this the candidate answers a much broader question than the one asked, and
#: offering it is noise: everything is, eventually, "software".
MAX_GENERALITY = 3


@dataclass(frozen=True, slots=True)
class ConceptMatch:
    """One required concept, and what the candidate offers against it."""

    required: str
    provided: str = ""
    distance: int = -1
    confidence: float = 0.0

    @property
    def matched(self) -> bool:
        return self.distance >= 0 and self.distance <= MAX_GENERALITY

    @property
    def exact(self) -> bool:
        return self.distance == 0

    @property
    def fit(self) -> float:
        """1.0 for an exact match, decaying with generality, 0 for a miss.

        Decay rather than a cliff: a candidate one hop too general is worse than
        an exact match but far better than nothing, and a step function would
        make the ranking jump on a lattice edit.
        """
        if not self.matched:
            return 0.0
        return round(1.0 / (1.0 + self.distance), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required, "provided": self.provided,
            "distance": self.distance, "confidence": round(self.confidence, 4),
            "fit": self.fit,
        }

    def __str__(self) -> str:
        if not self.matched:
            return f"{self.required}: not offered"
        via = "exactly" if self.exact else f"via {self.provided} ({self.distance} hops)"
        return f"{self.required}: {via}"


@dataclass(frozen=True, slots=True)
class Candidate:
    """An artifact considered as an answer to a specific need."""

    artifact: Artifact
    need: Need
    matches: tuple[ConceptMatch, ...] = ()
    facts: tuple[Fact, ...] = ()
    notes: tuple[str, ...] = ()

    # -- artifact passthrough, so callers do not reach through two dots ----

    @property
    def purl(self) -> str:
        return self.artifact.purl

    @property
    def name(self) -> str:
        return self.artifact.name

    @property
    def ecosystem(self) -> str:
        return self.artifact.ecosystem

    @property
    def licence(self) -> str:
        return self.artifact.licence

    # -- derived signals ---------------------------------------------------

    @property
    def coverage(self) -> float:
        """Fraction of the need's concepts this candidate offers at all.

        Kept separate from fit: a package that answers one of three required
        concepts perfectly is not a better match than one that answers all
        three adequately, and a single averaged number cannot say that.
        """
        if not self.matches:
            return 0.0
        return round(sum(1 for m in self.matches if m.matched) / len(self.matches), 4)

    @property
    def fit(self) -> float:
        """Mean fit across required concepts, misses included as zero."""
        if not self.matches:
            return 0.0
        return round(sum(m.fit for m in self.matches) / len(self.matches), 4)

    @property
    def complete(self) -> bool:
        return bool(self.matches) and all(m.matched for m in self.matches)

    @property
    def evidence(self) -> float:
        """Best confidence behind any concept claim.

        This is what separates a validated candidate from a guessed one: both
        may `provide` the concept, but one was checked by an agent at 0.97 and
        the other was inferred from its name at 0.35.
        """
        confidences = [m.confidence for m in self.matches if m.matched]
        return round(max(confidences), 4) if confidences else 0.0

    @property
    def dependency_count(self) -> int:
        return len(self.artifact.dependencies)

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        """Anything the reasoning engine concluded outright blocks reuse."""
        return tuple(sorted({
            fact.object for fact in self.facts
            if fact.subject == self.purl and fact.predicate == "blocked"
        }))

    def fact(self, predicate: str) -> Fact | None:
        for candidate in self.facts:
            if candidate.subject == self.purl and candidate.predicate == predicate:
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.purl,
            "artifact": self.artifact.to_dict(),
            "need": self.need.urn(),
            "matches": [m.to_dict() for m in self.matches],
            "coverage": self.coverage,
            "fit": self.fit,
            "evidence": self.evidence,
            "complete": self.complete,
            "blocked": list(self.blocked_reasons),
            "notes": list(self.notes),
        }

    def __str__(self) -> str:
        return f"{self.purl} (fit {self.fit:.2f}, covers {self.coverage:.0%})"


def build_candidate(
    artifact: Artifact,
    need: Need,
    *,
    ontology: Ontology | None = None,
    facts: Iterable[Fact] = (),
) -> Candidate:
    """Hold one artifact against one need, resolving every required concept.

    Facts are consulted before the lattice: what the engine actually concluded
    about this package beats what its name suggests. Only when no fact mentions
    a concept does the fallback apply, and the fallback is deliberately weak —
    an unproven keyword match carries the confidence of a keyword match.
    """
    lattice = ontology if ontology is not None else base_ontology()
    relevant = tuple(f for f in facts if f.subject == artifact.purl)

    provided: dict[str, float] = {}
    for fact in relevant:
        if fact.predicate == "provides":
            concept = fact.object
            provided[concept] = max(provided.get(concept, 0.0), fact.confidence)

    matches: list[ConceptMatch] = []
    for required in need.concepts:
        best = ConceptMatch(required=required)
        for offered, confidence in provided.items():
            distance = lattice.distance(offered, required)
            if distance < 0 or distance > MAX_GENERALITY:
                continue
            # Nearer wins; on a tie, better-evidenced wins. Ordering the other
            # way would let a loosely-related but well-evidenced concept beat an
            # exact match, which reads as the engine ignoring the question.
            if not best.matched or distance < best.distance or (
                distance == best.distance and confidence > best.confidence
            ):
                best = ConceptMatch(
                    required=required, provided=offered,
                    distance=distance, confidence=confidence,
                )
        matches.append(best)

    notes: list[str] = []
    if not any(f.predicate == "provides" for f in relevant):
        notes.append(
            "no capability claim exists for this package; matching rests on "
            "registry text alone"
        )

    return Candidate(
        artifact=artifact, need=need, matches=tuple(matches),
        facts=tuple(relevant), notes=tuple(notes),
    )
