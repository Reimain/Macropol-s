"""Do two version windows overlap, and what does upgrading actually break?

Two questions the solver asks constantly and that deserve their own module,
because both have a wrong answer that looks right.

**Overlap.** `>=1.0,<2.0` and `^1.5` obviously intersect; `<2.0` and `>=2.0`
obviously do not. The middle cases are where a naive check fails: `<=1.9` and
`>1.9` share a boundary that neither admits, and `[1.0,2.0)` in Maven's dialect
means something different from `1.0 - 2.0` in npm's. So ranges are reduced to
intervals over the shared `Version` algebra, and the boundary inclusivity is
carried rather than assumed.

Where a range's disjunction and exclusions make an exact answer expensive, this
module says so — `Overlap.certain` is false and the solver falls back to testing
concrete candidates. **An uncertain overlap is never reported as no overlap**,
because that would let the solver declare a conflict that does not exist and
send somebody hunting for a problem they do not have.

**Breaking changes.** Semver says a major bump may break you. It does not say it
*will*, and the distinction matters: reporting every major bump as a break makes
the report worthless and people stop reading it. So the classification is
`MAJOR`/`MINOR`/`PATCH`/`PRERELEASE`/`UNKNOWN` and the risk attached to it is
stated as risk, not as fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from ...domain.version import Comparator, Op, Version, VersionRange, parse_range
from ...errors import VersionError


class Change(str, Enum):
    """How far apart two versions are, in the terms semver defines."""

    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    PRERELEASE = "prerelease"
    DOWNGRADE = "downgrade"
    UNKNOWN = "unknown"

    @property
    def may_break(self) -> bool:
        """Whether a consumer *might* be broken. Not whether it will be."""
        return self in (Change.MAJOR, Change.DOWNGRADE, Change.UNKNOWN)

    @property
    def risk(self) -> float:
        return {
            Change.NONE: 0.0, Change.PATCH: 0.1, Change.MINOR: 0.25,
            Change.PRERELEASE: 0.5, Change.MAJOR: 0.8, Change.DOWNGRADE: 0.9,
            Change.UNKNOWN: 0.6,
        }[self]


@dataclass(frozen=True, slots=True)
class Interval:
    """One conjunction reduced to a window, boundaries carried explicitly."""

    lower: Version | None = None
    lower_inclusive: bool = True
    upper: Version | None = None
    upper_inclusive: bool = False
    excluded: tuple[Version, ...] = ()

    @property
    def unbounded(self) -> bool:
        return self.lower is None and self.upper is None

    @property
    def empty(self) -> bool:
        if self.lower is None or self.upper is None:
            return False
        if self.lower > self.upper:
            return True
        if self.lower == self.upper:
            return not (self.lower_inclusive and self.upper_inclusive)
        return False

    def contains(self, version: Version) -> bool:
        if version in self.excluded:
            return False
        if self.lower is not None:
            if version < self.lower:
                return False
            if version == self.lower and not self.lower_inclusive:
                return False
        if self.upper is not None:
            if version > self.upper:
                return False
            if version == self.upper and not self.upper_inclusive:
                return False
        return True

    def intersects(self, other: "Interval") -> bool:
        """Whether two windows share at least one point.

        The shared-boundary case is the one worth reading carefully: `<=1.9` and
        `>=1.9` intersect at exactly 1.9, `<1.9` and `>=1.9` do not, and a check
        written with `<=` on both sides gets the second one wrong.
        """
        if self.empty or other.empty:
            return False

        lower, lower_inclusive = _higher(
            self.lower, self.lower_inclusive, other.lower, other.lower_inclusive
        )
        upper, upper_inclusive = _lower(
            self.upper, self.upper_inclusive, other.upper, other.upper_inclusive
        )
        if lower is None or upper is None:
            return True
        if lower < upper:
            return True
        if lower == upper:
            # A single shared point survives only if both sides admit it, and
            # only if neither range excludes it outright.
            if not (lower_inclusive and upper_inclusive):
                return False
            return lower not in self.excluded and lower not in other.excluded
        return False

    def __str__(self) -> str:
        left = "(-inf" if self.lower is None else \
            f"{'[' if self.lower_inclusive else '('}{self.lower.text}"
        right = "inf)" if self.upper is None else \
            f"{self.upper.text}{']' if self.upper_inclusive else ')'}"
        return f"{left}, {right}"


def _higher(
    left: Version | None, left_inclusive: bool,
    right: Version | None, right_inclusive: bool,
) -> tuple[Version | None, bool]:
    if left is None:
        return right, right_inclusive
    if right is None:
        return left, left_inclusive
    if left > right:
        return left, left_inclusive
    if right > left:
        return right, right_inclusive
    return left, left_inclusive and right_inclusive


def _lower(
    left: Version | None, left_inclusive: bool,
    right: Version | None, right_inclusive: bool,
) -> tuple[Version | None, bool]:
    if left is None:
        return right, right_inclusive
    if right is None:
        return left, left_inclusive
    if left < right:
        return left, left_inclusive
    if right < left:
        return right, right_inclusive
    return left, left_inclusive and right_inclusive


def to_intervals(value: VersionRange | str, *, ecosystem: str = "generic") -> tuple[Interval, ...]:
    """A range as a disjunction of windows. One interval per clause."""
    parsed = value if isinstance(value, VersionRange) else parse_range(value, ecosystem=ecosystem)
    if parsed.is_any:
        return (Interval(),)

    found: list[Interval] = []
    for clause in parsed.clauses:
        found.append(_interval_of(clause))
    return tuple(found)


def _interval_of(clause: Sequence[Comparator]) -> Interval:
    lower: Version | None = None
    lower_inclusive = True
    upper: Version | None = None
    upper_inclusive = False
    excluded: list[Version] = []

    for comparator in clause:
        if comparator.op is Op.EQ:
            lower, lower_inclusive = comparator.version, True
            upper, upper_inclusive = comparator.version, True
        elif comparator.op is Op.NE:
            excluded.append(comparator.version)
        elif comparator.op is Op.GT:
            lower, lower_inclusive = _higher(lower, lower_inclusive, comparator.version, False)
        elif comparator.op is Op.GTE:
            lower, lower_inclusive = _higher(lower, lower_inclusive, comparator.version, True)
        elif comparator.op is Op.LT:
            upper, upper_inclusive = _lower(upper, upper_inclusive, comparator.version, False)
        elif comparator.op is Op.LTE:
            upper, upper_inclusive = _lower(upper, upper_inclusive, comparator.version, True)

    return Interval(lower, lower_inclusive, upper, upper_inclusive, tuple(excluded))


@dataclass(frozen=True, slots=True)
class Overlap:
    """Whether two windows can both hold, and how sure we are of that.

    `certain` false with `overlaps` true is the deliberate bias: an answer we
    could not compute exactly is reported as *possibly* compatible, never as
    incompatible, so the solver tests candidates instead of declaring a conflict
    that may not exist.
    """

    overlaps: bool
    certain: bool = True
    reason: str = ""
    left: tuple[str, ...] = ()
    right: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.overlaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "overlaps": self.overlaps, "certain": self.certain,
            "reason": self.reason, "left": list(self.left), "right": list(self.right),
        }

    def __str__(self) -> str:
        state = "overlaps" if self.overlaps else "disjoint"
        return f"{state}{'' if self.certain else ' (uncertain)'}: {self.reason}"


def overlaps(left: str, right: str, *, ecosystem: str = "generic") -> Overlap:
    """Whether any version satisfies both ranges."""
    try:
        left_intervals = to_intervals(left, ecosystem=ecosystem)
    except (VersionError, ValueError) as error:
        return Overlap(True, False, f"could not read {left!r}: {error}")
    try:
        right_intervals = to_intervals(right, ecosystem=ecosystem)
    except (VersionError, ValueError) as error:
        return Overlap(True, False, f"could not read {right!r}: {error}")

    rendered_left = tuple(str(i) for i in left_intervals)
    rendered_right = tuple(str(i) for i in right_intervals)

    for one in left_intervals:
        for other in right_intervals:
            if one.intersects(other):
                return Overlap(
                    True, True, f"{one} and {other} share versions",
                    rendered_left, rendered_right,
                )

    return Overlap(
        False, True,
        f"{' or '.join(rendered_left)} and {' or '.join(rendered_right)} are disjoint",
        rendered_left, rendered_right,
    )


def classify(before: str, after: str) -> Change:
    """How big a step it is from one version to another."""
    try:
        old = Version.parse(before)
        new = Version.parse(after)
    except VersionError:
        return Change.UNKNOWN

    if old == new:
        return Change.NONE
    if new < old:
        return Change.DOWNGRADE

    old_parts = old.release + (0, 0, 0)
    new_parts = new.release + (0, 0, 0)

    if new_parts[0] != old_parts[0]:
        return Change.MAJOR
    # 0.x is the exception every ecosystem honours in practice: pre-1.0, a minor
    # bump is where breaking changes live, and semver itself says so (rule 4).
    if old_parts[0] == 0 and new_parts[1] != old_parts[1]:
        return Change.MAJOR
    if new_parts[1] != old_parts[1]:
        return Change.MINOR
    if new_parts[2] != old_parts[2]:
        return Change.PATCH
    if new.prerelease != old.prerelease:
        return Change.PRERELEASE
    return Change.NONE


@dataclass(frozen=True, slots=True)
class UpgradeStep:
    """One candidate upgrade, with what it costs and what it would break."""

    coordinate: str
    current: str
    target: str
    change: Change
    satisfies: tuple[str, ...] = ()       # requester coordinates still satisfied
    breaks: tuple[str, ...] = ()          # requester coordinates no longer satisfied

    @property
    def safe(self) -> bool:
        return not self.breaks and not self.change.may_break

    @property
    def risk(self) -> float:
        base = self.change.risk
        return min(1.0, base + 0.1 * len(self.breaks))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate, "current": self.current,
            "target": self.target, "change": self.change.value,
            "safe": self.safe, "risk": round(self.risk, 3),
            "satisfies": list(self.satisfies), "breaks": list(self.breaks),
        }

    def __str__(self) -> str:
        mark = "safe" if self.safe else f"breaks {len(self.breaks)}"
        return f"{self.coordinate} {self.current} -> {self.target} ({self.change.value}, {mark})"


def safe_upgrades(
    coordinate: str,
    current: str,
    candidates: Iterable[str],
    requirements: Iterable[Any],
    *,
    ecosystem: str = "generic",
) -> tuple[UpgradeStep, ...]:
    """Every upgrade from `current`, each labelled with who it breaks.

    Returns *all* of them rather than the best one, ordered safest first. Which
    upgrade to take is a judgement about the codebase — how well tested it is,
    whether the major bump's breaking change is one it uses — and the platform
    is not in a position to make that judgement for somebody. It is in a
    position to say exactly what each option costs.
    """
    demands = [r for r in requirements if getattr(r, "coordinate", "") == coordinate]
    steps: list[UpgradeStep] = []

    for candidate in candidates:
        change = classify(current, candidate)
        if change in (Change.NONE, Change.DOWNGRADE):
            continue
        satisfied = tuple(
            r.requested_by for r in demands if r.allows(candidate)
        )
        broken = tuple(
            r.requested_by for r in demands if not r.allows(candidate)
        )
        steps.append(UpgradeStep(
            coordinate=coordinate, current=current, target=candidate,
            change=change, satisfies=satisfied, breaks=broken,
        ))

    steps.sort(key=lambda step: (step.risk, step.target))
    return tuple(steps)
