"""How much a suggestion is worth asking, before anybody has asked it.

Ranking has to start somewhere, and starting from "whatever order the generator
happened to emit" would mean the first useful suggestion arrives only after the
system has watched somebody long enough to learn. So the initial order is
computed, and the learned part adjusts it rather than replacing it.

    gain = uncertainty × (1 + leverage) × volatility ÷ cost

Each factor is there for a reason that can be stated:

* **uncertainty** — how much is *not* known about the subject. A node with one
  weak piece of evidence has more to gain from being looked at than one with four
  corroborating sources. Asking about what is already settled is the commonest way
  a recommendation system wastes attention.
* **leverage** — how much else depends on it. Learning something about a package
  fifty things import is worth more than the same fact about a leaf.
* **volatility** — how likely the answer is to have changed. A pinned, quiet
  dependency is not where the surprises are.
* **cost** — dividing rather than subtracting, because cost is a *ratio* concern:
  a cheap answer worth a little often beats an expensive answer worth somewhat
  more, and subtraction gets that backwards at both extremes.

The numbers are deliberately coarse. This is a ranking heuristic whose job is to
put the obviously-useful thing first; precision here would be false, and the
acceptance ledger is what actually tunes the order over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: What each cost band divides by. A three-band scale rather than a continuum,
#: because the difference between "instant" and "reads the whole tree" is what
#: matters and finer gradations would be invented precision.
COST = {"cheap": 1.0, "moderate": 2.0, "expensive": 4.0}

#: Touch kinds where the reviewer is blocked get a floor on gain, so a stuck
#: reviewer always receives *something* even when every factor scores low. An
#: empty suggestion list is the one outcome that must never happen at those
#: moments, because it is exactly when a reviewer gives up.
STUCK_FLOOR = 0.35


@dataclass(frozen=True, slots=True)
class Factors:
    """The inputs to a gain calculation, kept so the answer can be explained."""

    uncertainty: float = 0.5
    leverage: float = 0.0
    volatility: float = 0.5
    cost: str = "cheap"

    @property
    def divisor(self) -> float:
        return COST.get(self.cost, 1.0)

    def score(self) -> float:
        raw = (
            _clamp(self.uncertainty)
            * (1.0 + max(0.0, self.leverage))
            * _clamp(self.volatility)
            / self.divisor
        )
        return round(_clamp(raw), 4)

    def explain(self) -> str:
        return (
            f"uncertainty {self.uncertainty:.2f} × "
            f"(1 + leverage {self.leverage:.2f}) × "
            f"volatility {self.volatility:.2f} ÷ "
            f"cost {self.cost} ({self.divisor:g}) = {self.score():.3f}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uncertainty": self.uncertainty, "leverage": self.leverage,
            "volatility": self.volatility, "cost": self.cost,
            "score": self.score(), "explanation": self.explain(),
        }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def for_touch(touch: Any, *, cost: str = "cheap", **overrides: float) -> Factors:
    """Sensible factors for a touch, before anything specific is known.

    The defaults encode a judgement rather than a measurement: a reviewer who hit
    an error knows almost nothing about why (high uncertainty), and one browsing a
    well-corroborated node knows most of it already.
    """
    from .touch import TouchKind

    base = {
        TouchKind.ERROR: (0.95, 0.2, 0.9),
        TouchKind.EMPTY_RESULT: (0.9, 0.1, 0.8),
        TouchKind.GAP: (0.85, 0.3, 0.6),
        TouchKind.FINDING: (0.6, 0.5, 0.7),
        TouchKind.NODE: (0.5, 0.4, 0.5),
        TouchKind.TERM: (0.8, 0.0, 0.2),
        TouchKind.KIND: (0.75, 0.0, 0.2),
        TouchKind.VERB: (0.7, 0.0, 0.3),
    }.get(getattr(touch, "kind", None), (0.5, 0.0, 0.5))

    uncertainty, leverage, volatility = base
    return Factors(
        uncertainty=overrides.get("uncertainty", uncertainty),
        leverage=overrides.get("leverage", leverage),
        volatility=overrides.get("volatility", volatility),
        cost=cost,
    )


def rank(score: float, *, stuck: bool = False) -> float:
    """The final gain, with the floor a blocked reviewer is owed."""
    return max(score, STUCK_FLOOR) if stuck else score
