"""Spending a finite validation budget on the claims that most deserve it.

This is the paper's economic argument turned into code. Conjectures are cheap
and getting cheaper; validation is "physical, human and institutional — and so,
costly and slow". Our own crawler can propose reuse candidates far faster than
any agent confirms them. Something has to decide what gets checked, and the
default — first in, first out — is the one choice guaranteed to be wrong, since
arrival order carries no information about value.

So claims are ranked by **expected information gain**: how much would knowing
the answer change?

```
value  =  uncertainty × leverage × volatility  ÷  cost

uncertainty = 4·p·(1−p)      1.0 at p=0.5, 0.0 at either extreme
leverage    = log₂(1+deps)   sub-linear: 100 dependents is not 100× one
volatility  = how fast this decays into being wrong again
```

Each factor earns its place:

* **Uncertainty peaks in the middle.** Validating a claim already held at 0.97
  buys almost nothing, and neither does one at 0.02. The budget belongs on the
  claims that could go either way — which is exactly where a system left to
  itself will not look, because those are the uncomfortable ones.
* **Leverage is logarithmic**, the same discipline as the elasticity curve: a
  claim a hundred conclusions rest on matters more than one, but not a hundred
  times more, and a linear weight would let one hub swallow the whole budget.
* **Cost divides.** A claim checkable in silico for nothing should be checked
  even if it matters little; one requiring a week at a bench needs to matter a
  great deal.

**The deferred list is the deliverable, not the leftovers.** A budgeted run that
reports only what it validated is a run that has hidden its own bottleneck. The
honest output is: here is what was checked, here is what was not, and here is
the most valuable thing we could not afford — because that last line is what
justifies a bigger budget, or a cheaper validator.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError


class BudgetError(GratimosError):
    """A budget was misconfigured, or asked to spend what it does not have."""


class Unit(str, Enum):
    """What the budget is denominated in. One run, one unit."""

    CALLS = "calls"          # agent invocations
    MONEY = "money"          # currency, whatever the deployment uses
    SECONDS = "seconds"      # wall clock, which is what a bench costs
    TOKENS = "tokens"


#: Cost assumed when a candidate does not state one. Deliberately 1.0 rather
#: than 0.0: a free-by-default validation would sort to the top of every queue
#: and starve everything that honestly declared its cost.
DEFAULT_COST = 1.0


@dataclass(frozen=True, slots=True)
class Candidate:
    """A claim waiting to be checked, with what makes it worth checking."""

    id: str
    statement: str
    confidence: float
    dependents: int = 0
    volatility: float = 1.0
    cost: float = DEFAULT_COST
    subject: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise BudgetError(
                f"candidate {self.id!r} has confidence {self.confidence}, which "
                f"is not a probability"
            )
        if self.cost <= 0:
            raise BudgetError(
                f"candidate {self.id!r} claims cost {self.cost}; a validation "
                f"that costs nothing would outrank everything honest"
            )
        if self.dependents < 0:
            raise BudgetError(f"candidate {self.id!r} has negative dependents")

    @property
    def uncertainty(self) -> float:
        """Peaks at 0.5, zero at either extreme. What we might actually learn."""
        return round(4 * self.confidence * (1 - self.confidence), 6)

    @property
    def leverage(self) -> float:
        """Sub-linear in dependents, so no single hub swallows the budget."""
        return round(math.log2(1 + self.dependents), 6)

    @property
    def value(self) -> float:
        """Expected information gain per unit spent."""
        # A claim nothing depends on is still worth something — it may be the
        # answer somebody asked for — so leverage contributes additively rather
        # than multiplying the whole thing to zero.
        return round(
            self.uncertainty * (1.0 + self.leverage) * self.volatility / self.cost, 6
        )

    def explain(self) -> str:
        return (
            f"{self.value:.4f} = uncertainty {self.uncertainty:.2f} "
            f"× (1 + leverage {self.leverage:.2f}) × volatility "
            f"{self.volatility:.2f} ÷ cost {self.cost:.2f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "statement": self.statement, "subject": self.subject,
            "confidence": self.confidence, "dependents": self.dependents,
            "volatility": self.volatility, "cost": self.cost,
            "uncertainty": self.uncertainty, "leverage": self.leverage,
            "value": self.value, "reason": self.reason,
            "explanation": self.explain(),
        }

    def __str__(self) -> str:
        return f"{self.statement} ({self.value:.3f})"


@dataclass(slots=True)
class Budget:
    """What may be spent, and what has been. Mutable by design — it is a meter."""

    total: float
    unit: Unit = Unit.CALLS
    spent: float = 0.0
    reserve: float = 0.0

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise BudgetError("a budget must be positive")
        if self.reserve < 0 or self.reserve >= self.total:
            raise BudgetError(
                "the reserve must be smaller than the budget; a reserve that "
                "consumes the whole budget means nothing may ever be spent"
            )

    @property
    def remaining(self) -> float:
        return max(self.total - self.spent, 0.0)

    @property
    def available(self) -> float:
        """What may be spent *now* — the reserve is held back for escalations."""
        return max(self.remaining - self.reserve, 0.0)

    @property
    def exhausted(self) -> bool:
        return self.available <= 0

    def affords(self, cost: float, *, urgent: bool = False) -> bool:
        return (self.remaining if urgent else self.available) >= cost

    def spend(self, cost: float, *, urgent: bool = False) -> float:
        """Draw down. Raises rather than going negative — a silent overspend is
        a budget that was never a budget."""
        if not self.affords(cost, urgent=urgent):
            raise BudgetError(
                f"cannot spend {cost:.4f} {self.unit.value}: "
                f"{self.available:.4f} available"
                + (f" ({self.reserve:.4f} held in reserve)" if self.reserve else "")
            )
        self.spent += cost
        return self.remaining

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit.value, "total": self.total, "spent": self.spent,
            "remaining": self.remaining, "available": self.available,
            "reserve": self.reserve, "exhausted": self.exhausted,
        }

    def __str__(self) -> str:
        return (
            f"{self.spent:.2f}/{self.total:.2f} {self.unit.value} spent, "
            f"{self.available:.2f} available"
        )


@dataclass(frozen=True, slots=True)
class Outcome:
    """One validation that actually happened."""

    candidate: Candidate
    validated: bool
    confidence: float = 0.0
    cost: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.candidate.id, "validated": self.validated,
            "confidence": self.confidence, "cost": self.cost, "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Report:
    """What a budgeted run checked, what it skipped, and what that cost.

    `deferred` and `most_valuable_deferred` are the fields that matter. A report
    without them describes a queue as though it were a completed job.
    """

    checked: tuple[Outcome, ...] = ()
    deferred: tuple[Candidate, ...] = ()
    budget: Budget | None = None
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def coverage(self) -> float:
        offered = len(self.checked) + len(self.deferred)
        return round(len(self.checked) / offered, 4) if offered else 1.0

    @property
    def most_valuable_deferred(self) -> Candidate | None:
        """The best thing we could not afford. The argument for a bigger budget."""
        return max(self.deferred, key=lambda c: c.value, default=None)

    @property
    def value_captured(self) -> float:
        return round(sum(o.candidate.value for o in self.checked), 6)

    @property
    def value_left(self) -> float:
        return round(sum(candidate.value for candidate in self.deferred), 6)

    @property
    def efficiency(self) -> float:
        """Fraction of the available information gain this run actually took."""
        total = self.value_captured + self.value_left
        return round(self.value_captured / total, 4) if total else 1.0

    def render(self) -> str:
        lines = [
            f"validated {len(self.checked)} of "
            f"{len(self.checked) + len(self.deferred)} "
            f"({self.coverage:.0%}), capturing {self.efficiency:.0%} of the "
            f"available information gain"
        ]
        if self.budget is not None:
            lines.append(f"  budget: {self.budget}")
        for outcome in self.checked[:10]:
            mark = "✓" if outcome.validated else "✗"
            lines.append(f"  {mark} {outcome.candidate.statement}")
        if self.deferred:
            lines.append(f"  deferred {len(self.deferred)}, worth "
                         f"{self.value_left:.3f} in unrealised information gain:")
            best = self.most_valuable_deferred
            if best is not None:
                lines.append(f"    most valuable: {best.statement}")
                lines.append(f"      {best.explain()}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        best = self.most_valuable_deferred
        return {
            "checked": [outcome.to_dict() for outcome in self.checked],
            "deferred": [candidate.to_dict() for candidate in self.deferred],
            "coverage": self.coverage,
            "efficiency": self.efficiency,
            "value_captured": self.value_captured,
            "value_left": self.value_left,
            "most_valuable_deferred": best.to_dict() if best else None,
            "budget": self.budget.to_dict() if self.budget else None,
            "duration": round(self.finished_at - self.started_at, 4),
        }

    def __str__(self) -> str:
        return (
            f"{len(self.checked)} checked, {len(self.deferred)} deferred "
            f"({self.efficiency:.0%} of available gain)"
        )


class Queue:
    """Claims awaiting validation, ordered by what knowing would be worth."""

    def __init__(self, candidates: Iterable[Candidate] = ()) -> None:
        self._candidates: dict[str, Candidate] = {}
        for candidate in candidates:
            self.add(candidate)

    def add(self, candidate: Candidate) -> Candidate:
        """Adding the same claim twice keeps the more informative version.

        Re-queueing happens constantly — a crawl re-proposes what a previous
        crawl already offered. Keeping the higher-value record means new
        information about a claim (more dependents found, confidence moved
        toward the middle) raises its priority rather than being discarded.
        """
        existing = self._candidates.get(candidate.id)
        if existing is None or candidate.value > existing.value:
            self._candidates[candidate.id] = candidate
        return self._candidates[candidate.id]

    def remove(self, candidate_id: str) -> bool:
        return self._candidates.pop(candidate_id, None) is not None

    def ranked(self) -> tuple[Candidate, ...]:
        """Best first. Ties break on id so a run is reproducible."""
        return tuple(sorted(
            self._candidates.values(), key=lambda c: (-c.value, c.id)
        ))

    def drain(
        self,
        budget: Budget,
        validate: Callable[[Candidate], Outcome | bool],
        *,
        now: Callable[[], float] | None = None,
    ) -> Report:
        """Spend the budget best-first, and report what was left undone.

        A validator that raises does not abort the run: the failure is recorded
        as an unvalidated outcome, the cost is still charged — the call was
        made — and the remaining budget goes on the next claim. One broken
        check must not strand the rest of the queue.
        """
        clock = now if now is not None else time.monotonic
        started = clock()
        checked: list[Outcome] = []
        deferred: list[Candidate] = []

        for candidate in self.ranked():
            if not budget.affords(candidate.cost):
                deferred.append(candidate)
                continue

            budget.spend(candidate.cost)
            try:
                result = validate(candidate)
            except Exception as exc:  # noqa: BLE001 - a validator is foreign code
                checked.append(Outcome(
                    candidate=candidate, validated=False, cost=candidate.cost,
                    note=f"the validator failed: {exc}",
                ))
                continue

            if isinstance(result, Outcome):
                checked.append(replace(result, cost=result.cost or candidate.cost))
            else:
                checked.append(Outcome(
                    candidate=candidate, validated=bool(result),
                    cost=candidate.cost,
                ))

        for outcome in checked:
            self._candidates.pop(outcome.candidate.id, None)

        return Report(
            checked=tuple(checked), deferred=tuple(deferred), budget=budget,
            started_at=started, finished_at=clock(),
        )

    def preview(self, budget: Budget) -> tuple[tuple[Candidate, ...], tuple[Candidate, ...]]:
        """What this budget would and would not reach. Costs nothing to ask."""
        remaining = budget.available
        would: list[Candidate] = []
        would_not: list[Candidate] = []
        for candidate in self.ranked():
            if candidate.cost <= remaining:
                remaining -= candidate.cost
                would.append(candidate)
            else:
                would_not.append(candidate)
        return tuple(would), tuple(would_not)

    def status(self) -> dict[str, Any]:
        ranked = self.ranked()
        return {
            "queued": len(ranked),
            "value": round(sum(candidate.value for candidate in ranked), 6),
            "cost": round(sum(candidate.cost for candidate in ranked), 6),
            "best": ranked[0].to_dict() if ranked else None,
        }

    def __len__(self) -> int:
        return len(self._candidates)

    def __contains__(self, candidate_id: object) -> bool:
        return candidate_id in self._candidates

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.ranked())

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Queue {len(self._candidates)} awaiting validation>"
