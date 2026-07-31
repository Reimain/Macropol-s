"""What a dependency problem is, stated so a solver can be swapped out.

The plan defers PubGrub and ships backtracking, on the condition that the
*model* is PubGrub-shaped so the better solver drops in without a redesign.
That means three things, and they are the reason this module exists separately
from the solver that consumes it:

1. **A requirement is a term**: a package coordinate plus a version range, with
   the thing that asked for it named. PubGrub reasons over terms; so does the
   backtracking solver; neither needs to know how the other works.
2. **The registry is a protocol.** Where versions come from — a lockfile, a
   materialised simulator world, a live index — is not the solver's business.
3. **Failure is a value, not an exception.** `Unsatisfiable` carries the two
   requirements that could not both hold *and the window each one demands*.

Point 3 is the one worth defending. A solver that returns "unsatisfiable" and
stops has told you that you have a problem and nothing about which problem. The
person reading it then bisects their dependency tree by hand — which is the work
the solver was supposed to do. So the conflict is part of the return type, it
names the pair, and it quotes both windows as the manifests wrote them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Iterable,
    Iterator,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from ...domain.version import Version, VersionRange, parse_range
from ...errors import VersionError


@dataclass(frozen=True, slots=True)
class Requirement:
    """One package asking for a version window of another.

    `requested_by` is not decoration. It is what turns "no version of `urllib3`
    works" into "`requests 2.31` wants `<2`, `boto3 1.34` wants `>=2`", which is
    the difference between a report and an answer.
    """

    coordinate: str                       # version-independent purl, e.g. pkg:pypi/urllib3
    range: str                            # as written: "^1.2.0", "[1.0,2.0)", ">=2,<3"
    requested_by: str = "root"            # coordinate of the requester, or "root"
    ecosystem: str = "generic"
    optional: bool = False
    derived_from: tuple[str, ...] = ()    # evidence ids — the chain back to file:line

    @property
    def name(self) -> str:
        """The bare package name, for messages a human reads."""
        return self.coordinate.rpartition("/")[2] or self.coordinate

    def parsed(self) -> VersionRange:
        """The range as an algebra. Raises `VersionError` on a range we cannot read."""
        return parse_range(self.range, ecosystem=self.ecosystem)

    def allows(self, version: str) -> bool:
        """Whether a candidate satisfies this requirement.

        An unreadable range **admits nothing** rather than everything. Treating
        a range we failed to parse as `*` is how a solver returns a confident
        answer built on a constraint it never applied.
        """
        try:
            return self.parsed().allows(version)
        except (VersionError, ValueError):
            return False

    @property
    def readable(self) -> bool:
        try:
            self.parsed()
            return True
        except (VersionError, ValueError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate, "range": self.range,
            "requested_by": self.requested_by, "ecosystem": self.ecosystem,
            "optional": self.optional, "derived_from": list(self.derived_from),
        }

    def __str__(self) -> str:
        return f"{self.requested_by} requires {self.name} {self.range}"


@dataclass(frozen=True, slots=True)
class Assignment:
    """A decision: this coordinate resolves to this version, because of these."""

    coordinate: str
    version: str
    satisfies: tuple[Requirement, ...] = ()
    depth: int = 0

    @property
    def name(self) -> str:
        return self.coordinate.rpartition("/")[2] or self.coordinate

    @property
    def purl(self) -> str:
        return f"{self.coordinate}@{self.version}"

    @property
    def derived_from(self) -> tuple[str, ...]:
        """Every evidence id behind every requirement this assignment satisfies."""
        return tuple(dict.fromkeys(
            item for requirement in self.satisfies for item in requirement.derived_from
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate, "version": self.version,
            "purl": self.purl, "depth": self.depth,
            "satisfies": [r.to_dict() for r in self.satisfies],
        }

    def __str__(self) -> str:
        return f"{self.name} = {self.version}"


@dataclass(frozen=True, slots=True)
class Conflict:
    """Why nothing works, named precisely enough to act on.

    Both requirements, both windows, and the candidates that were available. A
    conflict that says "urllib3 is unsatisfiable" is a shrug; this one says
    which two packages disagree, what each demanded, and what existed — which
    is enough to decide whether to pin, upgrade, or drop one of them.
    """

    coordinate: str
    left: Requirement
    right: Requirement | None = None
    available: tuple[str, ...] = ()
    reason: str = ""

    @property
    def name(self) -> str:
        return self.coordinate.rpartition("/")[2] or self.coordinate

    @property
    def pair(self) -> tuple[str, str]:
        """The two requesters at odds. The second is empty when only one demand exists."""
        return (self.left.requested_by, self.right.requested_by if self.right else "")

    def explain(self) -> str:
        """One paragraph a human can act on without opening a dependency tree."""
        if self.right is None:
            detail = (
                f"{self.left.requested_by} requires {self.name} {self.left.range}"
            )
            if self.available:
                detail += f", but the available versions are {', '.join(self.available)}"
            else:
                detail += ", and no versions of it are known at all"
            return f"{detail}. {self.reason}".strip()

        return (
            f"{self.name} cannot be resolved: "
            f"{self.left.requested_by} requires {self.left.range} and "
            f"{self.right.requested_by} requires {self.right.range}"
            + (
                f"; the available versions are {', '.join(self.available)}, "
                f"and none satisfies both"
                if self.available else "; no version satisfies both"
            )
            + (f". {self.reason}" if self.reason else "")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate, "name": self.name,
            "left": self.left.to_dict(),
            "right": self.right.to_dict() if self.right else None,
            "available": list(self.available), "reason": self.reason,
            "pair": list(self.pair), "explanation": self.explain(),
        }

    def __str__(self) -> str:
        return self.explain()


@dataclass(frozen=True, slots=True)
class Solution:
    """The outcome of a solve. Satisfiable or not, it explains itself.

    `conflicts` is populated exactly when `satisfiable` is false, and is never
    empty in that case — an unsatisfiable result with nothing to point at would
    be the failure this whole model exists to prevent, so `__post_init__`
    refuses to construct one.
    """

    satisfiable: bool
    assignments: tuple[Assignment, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    unreadable: tuple[Requirement, ...] = ()
    attempts: int = 0
    exhausted: bool = False               # the step budget ran out; not a proof

    def __post_init__(self) -> None:
        if not self.satisfiable and not self.conflicts and not self.exhausted:
            raise ValueError(
                "an unsatisfiable solution must name a conflict; "
                "'it does not work' without saying why is not a usable answer"
            )

    @property
    def resolved(self) -> dict[str, str]:
        return {a.coordinate: a.version for a in self.assignments}

    def version_of(self, coordinate: str) -> str:
        return self.resolved.get(coordinate, "")

    def explain(self) -> str:
        if self.satisfiable:
            return f"resolved {len(self.assignments)} packages in {self.attempts} steps"

        # Exhaustion is always disclosed, even when real conflicts were found
        # along the way. Those conflicts are true; what is *not* known is
        # whether they are the whole story, and presenting a partial search as
        # a complete one is the failure this whole module is written against.
        cut_short = (
            f"the search stopped after {self.attempts} steps, so this is a "
            f"budget exhaustion and not an unsatisfiability proof — there may "
            f"be more"
        )
        if not self.conflicts:
            return f"nothing was proven: {cut_short}"

        found = "\n".join(conflict.explain() for conflict in self.conflicts)
        return f"{found}\n{cut_short}" if self.exhausted else found

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfiable": self.satisfiable,
            "assignments": [a.to_dict() for a in self.assignments],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "unreadable": [r.to_dict() for r in self.unreadable],
            "attempts": self.attempts, "exhausted": self.exhausted,
            "explanation": self.explain(),
        }

    def __bool__(self) -> bool:
        return self.satisfiable

    def __str__(self) -> str:
        state = "SAT" if self.satisfiable else "UNSAT"
        return f"[{state}] {self.explain()}"


@runtime_checkable
class PackageIndex(Protocol):
    """Where versions and their dependencies come from.

    A protocol so the solver never learns whether it is reading a lockfile, the
    simulator's materialised world, or a live registry. This is also the seam
    the crawler plugs into without the solver changing.
    """

    def versions_of(self, coordinate: str) -> Sequence[str]:
        ...

    def dependencies_of(self, coordinate: str, version: str) -> Sequence[Requirement]:
        ...


class StaticIndex:
    """An index built from what discovery already found. No network, ever.

    This is the default because it is the honest one: the platform resolves
    against the versions it has *observed*, and a coordinate it has never seen
    yields no candidates — which becomes a named conflict rather than a silent
    assumption that some satisfying version must exist somewhere.
    """

    def __init__(
        self,
        versions: Mapping[str, Iterable[str]] | None = None,
        dependencies: Mapping[str, Iterable[Requirement]] | None = None,
        *,
        ecosystem: str = "generic",
    ) -> None:
        self.ecosystem = ecosystem
        self._versions: dict[str, list[str]] = {
            key: list(value) for key, value in (versions or {}).items()
        }
        self._dependencies: dict[str, list[Requirement]] = {
            key: list(value) for key, value in (dependencies or {}).items()
        }

    def add(self, coordinate: str, version: str,
            *requirements: Requirement) -> "StaticIndex":
        known = self._versions.setdefault(coordinate, [])
        if version not in known:
            known.append(version)
        if requirements:
            self._dependencies[f"{coordinate}@{version}"] = list(requirements)
        return self

    def versions_of(self, coordinate: str) -> Sequence[str]:
        """Candidates, newest first — the order a solver should try them in.

        Sorted by the version algebra rather than by string, because `1.10.0`
        sorts before `1.9.0` lexically and a solver that tried them in that
        order would pick the wrong "latest" every time.
        """
        found = self._versions.get(coordinate, [])
        parsed: list[tuple[Version, str]] = []
        unparsed: list[str] = []
        for text in found:
            try:
                parsed.append((Version.parse(text), text))
            except VersionError:
                unparsed.append(text)
        parsed.sort(key=lambda item: item[0], reverse=True)
        return tuple(text for _, text in parsed) + tuple(unparsed)

    def dependencies_of(self, coordinate: str, version: str) -> Sequence[Requirement]:
        return tuple(self._dependencies.get(f"{coordinate}@{version}", ()))

    def __contains__(self, coordinate: object) -> bool:
        return coordinate in self._versions

    def __len__(self) -> int:
        return len(self._versions)

    def __iter__(self) -> Iterator[str]:
        return iter(self._versions)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<StaticIndex {len(self._versions)} coordinates>"


def requirements_from(resolution: Any, *, ecosystem: str = "generic") -> tuple[Requirement, ...]:
    """Turn a `Resolution` into requirements, keeping the evidence chain.

    Ranges become requirements; pins do not, because a pin is an *answer* to a
    requirement rather than one. Every requirement carries the evidence ids of
    the observations that stated it, so a conflict explanation can still reach
    the manifest line that caused it.
    """
    found: list[Requirement] = []
    for entry in getattr(resolution, "resolved", ()):
        for declared in getattr(entry, "ranges", ()):
            requester = "root"
            for observation in getattr(entry, "observations", ()):
                if observation.object and observation.subject != entry.identity:
                    requester = observation.subject
                    break
            found.append(Requirement(
                coordinate=entry.coordinate, range=declared,
                requested_by=requester, ecosystem=ecosystem,
                derived_from=tuple(
                    item.id for item in getattr(entry, "evidence", ())
                ),
            ))
    return tuple(found)
