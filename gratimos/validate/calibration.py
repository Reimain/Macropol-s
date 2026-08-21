"""Measuring whether our confidence means anything.

This system assigns confidence everywhere — evidence kinds, rule strengths,
recall decay, risk levels. Every one of those numbers is *derived* by a stated
rule, which has been treated as sufficient. It is not. A number derived by a
principled rule can still be systematically wrong, and until it is checked
against outcomes nobody knows which.

The paper names the standard: agents should "know when they don't know, and say
so", and points at AlphaFold, whose per-residue confidence lets researchers
"know when to trust it and when to reach for other methods". That property is
not honesty of intent — it is *calibration*, and calibration is measurable.

A predictor is calibrated when, across everything it scored 0.8, about 80% turn
out true. So: bucket predictions by stated confidence, compare each bucket's
claimed rate to its observed rate, and report the gap.

```
stated 0.9  ████████████ observed 0.62   ← overconfident by 0.28
stated 0.7  ██████████   observed 0.71   ← honest
stated 0.3  ████         observed 0.55   ← underconfident, wasting checks
```

Three commitments make this useful rather than decorative:

* **Sample size gates the claim.** An ECE computed from eleven observations is
  noise wearing a decimal point. Below `MIN_SAMPLES` the report says *unknown*
  rather than producing a number, and every bin carries a Wilson interval so a
  reader sees how much the estimate could move.
* **Overconfidence and underconfidence are different faults.** They are reported
  separately, never averaged into one "error". Overconfidence sends work
  somewhere it should not have gone; underconfidence spends validation budget on
  things already known. Averaging them hides both.
* **Recalibration is conservative and recorded.** The adjustment shrinks toward
  the stated value in proportion to how little evidence the bin has, so a bucket
  with three observations barely moves anything — and every adjusted value
  remembers that it was adjusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import GratimosError


class CalibrationError(GratimosError):
    """A calibration question was asked of data that cannot answer it."""


#: Below this many observations the whole report refuses to state a figure.
#: Ten bins over fewer than this cannot distinguish a real miscalibration from
#: sampling noise, and a confident number here would be worse than silence.
MIN_SAMPLES = 30

#: Below this, one bin reports its rate but is marked `thin` and is excluded
#: from the headline error.
MIN_BIN_SAMPLES = 5

#: 95% Wilson interval.
Z = 1.959964

#: Prior strength for recalibration: how many notional observations the stated
#: confidence is worth. A bin must beat this before it moves the number much.
PRIOR = 8.0

#: Gap above which a bin is called miscalibrated rather than noisy.
TOLERANCE = 0.10


def wilson(successes: int, total: int, *, z: float = Z) -> tuple[float, float]:
    """A proportion's confidence interval, done properly.

    The naive interval (`p ± z·√(p(1-p)/n)`) is badly wrong at the extremes —
    it happily produces bounds outside [0, 1] and gives zero width when a bin is
    all successes, which is precisely where calibration data lands. Wilson does
    not have that failure, which is why it is used here despite being uglier.
    """
    if total <= 0:
        return (0.0, 1.0)
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z / denominator
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    )
    return (max(0.0, centre - spread), min(1.0, centre + spread))


@dataclass(frozen=True, slots=True)
class Observation:
    """One prediction and what actually happened."""

    confidence: float
    correct: bool
    subject: str = ""
    source: str = ""
    at: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise CalibrationError(
                f"confidence {self.confidence} is outside [0, 1]; a calibration "
                f"input that is not a probability cannot be calibrated"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence, "correct": self.correct,
            "subject": self.subject, "source": self.source, "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class Bin:
    """One confidence bucket: what was claimed, what happened."""

    low: float
    high: float
    count: int = 0
    correct: int = 0
    stated: float = 0.0        # mean confidence actually asserted in this bin

    @property
    def observed(self) -> float:
        return self.correct / self.count if self.count else 0.0

    @property
    def gap(self) -> float:
        """Signed: positive means overconfident, negative means underconfident."""
        return round(self.stated - self.observed, 4) if self.count else 0.0

    @property
    def thin(self) -> bool:
        return self.count < MIN_BIN_SAMPLES

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.correct, self.count)

    @property
    def honest(self) -> bool:
        """Whether the claim is within tolerance, *or* the interval covers it.

        The second clause matters: a bin with six observations has an interval
        half the number line wide, and calling it miscalibrated on a point
        estimate would manufacture findings out of small samples.
        """
        if not self.count:
            return True
        low, high = self.interval
        return abs(self.gap) <= TOLERANCE or low <= self.stated <= high

    def to_dict(self) -> dict[str, Any]:
        low, high = self.interval
        return {
            "range": [round(self.low, 2), round(self.high, 2)],
            "count": self.count, "correct": self.correct,
            "stated": round(self.stated, 4), "observed": round(self.observed, 4),
            "gap": self.gap, "thin": self.thin, "honest": self.honest,
            "interval": [round(low, 4), round(high, 4)],
        }

    def __str__(self) -> str:
        if not self.count:
            return f"[{self.low:.1f}–{self.high:.1f}) no observations"
        direction = "over" if self.gap > 0 else "under"
        note = " (thin)" if self.thin else ""
        return (
            f"[{self.low:.1f}–{self.high:.1f}) n={self.count:<4} "
            f"claimed {self.stated:.2f} → actual {self.observed:.2f} "
            f"({direction} by {abs(self.gap):.2f}){note}"
        )


@dataclass(frozen=True, slots=True)
class Report:
    """What the numbers turned out to be worth."""

    bins: tuple[Bin, ...] = ()
    samples: int = 0
    ece: float | None = None
    mce: float | None = None
    brier: float | None = None
    overconfidence: float = 0.0
    underconfidence: float = 0.0

    @property
    def known(self) -> bool:
        """Whether there is enough data to say anything at all."""
        return self.samples >= MIN_SAMPLES

    @property
    def calibrated(self) -> bool:
        if not self.known:
            return False
        return all(bucket.honest for bucket in self.bins if not bucket.thin)

    @property
    def worst(self) -> Bin | None:
        scored = [b for b in self.bins if b.count and not b.thin]
        return max(scored, key=lambda b: abs(b.gap)) if scored else None

    @property
    def verdict(self) -> str:
        """One sentence, written for somebody deciding whether to trust a score."""
        if not self.known:
            return (
                f"unknown — {self.samples} observations, fewer than the "
                f"{MIN_SAMPLES} needed to distinguish miscalibration from noise"
            )
        if self.calibrated:
            return f"calibrated within {TOLERANCE:.0%} across {self.samples} observations"
        direction = (
            "overconfident" if self.overconfidence > self.underconfidence
            else "underconfident"
        )
        worst = self.worst
        detail = f"; worst bin {worst}" if worst else ""
        return f"{direction} (ECE {self.ece:.3f}) across {self.samples} observations{detail}"

    def render(self) -> str:
        lines = [f"calibration: {self.verdict}"]
        for bucket in self.bins:
            if bucket.count:
                mark = " " if bucket.honest else "!"
                lines.append(f"  {mark} {bucket}")
        if self.known:
            lines.append(
                f"  ECE {self.ece:.4f} · MCE {self.mce:.4f} · Brier {self.brier:.4f}"
            )
            lines.append(
                f"  overconfidence {self.overconfidence:.4f} · "
                f"underconfidence {self.underconfidence:.4f}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples, "known": self.known,
            "calibrated": self.calibrated, "verdict": self.verdict,
            "ece": self.ece, "mce": self.mce, "brier": self.brier,
            "overconfidence": round(self.overconfidence, 4),
            "underconfidence": round(self.underconfidence, 4),
            "bins": [bucket.to_dict() for bucket in self.bins],
        }

    def __str__(self) -> str:
        return f"calibration: {self.verdict}"


class Calibrator:
    """Collects predictions and outcomes, and says what the numbers are worth."""

    def __init__(self, *, bins: int = 10, source: str = "") -> None:
        if bins < 2:
            raise CalibrationError("a calibration needs at least two bins")
        self.bins = bins
        self.source = source
        self._observations: list[Observation] = []

    # -- collecting -------------------------------------------------------

    def observe(self, confidence: float, correct: bool, *, subject: str = "",
                source: str = "", at: float = 0.0) -> Observation:
        """Record one prediction and its outcome."""
        observation = Observation(
            confidence=confidence, correct=correct, subject=subject,
            source=source or self.source, at=at,
        )
        self._observations.append(observation)
        return observation

    def extend(self, observations: Iterable[Observation]) -> None:
        for observation in observations:
            if not isinstance(observation, Observation):
                raise CalibrationError("expected Observation instances")
            self._observations.append(observation)

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._observations)

    def _index(self, confidence: float) -> int:
        # 1.0 belongs in the top bin rather than falling off the end.
        return min(int(confidence * self.bins), self.bins - 1)

    # -- measuring --------------------------------------------------------

    def report(self, *, source: str = "") -> Report:
        """Measure. Returns `known=False` rather than a number when data is thin."""
        selected = [
            observation for observation in self._observations
            if not source or observation.source == source
        ]
        total = len(selected)

        buckets: list[dict[str, float]] = [
            {"count": 0, "correct": 0, "sum": 0.0} for _ in range(self.bins)
        ]
        for observation in selected:
            bucket = buckets[self._index(observation.confidence)]
            bucket["count"] += 1
            bucket["correct"] += 1 if observation.correct else 0
            bucket["sum"] += observation.confidence

        width = 1.0 / self.bins
        bins = tuple(
            Bin(
                low=index * width, high=(index + 1) * width,
                count=int(bucket["count"]), correct=int(bucket["correct"]),
                stated=round(bucket["sum"] / bucket["count"], 6) if bucket["count"] else 0.0,
            )
            for index, bucket in enumerate(buckets)
        )

        if total < MIN_SAMPLES:
            return Report(bins=bins, samples=total)

        # ECE weights each bin by how much of the data it holds, so a bin with
        # three points cannot dominate the headline figure.
        ece = sum(bucket.count / total * abs(bucket.gap) for bucket in bins)
        scored = [bucket for bucket in bins if bucket.count and not bucket.thin]
        mce = max((abs(bucket.gap) for bucket in scored), default=0.0)
        brier = sum(
            (observation.confidence - (1.0 if observation.correct else 0.0)) ** 2
            for observation in selected
        ) / total

        over = sum(
            bucket.count / total * bucket.gap for bucket in bins if bucket.gap > 0
        )
        under = sum(
            bucket.count / total * -bucket.gap for bucket in bins if bucket.gap < 0
        )

        return Report(
            bins=bins, samples=total,
            ece=round(ece, 6), mce=round(mce, 6), brier=round(brier, 6),
            overconfidence=over, underconfidence=under,
        )

    # -- correcting -------------------------------------------------------

    def adjust(self, confidence: float) -> float:
        """Map a stated confidence onto what the evidence says it is worth.

        Shrinks toward the stated value in proportion to how thin the bin is:

            adjusted = (n·observed + PRIOR·stated) / (n + PRIOR)

        A bin with two observations barely moves the number; one with two
        hundred moves it most of the way. Below `MIN_SAMPLES` overall the value
        is returned untouched — recalibrating from noise is how a system talks
        itself into a wrong number and then defends it with arithmetic.
        """
        if len(self._observations) < MIN_SAMPLES:
            return confidence

        report = self.report()
        bucket = report.bins[self._index(confidence)]
        if not bucket.count:
            return confidence

        adjusted = (
            (bucket.count * bucket.observed + PRIOR * confidence)
            / (bucket.count + PRIOR)
        )
        return round(min(max(adjusted, 0.0), 1.0), 4)

    def adjusts(self) -> bool:
        """Whether adjustment would currently do anything. Cheap to ask."""
        return len(self._observations) >= MIN_SAMPLES

    def status(self) -> dict[str, Any]:
        report = self.report()
        return {
            "observations": len(self._observations),
            "bins": self.bins,
            "known": report.known,
            "calibrated": report.calibrated,
            "ece": report.ece,
            "adjusting": self.adjusts(),
            "sources": sorted({o.source for o in self._observations if o.source}),
        }

    def __len__(self) -> int:
        return len(self._observations)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Calibrator {len(self._observations)} observations>"
