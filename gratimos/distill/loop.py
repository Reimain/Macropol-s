"""The teacher/student loop — where the agent gets turned off.

One rule governs the whole thing: **the agent is called when, and only when, the
system does not already know.** Everything else is bookkeeping around that.

```
need → signature → recall
                     ├─ usable      → answer from memory, agent NOT called
                     └─ absent/stale → agent validates
                                        → record the verdict
                                        → forward-chain its consequences
                                        → the next identical need is a recall
```

Two things stop this from becoming a machine for confidently repeating stale
answers, and they are the parts that matter:

* **The floor is on decayed confidence, not on presence.** Having an entry is
  not the same as having a usable one. A remembered verdict past its half-life
  escalates exactly as if it had never been recorded, and is passed to the agent
  as a prior rather than as an answer.
* **A sample of hits is re-validated anyway.** Without it the system can only
  ever confirm itself: every answer comes from memory, memory is never checked,
  and a systematically wrong verdict is never caught. The audit rate is the
  price of knowing the base is still right.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from ..ontology.need import Need
from ..reason.engine import Engine, ForwardReport
from ..reason.facts import Fact, Provenance
from .knowledge import (
    ESCALATION_FLOOR,
    KnowledgeBase,
    Recall,
    Verdict,
    Volatility,
)

#: Fraction of recalls re-validated anyway, so the base cannot drift unchecked.
#: Small enough to keep most of the saving, large enough that a systematically
#: wrong verdict is caught within a few dozen hits rather than never.
DEFAULT_AUDIT_RATE = 0.05


class Route(str, Enum):
    """Which way an answer came, and why. Recorded on every turn."""

    RECALLED = "recalled"            # memory answered; no agent call
    AUDITED = "audited"              # memory answered but was checked anyway
    ESCALATED_UNKNOWN = "escalated_unknown"    # nothing known
    ESCALATED_STALE = "escalated_stale"        # known but decayed below the floor
    ESCALATED_FORCED = "escalated_forced"      # caller demanded a fresh look
    UNAVAILABLE = "unavailable"      # no agent, and memory could not answer

    @property
    def called_agent(self) -> bool:
        return self in (
            Route.AUDITED, Route.ESCALATED_UNKNOWN,
            Route.ESCALATED_STALE, Route.ESCALATED_FORCED,
        )


@dataclass(frozen=True, slots=True)
class Judgement:
    """What a validating agent returned about one candidate."""

    subject: str
    claim: str
    value: str
    confidence: float
    rationale: str = ""
    volatility: Volatility = Volatility.MODERATE
    evidence: tuple[str, ...] = ()

    def to_verdict(self, *, signature: str, validator: str) -> Verdict:
        return Verdict(
            signature=signature, subject=self.subject, claim=self.claim,
            value=self.value, confidence=self.confidence,
            volatility=self.volatility, rationale=self.rationale,
            validator=validator, evidence=self.evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "claim": self.claim, "value": self.value,
            "confidence": self.confidence, "rationale": self.rationale,
            "volatility": self.volatility.value,
        }


@runtime_checkable
class Validator(Protocol):
    """The teacher. Claude is one implementation; any agent satisfying this works.

    Kept this narrow on purpose: the loop must not depend on the Anthropic SDK,
    on a network, or on any particular model. A local model, a rules engine, or
    a human review queue can all stand here.
    """

    name: str

    def validate(self, need: Need, prior: Sequence[Verdict] = ()) -> Sequence[Judgement]:
        """Judge a need, optionally informed by what memory already believed."""
        ...


@dataclass(frozen=True, slots=True)
class Turn:
    """One pass through the loop, and the record of why it went that way."""

    need: Need
    route: Route
    judgements: tuple[Judgement, ...] = ()
    recall: Recall | None = None
    propagated: int = 0
    duration: float = 0.0

    @property
    def answered(self) -> bool:
        return bool(self.judgements) or bool(self.recall and self.recall.hit)

    @property
    def agent_called(self) -> bool:
        return self.route.called_agent

    @property
    def confidence(self) -> float:
        if self.judgements:
            return max(j.confidence for j in self.judgements)
        return self.recall.confidence if self.recall else 0.0

    @property
    def explanation(self) -> str:
        """Why the agent was or was not consulted. Always available."""
        base = self.recall.reason if self.recall else "no memory consulted"
        if self.route is Route.AUDITED:
            return f"{base}; re-validated anyway as part of the audit sample"
        if self.route is Route.RECALLED:
            return f"{base}; agent not called"
        if self.route is Route.UNAVAILABLE:
            return f"{base}; no validator configured"
        return f"{base}; agent consulted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "need": self.need.to_dict(),
            "route": self.route.value,
            "agent_called": self.agent_called,
            "answered": self.answered,
            "confidence": round(self.confidence, 4),
            "explanation": self.explanation,
            "judgements": [j.to_dict() for j in self.judgements],
            "recall": self.recall.to_dict() if self.recall else None,
            "propagated": self.propagated,
            "duration": round(self.duration, 4),
        }

    def __str__(self) -> str:
        return f"{self.need} → {self.route.value} ({self.confidence:.2f})"


class DistillationLoop:
    """Answers needs, calling the validator only when memory cannot.

    Holds a knowledge base and, optionally, a reasoning engine. With an engine,
    a recorded verdict is forward-chained on the spot, so one validation answers
    every question it implies rather than only the one that was asked.
    """

    def __init__(
        self,
        knowledge: KnowledgeBase | None = None,
        validator: Validator | None = None,
        *,
        engine: Engine | None = None,
        audit_rate: float = DEFAULT_AUDIT_RATE,
        floor: float = ESCALATION_FLOOR,
        rng: random.Random | None = None,
    ) -> None:
        if not 0.0 <= audit_rate <= 1.0:
            raise ValueError("audit_rate must be a fraction between 0 and 1")

        self.knowledge = knowledge if knowledge is not None else KnowledgeBase(floor=floor)
        self.validator = validator
        self.engine = engine
        self.audit_rate = audit_rate
        self.floor = floor
        self._rng = rng if rng is not None else random.Random()
        self._turns: list[Turn] = []

    # -- the loop --------------------------------------------------------

    def resolve(self, need: Need, *, force: bool = False, now: float = 0.0) -> Turn:
        """Answer a need, consulting the validator only when necessary."""
        started = time.monotonic()
        signature = need.signature
        recall = self.knowledge.recall(signature, now=now)

        route = self._route(recall, force=force)

        if not route.called_agent:
            turn = Turn(
                need=need, route=route, recall=recall,
                duration=time.monotonic() - started,
            )
            self._turns.append(turn)
            return turn

        if self.validator is None:
            # No teacher. Say so rather than inventing an answer — a loop that
            # fabricated when it could not ask would be worse than one that
            # admits the gap.
            turn = Turn(
                need=need, route=Route.UNAVAILABLE, recall=recall,
                duration=time.monotonic() - started,
            )
            self._turns.append(turn)
            return turn

        self.knowledge.note_agent_call()
        # Stale verdicts go to the validator as priors, not as answers. An agent
        # told what was previously believed can correct it explicitly, which is
        # how a wrong verdict gets retired rather than merely aged out.
        prior = tuple(recall.stale) + (tuple(recall.usable) if route is Route.AUDITED else ())
        judgements = tuple(self.validator.validate(need, prior))

        propagated = self._record(signature, judgements, recall)

        turn = Turn(
            need=need, route=route, judgements=judgements, recall=recall,
            propagated=propagated, duration=time.monotonic() - started,
        )
        self._turns.append(turn)
        return turn

    def _route(self, recall: Recall, *, force: bool) -> Route:
        if force:
            return Route.ESCALATED_FORCED
        if recall.hit:
            # The audit sample. Without it the base can only confirm itself.
            if self.audit_rate and self._rng.random() < self.audit_rate:
                return Route.AUDITED
            return Route.RECALLED
        return Route.ESCALATED_STALE if recall.stale else Route.ESCALATED_UNKNOWN

    def _record(
        self,
        signature: str,
        judgements: Sequence[Judgement],
        recall: Recall,
    ) -> int:
        """Store what the agent said, retiring what it contradicts, then chain."""
        contradicted = {
            (verdict.subject, verdict.claim): verdict
            for verdict in (*recall.usable, *recall.stale)
        }

        for judgement in judgements:
            previous = contradicted.get((judgement.subject, judgement.claim))
            if previous is not None and previous.value != judgement.value:
                # The world moved. Retire the old answer at once rather than
                # letting two contradicting verdicts both sit in the base.
                self.knowledge.invalidate(judgement.subject, claim=judgement.claim)
            self.knowledge.record(
                judgement.to_verdict(signature=signature, validator=self.validator.name)
            )

        if self.engine is None or not judgements:
            return 0

        report = self.engine.learn(*(
            judgement.to_verdict(
                signature=signature, validator=self.validator.name
            ).as_fact()
            for judgement in judgements
        ))
        return report.count

    # -- housekeeping ----------------------------------------------------

    def observe(self, *facts: Fact) -> ForwardReport:
        """Feed the engine an observation and propagate it.

        This is how a crawl result reaches the conclusions it implies without
        anybody asking a question — the background half of the reasoning.
        """
        if self.engine is None:
            return ForwardReport()
        return self.engine.learn(*facts)

    def invalidate(self, subject: str, *, claim: str = "", reason: str = "") -> int:
        """Something changed upstream. Retire what depended on it."""
        return self.knowledge.invalidate(subject, claim=claim, reason=reason)

    # -- reporting -------------------------------------------------------

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    def status(self) -> dict[str, Any]:
        routes: dict[str, int] = {}
        for turn in self._turns:
            routes[turn.route.value] = routes.get(turn.route.value, 0) + 1
        called = sum(1 for turn in self._turns if turn.agent_called)
        return {
            "turns": len(self._turns),
            "agent_calls": called,
            "avoided": len(self._turns) - called,
            "avoidance_rate": (
                round((len(self._turns) - called) / len(self._turns), 4)
                if self._turns else 0.0
            ),
            "routes": routes,
            "knowledge": self.knowledge.status(),
            "validator": self.validator.name if self.validator else None,
            "audit_rate": self.audit_rate,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        status = self.status()
        return (
            f"<DistillationLoop {status['avoided']}/{status['turns']} avoided, "
            f"{len(self.knowledge)} verdicts>"
        )
