"""The gate — what disqualifies a candidate before anything ranks it.

Hard constraints are not preferences with a large weight. A licence the project
cannot absorb, a runtime it cannot run, a synchronous library in an async call
path: none of those become acceptable because the candidate is popular, and a
scoring function that could be outvoted on them is a scoring function that will
eventually recommend one. So they are evaluated first, separately, and a failure
excludes.

Three rules the gate is built around:

* **Refusal is reported, never silent.** Every blocked candidate comes back with
  its blockers attached and a remediation on each. A shortlist that quietly
  omits the obvious answer sends the reader to find it themselves and wonder why
  the tool missed it.
* **Unknown is not permission.** An undeclared licence blocks. It is tempting to
  let it through as "probably MIT" because most things are — that reasoning is
  how AGPL code arrives in a proprietary product.
* **Unverifiable is distinguished from failed.** A package that declares no
  runtime range is not incompatible; it is unstated. Those become
  `unverified` entries — surfaced as gaps on the answer, not as verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..ontology.need import Constraint, ConstraintKind, Need
from . import bridge
from .candidate import Candidate

#: Distribution and linkage contexts, spelled exactly as SLPIE's licence
#: analysis spells them. Restated rather than imported so a typo is caught at
#: construction with a usable message instead of deep inside the bridge — and
#: kept identical rather than prettified, because two vocabularies for one axis
#: is how a `network-service` silently becomes an `internal`.
DISTRIBUTIONS = ("internal", "network_service", "distributed_binary", "distributed_source")
LINKAGES = ("dynamic", "static", "source", "separate")


@dataclass(frozen=True, slots=True)
class Blocker:
    """One reason a candidate cannot be used, and what would fix it."""

    kind: ConstraintKind
    reason: str
    remediation: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "reason": self.reason,
            "remediation": self.remediation, "detail": self.detail,
        }

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.reason}"


@dataclass(frozen=True, slots=True)
class GateResult:
    """The verdict on one candidate, with everything that informed it."""

    candidate: Candidate
    blockers: tuple[Blocker, ...] = ()
    unverified: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    obligation: str = "unknown"

    @property
    def passed(self) -> bool:
        return not self.blockers

    def __bool__(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.candidate.purl,
            "passed": self.passed,
            "blockers": [b.to_dict() for b in self.blockers],
            "unverified": list(self.unverified),
            "warnings": list(self.warnings),
            "obligation": self.obligation,
        }

    def __str__(self) -> str:
        if self.passed:
            return f"{self.candidate.purl}: admitted ({self.obligation})"
        return f"{self.candidate.purl}: blocked — {'; '.join(str(b) for b in self.blockers)}"


class Gate:
    """Applies a need's hard constraints, plus the project's own licence context."""

    def __init__(
        self,
        *,
        project_licence: str = "",
        distribution: str = "network_service",
        linkage: str = "dynamic",
        runtime: str = "",
        allow_deprecated: bool = False,
    ) -> None:
        if distribution not in DISTRIBUTIONS:
            raise ValueError(
                f"unknown distribution {distribution!r}; expected one of {DISTRIBUTIONS}"
            )
        if linkage not in LINKAGES:
            raise ValueError(f"unknown linkage {linkage!r}; expected one of {LINKAGES}")
        self.project_licence = project_licence
        self.distribution = distribution
        self.linkage = linkage
        self.runtime = runtime
        self.allow_deprecated = allow_deprecated

    # -- the gate ---------------------------------------------------------

    def check(self, candidate: Candidate) -> GateResult:
        """Evaluate one candidate. Never raises; a failure is a returned verdict."""
        need = candidate.need
        blockers: list[Blocker] = []
        unverified: list[str] = []
        warnings: list[str] = []

        licence_verdict = bridge.licence_ok(
            candidate.licence,
            project_licence=self.project_licence,
            distribution=self.distribution,
            linkage=self.linkage,
        )
        obligation = bridge.licence_obligation(candidate.licence)
        if not licence_verdict.ok:
            blockers.append(Blocker(
                kind=ConstraintKind.LICENCE,
                reason=licence_verdict.reason,
                remediation=licence_verdict.remediation,
                detail=obligation,
            ))

        for constraint in need.constrained_on(ConstraintKind.LICENCE):
            blocker = self._licence_constraint(constraint, obligation, candidate)
            if blocker is not None:
                blockers.append(blocker)

        blockers.extend(self._runtime(candidate, unverified))
        blockers.extend(self._ecosystem(candidate))
        blockers.extend(self._dependencies(candidate))
        blockers.extend(self._platform(candidate, unverified))

        if candidate.artifact.yanked:
            blockers.append(Blocker(
                kind=ConstraintKind.MATURITY,
                reason=f"{candidate.purl} has been yanked from its registry",
                remediation="select a release that is still published",
            ))

        if candidate.artifact.deprecated:
            message = f"{candidate.purl} is marked deprecated or archived upstream"
            if self.allow_deprecated:
                warnings.append(message)
            else:
                blockers.append(Blocker(
                    kind=ConstraintKind.MATURITY,
                    reason=message,
                    remediation="find a maintained successor, or accept it explicitly",
                ))

        for reason in candidate.blocked_reasons:
            warnings.append(f"the reasoning engine concluded this is blocked: {reason}")

        if not candidate.artifact.described:
            unverified.append(
                f"{candidate.purl} carries no description, keywords or repository; "
                f"nothing here can be checked against the need"
            )

        return GateResult(
            candidate=candidate,
            blockers=tuple(blockers),
            unverified=tuple(dict.fromkeys(unverified)),
            warnings=tuple(dict.fromkeys(warnings)),
            obligation=obligation,
        )

    def filter(self, candidates: Iterable[Candidate]) -> tuple[
        tuple[GateResult, ...], tuple[GateResult, ...]
    ]:
        """Split into (admitted, refused), both fully explained."""
        results = [self.check(candidate) for candidate in candidates]
        return (
            tuple(r for r in results if r.passed),
            tuple(r for r in results if not r.passed),
        )

    # -- individual constraints -------------------------------------------

    def _licence_constraint(
        self,
        constraint: Constraint,
        obligation: str,
        candidate: Candidate,
    ) -> Blocker | None:
        """A need may name licences directly, beyond the obligation analysis.

        `not licence: GPL-3.0-only` is a policy decision an organisation is
        entitled to make even where the obligation would be satisfiable, so it
        is honoured literally rather than being second-guessed.
        """
        wanted = constraint.value.strip().lower()
        declared = candidate.licence.strip().lower()
        present = bool(wanted) and (wanted in declared or wanted == obligation)

        if constraint.negated and present:
            return Blocker(
                kind=ConstraintKind.LICENCE,
                reason=f"{candidate.purl} is {candidate.licence}, which this need excludes",
                remediation=constraint.reason or "choose a candidate under another licence",
                detail=obligation,
            )
        if not constraint.negated and not present:
            return Blocker(
                kind=ConstraintKind.LICENCE,
                reason=(
                    f"this need requires {constraint.value}, and {candidate.purl} "
                    f"declares {candidate.licence or 'nothing'}"
                ),
                remediation=constraint.reason or "relax the licence requirement, or look further",
                detail=obligation,
            )
        return None

    def _runtime(self, candidate: Candidate, unverified: list[str]) -> tuple[Blocker, ...]:
        constraints = candidate.need.constrained_on(ConstraintKind.RUNTIME)
        required = self.runtime or (constraints[0].value if constraints else "")
        if not required:
            return ()

        version = _version_part(required)
        supported = candidate.artifact.requires_runtime
        verdict = bridge.runtime_ok(
            version, supported, ecosystem=candidate.ecosystem or "generic"
        )
        if verdict.detail == "unstated":
            unverified.append(
                f"{candidate.purl} declares no supported runtime range, so {required} "
                f"is unverified rather than confirmed"
            )
            return ()
        if verdict.ok:
            return ()
        return (Blocker(
            kind=ConstraintKind.RUNTIME,
            reason=f"{candidate.purl}: {verdict.reason}",
            remediation=verdict.remediation,
            detail=verdict.detail,
        ),)

    def _ecosystem(self, candidate: Candidate) -> tuple[Blocker, ...]:
        blockers: list[Blocker] = []
        for constraint in candidate.need.constrained_on(ConstraintKind.ECOSYSTEM):
            wanted = constraint.value.strip().lower()
            actual = candidate.ecosystem.lower()
            # A repository record is not an ecosystem answer — it is the upkeep
            # signal attached to one — so it is never blocked on this axis.
            if actual == "github":
                continue
            if constraint.negated and actual == wanted:
                blockers.append(Blocker(
                    kind=ConstraintKind.ECOSYSTEM,
                    reason=f"{candidate.purl} is in {actual}, which this need excludes",
                    remediation=constraint.reason or "look in a permitted ecosystem",
                ))
            elif not constraint.negated and actual != wanted:
                blockers.append(Blocker(
                    kind=ConstraintKind.ECOSYSTEM,
                    reason=(
                        f"{candidate.purl} is a {actual} package and this need "
                        f"requires {wanted}"
                    ),
                    remediation=constraint.reason or f"search {wanted} instead",
                ))
        return tuple(blockers)

    def _dependencies(self, candidate: Candidate) -> tuple[Blocker, ...]:
        """`not dependency: X` — the constraint that keeps a tree from creeping.

        Checked against the direct dependencies only, and that limit is stated
        rather than hidden: a transitive check needs the full resolved tree,
        which is a resolver's job and not a crawler's.
        """
        blockers: list[Blocker] = []
        declared = {d.lower() for d in candidate.artifact.dependencies}
        for constraint in candidate.need.constrained_on(ConstraintKind.DEPENDENCY):
            wanted = constraint.value.strip().lower()
            present = any(wanted == d or d.startswith(f"{wanted} ") or
                          d.startswith(f"{wanted}=") or d.startswith(f"{wanted}>")
                          for d in declared)
            if constraint.negated and present:
                blockers.append(Blocker(
                    kind=ConstraintKind.DEPENDENCY,
                    reason=f"{candidate.purl} depends on {constraint.value}, which this need excludes",
                    remediation=constraint.reason or "choose a candidate without that dependency",
                ))
        return tuple(blockers)

    def _platform(self, candidate: Candidate, unverified: list[str]) -> tuple[Blocker, ...]:
        """Platform is hard but rarely stated in registry metadata.

        Rather than pretend, an unstated platform is recorded as unverified. The
        alternative — inferring platform support from a package name or a
        classifier that may not exist — would produce a hard block from a guess.
        """
        constraints = candidate.need.constrained_on(ConstraintKind.PLATFORM)
        if not constraints:
            return ()
        text = " ".join(candidate.artifact.keywords).lower()
        blockers: list[Blocker] = []
        for constraint in constraints:
            wanted = constraint.value.strip().lower()
            if constraint.negated and wanted in text:
                blockers.append(Blocker(
                    kind=ConstraintKind.PLATFORM,
                    reason=f"{candidate.purl} advertises {wanted}, which this need excludes",
                    remediation=constraint.reason or "choose a candidate for another platform",
                ))
            elif not constraint.negated and wanted not in text:
                unverified.append(
                    f"{candidate.purl} does not state platform support, so {wanted} "
                    f"is unverified"
                )
        return tuple(blockers)

    def status(self) -> dict[str, Any]:
        return {
            "project_licence": self.project_licence,
            "distribution": self.distribution,
            "linkage": self.linkage,
            "runtime": self.runtime,
            "allow_deprecated": self.allow_deprecated,
            "evaluator": bridge.status(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Gate {self.distribution}/{self.linkage} licence={self.project_licence or '—'}>"


def _version_part(text: str) -> str:
    """`python>=3.11` → `3.11`; `3.11` → `3.11`.

    Needs state runtimes the way people write them, with the interpreter name
    attached. Stripping it here means every caller does not have to.
    """
    cleaned = text.strip()
    for index, character in enumerate(cleaned):
        if character.isdigit():
            return cleaned[index:].strip()
    return cleaned
