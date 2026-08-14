"""The acknowledged base — what has been validated, and how much it is still worth.

This is the store that lets the agent be turned off. A verdict validated once is
remembered, and the next identical need is answered from memory at no cost and
no latency.

The danger is precisely the same mechanism. A verdict about open source is a
claim about a *moving* world: the package that was healthy and permissively
licensed eighteen months ago may now be archived, relicensed, or carrying a
CVE. A cache that does not know this will confidently recommend it forever, and
the failure is silent — the system looks like it is working right up until
somebody ships the recommendation.

So every verdict carries three things that a plain cache does not:

* a **half-life**, so its confidence decays with age rather than staying flat;
* a **volatility class**, because a licence changes far more slowly than
  maintenance status and they must not decay at the same rate;
* an **invalidation hook**, so a contradicting observation retires it at once
  instead of waiting for it to age out.

Below the escalation floor the verdict stops being an answer and becomes a
hint, and the agent is asked again. That threshold is the whole safety story.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError
from ..reason.facts import Fact, Provenance

#: Below this a remembered verdict is no longer trusted to answer on its own.
#: It is still shown as a hint, and the agent is consulted.
ESCALATION_FLOOR = 0.55

#: A verdict is never trusted above this however fresh, because it is a memory
#: of a judgement rather than the judgement itself.
RECALL_CEILING = 0.92


class Volatility(str, Enum):
    """How fast a claim goes stale. Half-lives in days.

    These are the honest part of the design. Treating every fact as equally
    durable is what makes a knowledge base rot: a licence and a maintenance
    status decay at completely different rates and must not share a TTL.
    """

    IMMUTABLE = "immutable"      # an API signature at a pinned version
    SLOW = "slow"                # licence, ecosystem, language
    MODERATE = "moderate"        # capability, API surface at a major version
    FAST = "fast"                # maintenance, release cadence, popularity
    VOLATILE = "volatile"        # advisories, yanked releases, deprecation

    @property
    def half_life_days(self) -> float:
        return _HALF_LIFE[self]

    @property
    def never_decays(self) -> bool:
        return self is Volatility.IMMUTABLE


_HALF_LIFE: dict[Volatility, float] = {
    Volatility.IMMUTABLE: math.inf,
    Volatility.SLOW: 365.0,
    Volatility.MODERATE: 120.0,
    Volatility.FAST: 30.0,
    Volatility.VOLATILE: 7.0,
}

DAY = 86_400.0


@dataclass(frozen=True, slots=True)
class Verdict:
    """One remembered judgement, with its shelf life."""

    signature: str                       # the need this answers
    subject: str                         # what it is about
    claim: str                           # the predicate
    value: str                           # the answer
    confidence: float                    # as judged at validation time
    volatility: Volatility = Volatility.MODERATE
    rationale: str = ""
    validator: str = ""
    evidence: tuple[str, ...] = ()
    validated_at: float = 0.0
    superseded: bool = False
    recalls: int = 0

    def __post_init__(self) -> None:
        if not self.validated_at:
            object.__setattr__(self, "validated_at", time.time())

    @property
    def id(self) -> str:
        import hashlib

        material = f"{self.signature}\x1f{self.subject}\x1f{self.claim}"
        return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()

    def age_days(self, *, now: float = 0.0) -> float:
        return max(((now or time.time()) - self.validated_at) / DAY, 0.0)

    def current_confidence(self, *, now: float = 0.0) -> float:
        """Confidence discounted for age, and capped for being a recollection.

        Exponential decay on the volatility half-life: a verdict at one
        half-life is worth half what it was, at two half-lives a quarter. That
        curve is right because staleness compounds — the longer since anyone
        looked, the more could have changed since the last thing that changed.
        """
        if self.superseded:
            return 0.0
        if self.volatility.never_decays:
            return min(self.confidence, RECALL_CEILING)

        elapsed = self.age_days(now=now)
        decayed = self.confidence * (0.5 ** (elapsed / self.volatility.half_life_days))
        return round(min(decayed, RECALL_CEILING), 4)

    def is_usable(self, *, now: float = 0.0, floor: float = ESCALATION_FLOOR) -> bool:
        """Whether this can answer on its own, or only inform a fresh look."""
        return not self.superseded and self.current_confidence(now=now) >= floor

    def recalled(self) -> "Verdict":
        return replace(self, recalls=self.recalls + 1)

    def supersede(self) -> "Verdict":
        """Retire it. Kept rather than deleted — how the answer changed matters."""
        return replace(self, superseded=True)

    def as_fact(self, *, now: float = 0.0) -> Fact:
        """The verdict as a fact the reasoning engine can chain over.

        Provenance is `DISTILLED`, deliberately below `VALIDATED`: this is what
        an agent said once, not what an agent is saying now.
        """
        return Fact(
            subject=self.subject,
            predicate=self.claim,
            object=self.value,
            provenance=Provenance.DISTILLED,
            confidence=self.current_confidence(now=now),
            source=self.validator,
            derived_from=self.evidence,
        )

    def to_dict(self, *, now: float = 0.0) -> dict[str, Any]:
        return {
            "id": self.id, "signature": self.signature, "subject": self.subject,
            "claim": self.claim, "value": self.value,
            "confidence": self.confidence,
            "current_confidence": self.current_confidence(now=now),
            "volatility": self.volatility.value, "rationale": self.rationale,
            "validator": self.validator, "evidence": list(self.evidence),
            "validated_at": self.validated_at, "superseded": self.superseded,
            "recalls": self.recalls, "age_days": round(self.age_days(now=now), 2),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Verdict":
        return cls(
            signature=data["signature"], subject=data["subject"],
            claim=data["claim"], value=data.get("value", ""),
            confidence=float(data.get("confidence", 0.0)),
            volatility=Volatility(data.get("volatility", "moderate")),
            rationale=data.get("rationale", ""), validator=data.get("validator", ""),
            evidence=tuple(data.get("evidence", ())),
            validated_at=float(data.get("validated_at", 0.0)),
            superseded=bool(data.get("superseded", False)),
            recalls=int(data.get("recalls", 0)),
        )

    def __str__(self) -> str:
        state = "superseded" if self.superseded else f"{self.current_confidence():.2f}"
        return f"{self.subject} {self.claim} {self.value} [{state}]"


@dataclass(slots=True)
class Recall:
    """What the knowledge base had to say about a need."""

    signature: str
    verdicts: tuple[Verdict, ...] = ()
    usable: tuple[Verdict, ...] = ()
    stale: tuple[Verdict, ...] = ()
    #: The instant this recall was evaluated at. Carried rather than recomputed,
    #: so the reason a verdict was rejected is stated in the same terms that
    #: rejected it — an explanation evaluated at a different clock than the
    #: decision is worse than none.
    evaluated_at: float = 0.0
    floor: float = ESCALATION_FLOOR

    def __post_init__(self) -> None:
        if not self.evaluated_at:
            self.evaluated_at = time.time()

    @property
    def hit(self) -> bool:
        """True when memory can answer without asking the agent."""
        return bool(self.usable)

    @property
    def confidence(self) -> float:
        return max(
            (v.current_confidence(now=self.evaluated_at) for v in self.usable),
            default=0.0,
        )

    @property
    def reason(self) -> str:
        """Why the agent is or is not being called — for the audit trail."""
        if self.usable:
            return f"{len(self.usable)} usable verdict(s) recalled at {self.confidence:.2f}"
        if self.stale:
            best = max(v.current_confidence(now=self.evaluated_at) for v in self.stale)
            oldest = max(v.age_days(now=self.evaluated_at) for v in self.stale)
            return (
                f"{len(self.stale)} verdict(s) recalled but decayed to {best:.2f} "
                f"after {oldest:.0f} days, below the {self.floor} floor"
            )
        return "nothing known about this need"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature, "hit": self.hit,
            "confidence": round(self.confidence, 4), "reason": self.reason,
            "evaluated_at": self.evaluated_at,
            "usable": [v.to_dict(now=self.evaluated_at) for v in self.usable],
            "stale": [v.to_dict(now=self.evaluated_at) for v in self.stale],
        }


class KnowledgeBase:
    """Everything the system has been told and has not forgotten.

    Indexed by need signature, so recall is a dictionary lookup on a key that
    two different phrasings of one question both produce.
    """

    def __init__(self, *, floor: float = ESCALATION_FLOOR) -> None:
        self.floor = floor
        self._verdicts: dict[str, Verdict] = {}
        self._by_signature: dict[str, set[str]] = {}
        self._by_subject: dict[str, set[str]] = {}
        self._calls_saved = 0
        self._calls_made = 0

    # -- writing ---------------------------------------------------------

    def record(self, verdict: Verdict) -> Verdict:
        """Remember a judgement, superseding any earlier one it replaces."""
        existing = self._verdicts.get(verdict.id)
        if existing is not None and not existing.superseded:
            self._verdicts[existing.id] = existing.supersede()

        self._verdicts[verdict.id] = verdict
        self._by_signature.setdefault(verdict.signature, set()).add(verdict.id)
        self._by_subject.setdefault(verdict.subject, set()).add(verdict.id)
        return verdict

    def record_all(self, verdicts: Iterable[Verdict]) -> int:
        return sum(1 for verdict in verdicts if self.record(verdict))

    def invalidate(self, subject: str, *, claim: str = "", reason: str = "") -> int:
        """Retire what is known about a subject, because reality moved.

        The hook that stops the cache lying. A package archived, relicensed or
        given an advisory invalidates immediately — waiting for decay would
        leave a wrong answer in service for a half-life.
        """
        retired = 0
        for verdict_id in list(self._by_subject.get(subject, ())):
            verdict = self._verdicts[verdict_id]
            if verdict.superseded or (claim and verdict.claim != claim):
                continue
            self._verdicts[verdict_id] = verdict.supersede()
            retired += 1
        return retired

    # -- reading ---------------------------------------------------------

    def recall(self, signature: str, *, now: float = 0.0) -> Recall:
        """What is known about this need, split by whether it is still good."""
        verdicts = tuple(
            self._verdicts[i] for i in self._by_signature.get(signature, ())
        )
        live = tuple(v for v in verdicts if not v.superseded)
        usable = tuple(v for v in live if v.is_usable(now=now, floor=self.floor))
        stale = tuple(v for v in live if v not in usable)

        if usable:
            self._calls_saved += 1
            for verdict in usable:
                self._verdicts[verdict.id] = verdict.recalled()

        return Recall(
            signature=signature, verdicts=verdicts, usable=usable, stale=stale,
            evaluated_at=now or time.time(), floor=self.floor,
        )

    def about(self, subject: str, *, include_superseded: bool = False) -> tuple[Verdict, ...]:
        found = tuple(self._verdicts[i] for i in self._by_subject.get(subject, ()))
        return found if include_superseded else tuple(v for v in found if not v.superseded)

    def facts(self, *, now: float = 0.0, floor: float = 0.0) -> tuple[Fact, ...]:
        """Everything still worth believing, as facts to chain over.

        The bridge into forward chaining: recording a verdict and then
        saturating is what turns one validation into every conclusion it
        implies.
        """
        return tuple(
            verdict.as_fact(now=now)
            for verdict in self._verdicts.values()
            if not verdict.superseded and verdict.current_confidence(now=now) >= floor
        )

    def note_agent_call(self) -> None:
        """Record that the agent had to be asked. Drives the savings figure."""
        self._calls_made += 1

    # -- housekeeping ----------------------------------------------------

    def prune(self, *, now: float = 0.0, floor: float = 0.05) -> int:
        """Drop verdicts decayed past any usefulness, and superseded ones.

        A verdict below the floor cannot answer and cannot usefully hint, so
        keeping it only costs memory and slows recall.
        """
        dropped = 0
        for verdict_id, verdict in list(self._verdicts.items()):
            if verdict.superseded or verdict.current_confidence(now=now) < floor:
                self._verdicts.pop(verdict_id, None)
                self._by_signature.get(verdict.signature, set()).discard(verdict_id)
                self._by_subject.get(verdict.subject, set()).discard(verdict_id)
                dropped += 1
        return dropped

    def save(self, path: str | Path) -> int:
        """Persist. The base is the asset — losing it means paying for it again."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "calls_saved": self._calls_saved,
            "calls_made": self._calls_made,
            "verdicts": [v.to_dict() for v in self._verdicts.values()],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return len(self._verdicts)

    @classmethod
    def load(cls, path: str | Path, *, floor: float = ESCALATION_FLOOR) -> "KnowledgeBase":
        source = Path(path)
        base = cls(floor=floor)
        if not source.is_file():
            return base

        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except ValueError as error:
            raise GratimosError(f"{source} is not a readable knowledge base: {error}") from None

        for entry in payload.get("verdicts", ()):
            base.record(Verdict.from_dict(entry))
        base._calls_saved = int(payload.get("calls_saved", 0))
        base._calls_made = int(payload.get("calls_made", 0))
        return base

    # -- reporting -------------------------------------------------------

    @property
    def savings(self) -> dict[str, Any]:
        """How much of the work the base is absorbing.

        The number that says whether distillation is paying for itself. A hit
        rate that stops climbing means the ontology is not generalising and
        every need is arriving as a fresh key.
        """
        total = self._calls_saved + self._calls_made
        return {
            "recalled": self._calls_saved,
            "escalated": self._calls_made,
            "total": total,
            "hit_rate": round(self._calls_saved / total, 4) if total else 0.0,
        }

    def status(self, *, now: float = 0.0) -> dict[str, Any]:
        live = [v for v in self._verdicts.values() if not v.superseded]
        usable = [v for v in live if v.is_usable(now=now, floor=self.floor)]
        return {
            "verdicts": len(self._verdicts),
            "live": len(live),
            "usable": len(usable),
            "stale": len(live) - len(usable),
            "superseded": len(self._verdicts) - len(live),
            "signatures": len(self._by_signature),
            "savings": self.savings,
        }

    def __len__(self) -> int:
        return len(self._verdicts)

    def __iter__(self) -> Iterator[Verdict]:
        return iter(self._verdicts.values())

    def __repr__(self) -> str:  # pragma: no cover - display only
        status = self.status()
        return f"<KnowledgeBase {status['usable']}/{status['verdicts']} usable>"
