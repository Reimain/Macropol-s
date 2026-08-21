"""The assessor — the whole path, from a sentence to a defensible recommendation.

```
need ─→ crawl (polite, cached) ─→ observed facts ─→ forward chaining
                                                          │
                          distillation: validate, or recall a past validation
                                                          │
                            candidates ─→ gate (hard) ─→ rank (soft)
                                                          │
                                          recommendation + alternatives
                                          + refusals, each with a reason
                                          + gaps, each named
```

Two design positions run through it.

**Validation happens before ranking, not after.** It is cheaper to rank first
and validate only the winner, and it is wrong: the ranking is driven by evidence
quality, so validating afterwards means the ranking that chose what to validate
was made on guesses. Validating first — through the distillation loop, so the
agent is called at most once per distinct need and never again for a repeat —
means the ordering is built on checked claims.

**Refusals are part of the answer.** `Assessment.rejected` carries every
candidate the gate excluded with its blockers, and `gaps` carries everything
that could not be established. An engineer who asked "what can I reuse here?"
needs to know that the obvious library was excluded for its licence; a
shortlist that silently omits it looks like the tool never found it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..crawl.crawler import Crawler, CrawlResult
from ..crawl.source import Artifact
from ..distill.loop import DistillationLoop, Judgement, Route, Turn
from ..ontology.concepts import Ontology, base_ontology
from ..ontology.need import Need
from ..reason.engine import Engine
from ..reason.facts import Fact, FactBase, Provenance
from ..reason.rules import reuse_rules
from .candidate import Candidate, build_candidate
from .gate import Gate, GateResult
from .rank import Ranker, Score


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One candidate offered, with everything behind the offer."""

    candidate: Candidate
    score: Score
    gate: GateResult

    @property
    def purl(self) -> str:
        return self.candidate.purl

    @property
    def confident(self) -> bool:
        """Whether this rests on a checked claim rather than a naming guess."""
        return self.candidate.evidence >= 0.60

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.purl,
            "score": self.score.to_dict(),
            "candidate": self.candidate.to_dict(),
            "gate": self.gate.to_dict(),
            "confident": self.confident,
        }

    def __str__(self) -> str:
        return f"{self.purl} — {self.score}"


@dataclass(frozen=True, slots=True)
class Assessment:
    """The answer to "what can I reuse for this?", refusals and gaps included."""

    need: Need
    ranked: tuple[Recommendation, ...] = ()
    rejected: tuple[GateResult, ...] = ()
    gaps: tuple[str, ...] = ()
    crawl: CrawlResult | None = None
    turn: Turn | None = None
    duration: float = 0.0

    @property
    def best(self) -> Recommendation | None:
        return self.ranked[0] if self.ranked else None

    @property
    def alternatives(self) -> tuple[Recommendation, ...]:
        return self.ranked[1:]

    @property
    def agent_called(self) -> bool:
        return bool(self.turn and self.turn.agent_called)

    @property
    def answered(self) -> bool:
        return bool(self.ranked)

    @property
    def reasoning(self) -> str:
        """Why this answer, in the order the decisions were made.

        Rendered rather than logged, because the reasoning path is part of the
        deliverable: an assessment nobody can audit is an assertion.
        """
        lines: list[str] = [f"need: {self.need}"]
        if self.crawl is not None:
            lines.append(
                f"crawled {', '.join(self.crawl.sources) or 'nothing'} for "
                f"{', '.join(self.crawl.terms)} → {self.crawl.found} artifacts"
            )
        if self.turn is not None:
            lines.append(f"validation: {self.turn.route.value} — {self.turn.explanation}")
        if self.rejected:
            lines.append(f"refused {len(self.rejected)}:")
            lines.extend(f"  - {result}" for result in self.rejected)
        if self.ranked:
            lines.append(f"admitted {len(self.ranked)}, best first:")
            lines.extend(
                f"  {index}. {rec.purl} → {rec.score.explanation}"
                for index, rec in enumerate(self.ranked, start=1)
            )
        else:
            lines.append("nothing cleared the gate")
        if self.gaps:
            lines.append("gaps:")
            lines.extend(f"  ! {gap}" for gap in self.gaps)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "need": self.need.to_dict(),
            "best": self.best.to_dict() if self.best else None,
            "ranked": [r.to_dict() for r in self.ranked],
            "rejected": [r.to_dict() for r in self.rejected],
            "gaps": list(self.gaps),
            "agent_called": self.agent_called,
            "crawl": self.crawl.to_dict() if self.crawl else None,
            "turn": self.turn.to_dict() if self.turn else None,
            "duration": round(self.duration, 4),
            "reasoning": self.reasoning,
        }

    def __str__(self) -> str:
        if not self.ranked:
            return f"{self.need} → nothing reusable ({len(self.rejected)} refused)"
        return f"{self.need} → {self.best} (+{len(self.alternatives)} alternatives)"


class ReuseAssessor:
    """Crawl, validate, gate, rank — the four steps, composed once."""

    def __init__(
        self,
        *,
        crawler: Crawler | None = None,
        gate: Gate | None = None,
        ranker: Ranker | None = None,
        engine: Engine | None = None,
        loop: DistillationLoop | None = None,
        ontology: Ontology | None = None,
        limit: int = 5,
    ) -> None:
        self.ontology = ontology if ontology is not None else base_ontology()
        self.engine = engine if engine is not None else (
            crawler.engine if crawler is not None and crawler.engine is not None
            else Engine(FactBase(), reuse_rules(self.ontology))
        )
        self.crawler = crawler
        self.gate = gate if gate is not None else Gate()
        self.ranker = ranker if ranker is not None else Ranker()
        self.loop = loop
        self.limit = limit

    # -- the assessment ---------------------------------------------------

    def assess(
        self,
        need: Need,
        artifacts: Iterable[Artifact] | None = None,
        *,
        limit: int | None = None,
        force_validation: bool = False,
    ) -> Assessment:
        """Answer one need. Never raises on a missing source; it reports a gap."""
        started = time.monotonic()
        ceiling = self.limit if limit is None else limit
        gaps: list[str] = []

        crawl: CrawlResult | None = None
        if artifacts is None:
            if self.crawler is None:
                gaps.append(
                    "no crawler is configured and no artifacts were supplied, so "
                    "nothing could be considered"
                )
                found: tuple[Artifact, ...] = ()
            else:
                crawl = self.crawler.discover(need)
                found = crawl.artifacts
                gaps.extend(crawl.gaps)
        else:
            found = tuple(artifacts)

        turn = self._validate(need, found, force=force_validation)
        if turn is not None and turn.route is Route.UNAVAILABLE:
            gaps.append(
                "no validator is configured, so capability claims rest on registry "
                "text rather than on a checked judgement"
            )

        known = self.engine.facts.facts
        candidates = [
            build_candidate(artifact, need, ontology=self.ontology, facts=known)
            for artifact in found
        ]

        admitted, refused = self.gate.filter(candidates)
        for result in admitted:
            gaps.extend(result.unverified)
            gaps.extend(result.warnings)

        obligations = {r.candidate.purl: r.obligation for r in admitted}
        by_purl = {r.candidate.purl: r for r in admitted}
        ranked = self.ranker.rank(
            (result.candidate for result in admitted), obligations=obligations
        )

        recommendations = tuple(
            Recommendation(candidate=candidate, score=score, gate=by_purl[candidate.purl])
            for candidate, score in ranked[:ceiling]
        )

        for recommendation in recommendations:
            if not recommendation.confident:
                gaps.append(
                    f"{recommendation.purl} matches on naming alone "
                    f"({recommendation.candidate.evidence:.2f}); nothing has confirmed "
                    f"it does what the need asks"
                )

        if found and not admitted:
            gaps.append(
                f"all {len(found)} candidates were refused; the need may be "
                f"over-constrained, or the ecosystem may genuinely lack an answer"
            )

        return Assessment(
            need=need,
            ranked=recommendations,
            rejected=refused,
            gaps=tuple(dict.fromkeys(gaps)),
            crawl=crawl,
            turn=turn,
            duration=time.monotonic() - started,
        )

    # -- validation --------------------------------------------------------

    def _validate(
        self,
        need: Need,
        artifacts: Sequence[Artifact],
        *,
        force: bool,
    ) -> Turn | None:
        """Ask the agent — or the memory of the last time it was asked.

        The candidate list is handed to the validator through the need's
        context, so an agent judging "which of these actually retries with
        backoff?" is looking at the same artifacts that will be ranked. Without
        that the agent answers in the abstract and its judgement cannot be
        matched to a purl.
        """
        if self.loop is None:
            return None

        enriched = need
        if artifacts:
            enriched = Need(
                concepts=need.concepts,
                constraints=need.constraints,
                text=need.text,
                context={
                    **dict(need.context),
                    "candidates": [artifact.to_dict() for artifact in artifacts],
                },
            )
            # The signature is computed from concepts and constraints only, so
            # attaching candidates cannot change the cache key. Asserted here
            # because if that ever stopped being true, every crawl would produce
            # a fresh key and the loop would call the agent every single time.
            if enriched.signature != need.signature:  # pragma: no cover - invariant
                raise AssertionError(
                    "attaching candidates changed the need signature; the "
                    "distillation cache would never hit again"
                )

        return self.loop.resolve(enriched, force=force)

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "crawler": self.crawler.status() if self.crawler else None,
            "gate": self.gate.status(),
            "engine": self.engine.status(),
            "loop": self.loop.status() if self.loop else None,
            "limit": self.limit,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<ReuseAssessor crawler={'yes' if self.crawler else 'no'}, "
            f"validator={'yes' if self.loop and self.loop.validator else 'no'}>"
        )
