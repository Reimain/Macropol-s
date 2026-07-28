"""The crawler — a need goes in, grounded facts about real packages come out.

This is the module that makes "crawl the open-source world" a bounded activity
rather than an open-ended one. The bound comes from the need: its concepts
supply the search terms, its ecosystem constraint decides which registries are
consulted at all, and its ceiling decides when to stop. A crawl with no need
attached is not supported, because it has no stopping condition and no way to
tell a relevant result from a popular one.

```
Need ─ concepts + synonyms ─→ terms
                                │
              ecosystem constraint selects sources
                                │
                     search / describe (polite, cached)
                                │
                  artifacts ─ upkeep enrichment ─→ artifacts
                                │
                          facts, grounded in what was read
                                │
                    Engine.learn() ─ forward chaining ─→ conclusions
```

Every fact the crawler emits is `OBSERVED` and cites the URL it was read from,
so the engine's conclusions terminate in something re-checkable. That is the
whole reason the crawler bothers with provenance: a reuse recommendation whose
chain ends in "the crawler said so" is not a recommendation, it is an opinion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from ..ontology.concepts import Ontology, base_ontology
from ..ontology.need import ConstraintKind, Need
from ..reason.engine import Engine, ForwardReport
from ..reason.facts import Fact, Provenance
from .fetch import Fetcher
from .policy import CrawlError, PolicyViolation
from .source import Artifact, Source, SourceRegistry

#: A release inside this window counts as `released-within year`, which is what
#: the `maintained-implies-viable` rule keys on.
YEAR_DAYS = 365.0

#: Thresholds for `widely-used`. Two different scales, because a download count
#: and a star count are not comparable and averaging them would produce a number
#: that means nothing on either.
POPULAR_DOWNLOADS = 1_000_000
POPULAR_STARS = 1_000

#: How many name forms one concept is allowed to expand into. Without a cap, a
#: concept with many synonyms turns a single need into a hundred requests.
MAX_TERMS_PER_CONCEPT = 4


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """What one crawl found, and what it could not see."""

    need: Need
    artifacts: tuple[Artifact, ...] = ()
    facts: tuple[Fact, ...] = ()
    terms: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    propagated: int = 0
    duration: float = 0.0

    @property
    def found(self) -> int:
        return len(self.artifacts)

    def by_purl(self) -> dict[str, Artifact]:
        return {artifact.purl: artifact for artifact in self.artifacts}

    def to_dict(self) -> dict[str, Any]:
        return {
            "need": self.need.to_dict(),
            "terms": list(self.terms),
            "sources": list(self.sources),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "facts": [f.to_dict() for f in self.facts],
            "gaps": list(self.gaps),
            "propagated": self.propagated,
            "duration": round(self.duration, 4),
        }

    def __str__(self) -> str:
        return f"{self.need} → {self.found} candidates, {len(self.facts)} facts"


class Crawler:
    """Turns a need into observed facts about packages that might answer it."""

    def __init__(
        self,
        sources: SourceRegistry | Iterable[Source] = (),
        *,
        ontology: Ontology | None = None,
        engine: Engine | None = None,
        fetcher: Fetcher | None = None,
        limit: int = 10,
    ) -> None:
        self.sources = (
            sources if isinstance(sources, SourceRegistry) else SourceRegistry(*sources)
        )
        self.ontology = ontology if ontology is not None else base_ontology()
        self.engine = engine
        self.fetcher = fetcher
        self.limit = limit
        self._crawls = 0

    # -- terms -----------------------------------------------------------

    def terms_for(self, need: Need) -> tuple[str, ...]:
        """Concepts plus their synonyms, canonical form first.

        Synonyms are included because registries index the words their authors
        used, not the words the ontology settled on: nobody publishes a package
        described as `retry-with-backoff`, they publish one described as
        "exponential backoff".
        """
        terms: list[str] = []
        for name in need.concepts:
            concept = self.ontology.get(name)
            candidates = [name] if concept is None else [
                concept.name, *concept.synonyms
            ]
            for term in candidates[:MAX_TERMS_PER_CONCEPT]:
                cleaned = term.strip().lower()
                if cleaned and cleaned not in terms:
                    terms.append(cleaned)
        return tuple(terms)

    def sources_for(self, need: Need) -> tuple[Source, ...]:
        """The registries this need permits.

        An ecosystem constraint is hard: a Python service cannot reuse an npm
        package, so crawling npm for it spends politeness budget on results that
        are disqualified before they are ranked. Ecosystems named by the need
        but unknown to the registry become gaps rather than silence.
        """
        declared = [
            c.value.strip().lower()
            for c in need.constrained_on(ConstraintKind.ECOSYSTEM)
            if not c.negated
        ]
        excluded = {
            c.value.strip().lower()
            for c in need.constrained_on(ConstraintKind.ECOSYSTEM)
            if c.negated
        }
        if not declared:
            return tuple(s for s in self.sources if s.ecosystem not in excluded)

        chosen: list[Source] = []
        for source in self.sources:
            if source.ecosystem in excluded:
                continue
            # A repository host is consulted under any ecosystem: it carries the
            # upkeep signal, which is ecosystem-independent.
            if source.ecosystem in declared or source.ecosystem == "github":
                chosen.append(source)
        return tuple(chosen)

    # -- the crawl -------------------------------------------------------

    def discover(self, need: Need, *, limit: int | None = None) -> CrawlResult:
        """Search every permitted source and return what was actually read."""
        started = time.monotonic()
        self._crawls += 1
        ceiling = self.limit if limit is None else limit
        terms = self.terms_for(need)
        chosen = self.sources_for(need)
        gaps: list[str] = []

        if not terms:
            gaps.append("the need carries no concepts the ontology recognises")
        if not chosen:
            gaps.append("no registered source serves the ecosystems this need permits")

        declared = {
            c.value.strip().lower()
            for c in need.constrained_on(ConstraintKind.ECOSYSTEM)
            if not c.negated
        }
        served = {s.ecosystem for s in chosen}
        for missing in sorted(declared - served):
            gaps.append(f"no source registered for ecosystem {missing!r}")

        collected: dict[tuple[str, str], Artifact] = {}
        consulted: list[str] = []
        for source in chosen:
            if not terms:
                break
            consulted.append(source.name)
            try:
                found = source.search(terms, limit=ceiling)
            except (CrawlError, PolicyViolation) as exc:
                # One registry being unreachable must not void the crawl. It
                # becomes a named gap, so an answer built on the remaining
                # sources says which one it could not see.
                gaps.append(f"{source.name} could not be searched: {exc}")
                continue
            for artifact in found:
                key = (artifact.ecosystem, artifact.name)
                collected[key] = (
                    collected[key].merge(artifact) if key in collected else artifact
                )

        artifacts = tuple(collected.values())
        artifacts = tuple(self.enrich(a) for a in artifacts)

        facts: list[Fact] = []
        for artifact in artifacts:
            facts.extend(self.facts_for(artifact, need))
        facts.extend(self.need_facts(need))
        facts.extend(self.lattice_facts(need))

        for artifact in artifacts:
            if not artifact.licence:
                gaps.append(f"{artifact.purl} publishes no licence metadata")

        propagated = 0
        if self.engine is not None and facts:
            report: ForwardReport = self.engine.learn(*facts)
            propagated = report.count

        return CrawlResult(
            need=need,
            artifacts=artifacts,
            facts=tuple(facts),
            terms=terms,
            sources=tuple(consulted),
            gaps=tuple(dict.fromkeys(gaps)),
            propagated=propagated,
            duration=time.monotonic() - started,
        )

    def enrich(self, artifact: Artifact) -> Artifact:
        """Fold repository upkeep into a package record.

        A registry knows what was published; only the forge knows whether the
        project is archived and when it was last touched. Those two facts decide
        more reuse questions than any amount of package metadata, so they are
        worth the extra request — but only when a repository link exists and a
        forge source is registered.
        """
        forge = self.sources.get("github")
        if forge is None or not artifact.repository or artifact.ecosystem == "github":
            return artifact
        slug = getattr(forge, "slug", lambda value: "")(artifact.repository)
        if not slug:
            return artifact
        try:
            repository = forge.describe(slug)
        except (CrawlError, PolicyViolation):
            return artifact
        if repository is None:
            return artifact
        return replace(
            artifact,
            stars=artifact.stars if artifact.stars is not None else repository.stars,
            deprecated=artifact.deprecated or repository.deprecated,
            licence=artifact.licence or repository.licence,
            released_at=(
                artifact.released_at if artifact.released_at is not None
                else repository.released_at
            ),
            raw={**dict(artifact.raw), "repository": repository.to_dict()},
        )

    # -- fact emission ---------------------------------------------------

    def facts_for(self, artifact: Artifact, need: Need) -> tuple[Fact, ...]:
        """Everything read about one artifact, as triples the engine can chain.

        Note what is *not* emitted: `provides`. The crawler never claims a
        package does what the need asks — it emits `named-like` and lets the
        weak heuristic rule draw that conclusion at 0.35, or an agent validate
        it at 0.97. Asserting capability from a package description would put a
        guess into the fact base wearing observation's confidence.
        """
        subject = artifact.purl
        source = artifact.origin.url if artifact.origin else artifact.ecosystem
        facts: list[Fact] = []

        def observe(predicate: str, obj: str, *, provenance: Provenance = Provenance.OBSERVED) -> None:
            facts.append(Fact(
                subject=subject, predicate=predicate, object=obj,
                provenance=provenance, source=source,
            ))

        if artifact.licence:
            observe("declares-licence", artifact.licence)
        observe("in-ecosystem", artifact.ecosystem)
        if artifact.version:
            observe("at-version", artifact.version)
        if artifact.repository:
            observe("hosted-at", artifact.repository)
        if artifact.requires_runtime:
            observe("requires-runtime", artifact.requires_runtime)

        for dependency in artifact.dependencies:
            name = dependency.split(";")[0].split("(")[0].strip()
            name = name.split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip()
            if name:
                observe("depends-on", name)

        if artifact.deprecated:
            observe("deprecated", "true")
            if bool(artifact.raw.get("archived")):
                observe("archived", "true")

        if artifact.yanked:
            observe("yanked", "true")

        age = artifact.age_days
        if age is not None and age <= YEAR_DAYS:
            observe("released-within", "year")

        if (
            (artifact.downloads is not None and artifact.downloads >= POPULAR_DOWNLOADS)
            or (artifact.stars is not None and artifact.stars >= POPULAR_STARS)
        ):
            observe("widely-used", "true")

        # The naming hint, kept deliberately weak. A concept whose canonical
        # name or synonym appears in the package's own text is a reason to look,
        # not a reason to believe.
        text = artifact.text().lower()
        for concept_name in need.concepts:
            concept = self.ontology.get(concept_name)
            words = [concept_name] if concept is None else [concept.name, *concept.synonyms]
            for word in words:
                token = word.lower()
                if token and (token in text or token.replace("-", " ") in text):
                    observe("named-like", concept_name)
                    break

        return tuple(facts)

    def need_facts(self, need: Need) -> tuple[Fact, ...]:
        """The need itself as facts, so rules can join packages against it."""
        return tuple(
            Fact(
                subject=need.urn(), predicate="requires", object=concept,
                provenance=Provenance.DECLARED, source="need",
            )
            for concept in need.concepts
        )

    def lattice_facts(self, need: Need) -> tuple[Fact, ...]:
        """`is-a` edges for the part of the lattice this need touches.

        Only the ancestors of the required concepts, not the whole ontology.
        Loading 43 concepts' worth of subsumption for a need about two of them
        would make every forward pass do work no conclusion depends on.
        """
        facts: list[Fact] = []
        seen: set[tuple[str, str]] = set()
        for name in need.concepts:
            concept = self.ontology.get(name)
            if concept is None:
                continue
            frontier = [concept.name]
            while frontier:
                current = frontier.pop()
                node = self.ontology.get(current)
                if node is None:
                    continue
                for parent in node.broader:
                    if (current, parent) in seen:
                        continue
                    seen.add((current, parent))
                    facts.append(Fact(
                        subject=current, predicate="is-a", object=parent,
                        provenance=Provenance.DECLARED, source="ontology",
                    ))
                    frontier.append(parent)
        return tuple(facts)

    # -- reporting -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "crawls": self._crawls,
            "sources": sorted(s.name for s in self.sources),
            "ecosystems": list(self.sources.ecosystems()),
            "limit": self.limit,
            "fetch": self.fetcher.status()["fetch"] if self.fetcher else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Crawler {len(self.sources)} sources, {self._crawls} crawls>"
