"""The concept lattice — what code *does*, independent of what it is called.

Keyword search fails at reuse for one reason: `tenacity`, `backoff` and
`urllib3.Retry` share almost no vocabulary and solve the same problem. Matching
on names finds none of the substitutes. Matching on **concepts** finds all three.

So a concept is a node in a subsumption lattice, and the lattice is what makes
the reasoning semantic rather than lexical:

``retry-with-jitter`` **is-a** ``retry-with-backoff`` **is-a** ``retry``

A candidate providing the narrow concept satisfies a need for the broad one, and
never the reverse — asking for jitter and being handed plain retry is a wrong
answer that a flat tag set would happily give you.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import OntologyError


class Domain(str, Enum):
    """Broad areas a concept belongs to. Used to prune the search early."""

    NETWORKING = "networking"
    PERSISTENCE = "persistence"
    SERIALISATION = "serialisation"
    CONCURRENCY = "concurrency"
    RESILIENCE = "resilience"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    VALIDATION = "validation"
    TRANSFORMATION = "transformation"
    INTERFACE = "interface"
    TESTING = "testing"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class Concept:
    """One capability the ontology can talk about.

    ``broader`` names the concepts this one specialises. A concept with no
    broader parent is a root of its domain.
    """

    name: str
    domain: Domain = Domain.GENERIC
    broader: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise OntologyError("a concept must be named")

    @property
    def is_root(self) -> bool:
        return not self.broader

    def __str__(self) -> str:
        return self.name


class Ontology:
    """The concept lattice, and the subsumption reasoning over it.

    Deliberately a lattice rather than a tree: `async-http-client` is both an
    `http-client` and an `async-io` thing, and forcing a single parent would
    lose one of those facts.
    """

    def __init__(self, concepts: Iterable[Concept] = ()) -> None:
        self._concepts: dict[str, Concept] = {}
        self._synonyms: dict[str, str] = {}
        self._narrower: dict[str, set[str]] = {}
        for concept in concepts:
            self.add(concept)

    # -- construction ----------------------------------------------------

    def add(self, concept: Concept) -> Concept:
        """Register a concept and index it both ways."""
        if concept.name in self._concepts:
            raise OntologyError(f"concept {concept.name!r} is already defined")

        self._concepts[concept.name] = concept
        self._narrower.setdefault(concept.name, set())

        for alias in concept.synonyms:
            key = _normalise(alias)
            if key in self._synonyms and self._synonyms[key] != concept.name:
                raise OntologyError(
                    f"synonym {alias!r} already points at {self._synonyms[key]!r}"
                )
            self._synonyms[key] = concept.name

        for parent in concept.broader:
            self._narrower.setdefault(parent, set()).add(concept.name)

        return concept

    def define(self, name: str, *broader: str, **attributes: Any) -> Concept:
        """Terser `add`, for building a lattice literally."""
        return self.add(Concept(name=name, broader=tuple(broader), **attributes))

    def validate(self) -> None:
        """Check every parent exists and no cycle was introduced.

        A cycle would make subsumption non-terminating, and a dangling parent
        would silently drop a whole branch of the lattice from every query.
        """
        for concept in self._concepts.values():
            for parent in concept.broader:
                if parent not in self._concepts:
                    raise OntologyError(
                        f"{concept.name!r} declares unknown parent {parent!r}"
                    )

        for name in self._concepts:
            self._ancestors(name, guard=set())

    # -- lookup ----------------------------------------------------------

    def get(self, name: str) -> Concept | None:
        """Resolve a name or a synonym to its concept."""
        if name in self._concepts:
            return self._concepts[name]
        canonical = self._synonyms.get(_normalise(name))
        return self._concepts.get(canonical) if canonical else None

    def require(self, name: str) -> Concept:
        concept = self.get(name)
        if concept is None:
            raise OntologyError(f"unknown concept {name!r}")
        return concept

    def canonical(self, name: str) -> str:
        """The canonical name for a term, or the term itself when unknown.

        Unknown terms pass through rather than raising: a need may legitimately
        mention something the lattice has not learned yet, and losing it would
        be worse than carrying it unresolved.
        """
        concept = self.get(name)
        return concept.name if concept else _normalise(name)

    # -- subsumption -----------------------------------------------------

    def ancestors(self, name: str) -> frozenset[str]:
        """Every concept this one specialises, transitively."""
        return frozenset(self._ancestors(self.canonical(name), guard=set()))

    def _ancestors(self, name: str, *, guard: set[str]) -> set[str]:
        if name in guard:
            raise OntologyError(f"cycle in the concept lattice at {name!r}")
        concept = self._concepts.get(name)
        if concept is None:
            return set()

        guard.add(name)
        found: set[str] = set()
        for parent in concept.broader:
            found.add(parent)
            found |= self._ancestors(parent, guard=guard)
        guard.discard(name)
        return found

    def descendants(self, name: str) -> frozenset[str]:
        """Every concept that specialises this one, transitively."""
        canonical = self.canonical(name)
        found: set[str] = set()
        frontier = [canonical]
        while frontier:
            current = frontier.pop()
            for child in self._narrower.get(current, ()):
                if child not in found:
                    found.add(child)
                    frontier.append(child)
        return frozenset(found)

    def satisfies(self, provided: str, required: str) -> bool:
        """Whether something providing ``provided`` meets a need for ``required``.

        Directional on purpose. `retry-with-jitter` satisfies `retry`; `retry`
        does not satisfy `retry-with-jitter`. Treating the lattice as symmetric
        is how a reuse engine ends up recommending something that does less than
        was asked for.
        """
        left, right = self.canonical(provided), self.canonical(required)
        return left == right or right in self.ancestors(left)

    def closure(self, names: Iterable[str]) -> frozenset[str]:
        """A set of concepts plus everything they imply.

        This is what gets stored on a candidate: matching against the closure
        means a lattice edge added later cannot silently change what an already
        indexed candidate is found by.
        """
        found: set[str] = set()
        for name in names:
            canonical = self.canonical(name)
            found.add(canonical)
            found |= self.ancestors(canonical)
        return frozenset(found)

    def distance(self, provided: str, required: str) -> int:
        """Lattice hops from the provided concept up to the required one.

        Zero is an exact match. Larger means the candidate is more general than
        what was asked for — usable, but a worse fit, and ranking should say so.
        Returns -1 when it does not satisfy the need at all.
        """
        left, right = self.canonical(provided), self.canonical(required)
        if left == right:
            return 0

        frontier = [(left, 0)]
        seen = {left}
        while frontier:
            current, depth = frontier.pop(0)
            concept = self._concepts.get(current)
            if concept is None:
                continue
            for parent in concept.broader:
                if parent == right:
                    return depth + 1
                if parent not in seen:
                    seen.add(parent)
                    frontier.append((parent, depth + 1))
        return -1

    # -- introspection ---------------------------------------------------

    def in_domain(self, domain: Domain) -> tuple[Concept, ...]:
        return tuple(c for c in self._concepts.values() if c.domain is domain)

    def roots(self) -> tuple[Concept, ...]:
        return tuple(c for c in self._concepts.values() if c.is_root)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concepts": [
                {
                    "name": c.name, "domain": c.domain.value,
                    "broader": list(c.broader), "synonyms": list(c.synonyms),
                }
                for c in self._concepts.values()
            ]
        }

    def __len__(self) -> int:
        return len(self._concepts)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def __iter__(self) -> Iterator[Concept]:
        return iter(self._concepts.values())

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Ontology {len(self._concepts)} concepts>"


def _normalise(term: str) -> str:
    """Fold a term to its comparison form.

    `HTTP Client`, `http_client` and `http-client` are one term. Doing this at
    the boundary means every lookup path agrees, rather than each caller
    inventing its own spelling rules.
    """
    return "-".join(
        part for part in term.strip().lower().replace("_", "-").replace(" ", "-").split("-")
        if part
    )


def base_ontology() -> Ontology:
    """The starting lattice.

    Small on purpose. It covers the concepts a reuse query actually reaches for,
    and the crawler extends it as it learns — a large hand-written ontology
    would be mostly wrong and entirely unmaintained.
    """
    ontology = Ontology()
    d = Domain

    # networking
    ontology.define("network-io", domain=d.NETWORKING)
    ontology.define("http-client", "network-io", domain=d.NETWORKING,
                    synonyms=("http", "rest client", "web client"))
    ontology.define("async-http-client", "http-client", "async-io", domain=d.NETWORKING)
    ontology.define("websocket-client", "network-io", domain=d.NETWORKING)
    ontology.define("grpc-client", "network-io", domain=d.NETWORKING)

    # concurrency
    ontology.define("concurrency", domain=d.CONCURRENCY)
    ontology.define("async-io", "concurrency", domain=d.CONCURRENCY,
                    synonyms=("asyncio", "async"))
    ontology.define("thread-pool", "concurrency", domain=d.CONCURRENCY)
    ontology.define("task-queue", "concurrency", domain=d.CONCURRENCY,
                    synonyms=("job queue", "worker queue"))

    # resilience — the running example, and the case names get most wrong
    ontology.define("resilience", domain=d.RESILIENCE)
    ontology.define("retry", "resilience", domain=d.RESILIENCE,
                    synonyms=("retries", "retrying"))
    ontology.define("retry-with-backoff", "retry", domain=d.RESILIENCE,
                    synonyms=("backoff", "exponential backoff"))
    ontology.define("retry-with-jitter", "retry-with-backoff", domain=d.RESILIENCE,
                    synonyms=("jitter",))
    ontology.define("circuit-breaker", "resilience", domain=d.RESILIENCE)
    ontology.define("rate-limit", "resilience", domain=d.RESILIENCE,
                    synonyms=("throttle", "throttling"))
    ontology.define("timeout", "resilience", domain=d.RESILIENCE)

    # serialisation and validation
    ontology.define("serialisation", domain=d.SERIALISATION,
                    synonyms=("serialization", "encoding"))
    ontology.define("json-codec", "serialisation", domain=d.SERIALISATION)
    ontology.define("yaml-codec", "serialisation", domain=d.SERIALISATION)
    ontology.define("schema-validation", domain=d.VALIDATION,
                    synonyms=("validation", "data validation"))
    ontology.define("type-coercion", "schema-validation", domain=d.VALIDATION)

    # persistence
    ontology.define("persistence", domain=d.PERSISTENCE, synonyms=("storage",))
    ontology.define("sql-access", "persistence", domain=d.PERSISTENCE)
    ontology.define("orm", "sql-access", domain=d.PERSISTENCE)
    ontology.define("migration", "persistence", domain=d.PERSISTENCE)
    ontology.define("object-storage", "persistence", domain=d.PERSISTENCE,
                    synonyms=("blob storage",))
    ontology.define("caching", "persistence", domain=d.PERSISTENCE, synonyms=("cache",))

    # security
    ontology.define("security", domain=d.SECURITY)
    ontology.define("authentication", "security", domain=d.SECURITY, synonyms=("auth",))
    ontology.define("oauth", "authentication", domain=d.SECURITY)
    ontology.define("jwt", "authentication", domain=d.SECURITY)
    ontology.define("cryptography", "security", domain=d.SECURITY, synonyms=("crypto",))
    ontology.define("secret-management", "security", domain=d.SECURITY)

    # observability
    ontology.define("observability", domain=d.OBSERVABILITY)
    ontology.define("structured-logging", "observability", domain=d.OBSERVABILITY,
                    synonyms=("logging",))
    ontology.define("metrics", "observability", domain=d.OBSERVABILITY)
    ontology.define("tracing", "observability", domain=d.OBSERVABILITY)

    # interface and testing
    ontology.define("interface", domain=d.INTERFACE)
    ontology.define("cli-parsing", "interface", domain=d.INTERFACE, synonyms=("cli",))
    ontology.define("web-framework", "interface", domain=d.INTERFACE)
    ontology.define("testing", domain=d.TESTING)
    ontology.define("property-testing", "testing", domain=d.TESTING)
    ontology.define("mocking", "testing", domain=d.TESTING)

    ontology.validate()
    return ontology
