"""Ranking — a number, and the arithmetic that produced it, always together.

Every candidate that clears the gate gets a score built from named components,
each carrying its weight, its raw measurement and a sentence explaining it. The
total is the weighted mean of the components that could be measured, and
components with no data are **dropped rather than zeroed**, so a package that
does not report downloads is not penalised for the reporting gap.

The weights encode a position, and it is worth stating rather than burying:

| Component | Weight | Why |
|---|---|---|
| fit | 0.30 | answering the question is the point |
| evidence | 0.25 | *how well we know* it answers is nearly as important |
| maintenance | 0.20 | an unmaintained library is a future migration |
| licence cost | 0.10 | permissive is cheaper to absorb than copyleft |
| footprint | 0.08 | every transitive dependency is someone else's risk |
| popularity | 0.07 | last, and deliberately so |

Popularity is the weakest input on purpose. It is the easiest signal to obtain
and the least related to whether a package does what you need — ranking by it
recommends whatever is fashionable, which is a search engine, not an
architecture tool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .candidate import Candidate

#: Weights, summing to 1.0 when every component is measurable.
WEIGHTS: dict[str, float] = {
    "fit": 0.30,
    "evidence": 0.25,
    "maintenance": 0.20,
    "licence": 0.10,
    "footprint": 0.08,
    "popularity": 0.07,
}

#: Obligation classes, cheapest to absorb first. An unknown licence never gets
#: here — the gate blocks it — so the scale starts at "known and permissive".
OBLIGATION_COST: dict[str, float] = {
    "public-domain": 1.00,
    "permissive": 0.95,
    "weak-copyleft": 0.65,
    "strong-copyleft": 0.35,
    "network-copyleft": 0.20,
    "unknown": 0.0,
}

#: A release within this many days scores full marks for maintenance; the score
#: decays linearly to zero at `STALE_DAYS`.
FRESH_DAYS = 180.0
STALE_DAYS = 1095.0

#: Direct dependency counts at which footprint scores 1.0 and 0.0.
LEAN_DEPENDENCIES = 2
HEAVY_DEPENDENCIES = 40


@dataclass(frozen=True, slots=True)
class Component:
    """One measured dimension of a score."""

    name: str
    value: float
    weight: float
    reason: str
    raw: Any = None

    @property
    def contribution(self) -> float:
        return round(self.value * self.weight, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "value": round(self.value, 4),
            "weight": self.weight, "contribution": self.contribution,
            "reason": self.reason, "raw": self.raw,
        }

    def __str__(self) -> str:
        return f"{self.name} {self.value:.2f}×{self.weight:.2f} — {self.reason}"


@dataclass(frozen=True, slots=True)
class Score:
    """A total that can be re-derived from the components printed beside it."""

    total: float
    components: tuple[Component, ...] = ()
    unmeasured: tuple[str, ...] = ()

    def component(self, name: str) -> Component | None:
        for component in self.components:
            if component.name == name:
                return component
        return None

    @property
    def explanation(self) -> str:
        """The score as prose, in descending order of what drove it."""
        ordered = sorted(self.components, key=lambda c: c.contribution, reverse=True)
        parts = [str(component) for component in ordered]
        if self.unmeasured:
            parts.append(f"not measurable: {', '.join(self.unmeasured)}")
        return f"{self.total:.2f} = " + "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "components": [c.to_dict() for c in self.components],
            "unmeasured": list(self.unmeasured),
            "explanation": self.explanation,
        }

    def __float__(self) -> float:
        return self.total

    def __str__(self) -> str:
        return f"{self.total:.2f}"


class Ranker:
    """Scores admitted candidates. Weights are injectable but must be complete."""

    def __init__(
        self,
        weights: Mapping[str, float] | None = None,
        *,
        now: float | None = None,
    ) -> None:
        self.weights = dict(WEIGHTS)
        if weights:
            unknown = set(weights) - set(WEIGHTS)
            if unknown:
                raise ValueError(f"unknown ranking components: {sorted(unknown)}")
            self.weights.update(weights)
        self._now = now

    def _clock(self) -> float:
        return time.time() if self._now is None else self._now

    # -- scoring ----------------------------------------------------------

    def score(self, candidate: Candidate, *, obligation: str = "unknown") -> Score:
        components: list[Component] = []
        unmeasured: list[str] = []

        components.append(self._fit(candidate))
        components.append(self._evidence(candidate))

        maintenance = self._maintenance(candidate)
        if maintenance is None:
            unmeasured.append("maintenance (no release date published)")
        else:
            components.append(maintenance)

        components.append(self._licence(obligation))

        footprint = self._footprint(candidate)
        if footprint is None:
            unmeasured.append("footprint (no dependency list published)")
        else:
            components.append(footprint)

        popularity = self._popularity(candidate)
        if popularity is None:
            unmeasured.append("popularity (neither downloads nor stars reported)")
        else:
            components.append(popularity)

        # Renormalise over what was actually measurable, so a missing signal
        # dilutes nobody: without this, a package that reports less would score
        # lower purely for reporting less.
        divisor = sum(component.weight for component in components)
        total = (
            round(sum(c.value * c.weight for c in components) / divisor, 4)
            if divisor else 0.0
        )
        return Score(total=total, components=tuple(components), unmeasured=tuple(unmeasured))

    def rank(
        self,
        candidates: Iterable[Candidate],
        *,
        obligations: Mapping[str, str] | None = None,
    ) -> tuple[tuple[Candidate, Score], ...]:
        """Best first. Ties break on purl so the order is reproducible."""
        lookup = dict(obligations or {})
        scored = [
            (candidate, self.score(candidate, obligation=lookup.get(candidate.purl, "unknown")))
            for candidate in candidates
        ]
        scored.sort(key=lambda pair: (-pair[1].total, pair[0].purl))
        return tuple(scored)

    # -- components -------------------------------------------------------

    def _fit(self, candidate: Candidate) -> Component:
        exact = sum(1 for m in candidate.matches if m.exact)
        return Component(
            name="fit", value=candidate.fit, weight=self.weights["fit"],
            raw={"coverage": candidate.coverage, "exact": exact},
            reason=(
                f"covers {candidate.coverage:.0%} of the required concepts, "
                f"{exact} of them exactly"
            ),
        )

    def _evidence(self, candidate: Candidate) -> Component:
        best = candidate.evidence
        if best >= 0.90:
            quality = "validated"
        elif best >= 0.60:
            quality = "inferred"
        elif best > 0:
            quality = "guessed from naming"
        else:
            quality = "unsupported"
        return Component(
            name="evidence", value=best, weight=self.weights["evidence"],
            raw=best,
            reason=f"strongest capability claim is {quality} at {best:.2f}",
        )

    def _maintenance(self, candidate: Candidate) -> Component | None:
        age = candidate.artifact.age_days
        if age is None:
            return None
        if age <= FRESH_DAYS:
            value = 1.0
        elif age >= STALE_DAYS:
            value = 0.0
        else:
            value = round(1.0 - (age - FRESH_DAYS) / (STALE_DAYS - FRESH_DAYS), 4)
        return Component(
            name="maintenance", value=value, weight=self.weights["maintenance"],
            raw=round(age, 1),
            reason=f"last release {age:.0f} days ago",
        )

    def _licence(self, obligation: str) -> Component:
        value = OBLIGATION_COST.get(obligation, 0.0)
        return Component(
            name="licence", value=value, weight=self.weights["licence"],
            raw=obligation,
            reason=f"{obligation} obligations to absorb",
        )

    def _footprint(self, candidate: Candidate) -> Component | None:
        if not candidate.artifact.dependencies:
            # Genuinely zero dependencies is the best possible footprint, but so
            # is an unpublished list, and the two must not score the same. Only
            # a source that publishes dependency data at all can be scored.
            if not candidate.artifact.raw:
                return None
            count = 0
        else:
            count = candidate.dependency_count

        if count <= LEAN_DEPENDENCIES:
            value = 1.0
        elif count >= HEAVY_DEPENDENCIES:
            value = 0.0
        else:
            value = round(
                1.0 - (count - LEAN_DEPENDENCIES) / (HEAVY_DEPENDENCIES - LEAN_DEPENDENCIES),
                4,
            )
        return Component(
            name="footprint", value=value, weight=self.weights["footprint"],
            raw=count,
            reason=f"{count} direct dependencies",
        )

    def _popularity(self, candidate: Candidate) -> Component | None:
        artifact = candidate.artifact
        if artifact.downloads is None and artifact.stars is None:
            return None

        # Logarithmic, because the difference between 10 and 1000 users is
        # meaningful and the difference between 100k and 10M is mostly age.
        import math

        scores: list[float] = []
        detail: dict[str, Any] = {}
        if artifact.downloads is not None:
            detail["downloads"] = artifact.downloads
            scores.append(min(math.log10(artifact.downloads + 1) / 7.0, 1.0))
        if artifact.stars is not None:
            detail["stars"] = artifact.stars
            scores.append(min(math.log10(artifact.stars + 1) / 5.0, 1.0))

        value = round(max(scores), 4)
        return Component(
            name="popularity", value=value, weight=self.weights["popularity"],
            raw=detail,
            reason="; ".join(f"{k}={v}" for k, v in detail.items()) or "no usage reported",
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Ranker {self.weights}>"
