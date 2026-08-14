"""The access review, computed rather than conducted.

Every enterprise runs a periodic access review, and it is almost always a
spreadsheet exported from a directory, mailed to managers who approve it without
reading it. The reason it is theatre is that the raw data — a list of who holds
what — does not tell anybody what is *wrong*. Finding the problem requires
cross-referencing role definitions, inheritance, usage and separation rules, and
no spreadsheet does that.

This module does it. It takes the role graph and the binding log and returns
ranked findings, each naming the principal or role at fault, the evidence, and
what to do. Ten checks, each of which corresponds to a real audit exception:

| Finding | Why it matters |
|---|---|
| `WILDCARD_GRANT` | a role granting `*` on `*` is an administrator by another name |
| `PERMANENT_ELEVATION` | break-glass still live past its window |
| `REPEATED_ELEVATION` | the same emergency every week is a standing need |
| `EXPIRED_NOT_REVOKED` | lapsed but never withdrawn — invisible to a directory review |
| `SEPARATION_VIOLATED` | someone can both request and approve |
| `UNUSED_ROLE` | granted, never exercised; the definition of over-provisioning |
| `ORPHANED_BINDING` | a role assigned to a principal nobody can identify |
| `DEEP_INHERITANCE` | a chain nobody can read is a chain nobody reviews |
| `DENY_SHADOWED` | a refusal that no allow could ever have reached |
| `PRIVILEGE_CONCENTRATION` | one principal holding most of the model |
| `WEAK_ASSURANCE_HIGH_PRIVILEGE` | production reachable from an SMS code |

The findings are *derived*, never assigned a severity by hand: severity comes
from what the finding is, so two deployments reviewing the same model get the
same ranking and a customer cannot quietly downgrade an exception.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..identity.principal import Assurance
from .engine import MAX_ELEVATION, AccessEngine, Binding, Decision
from .model import Effect, Permission, Scope
from .role import Role, RoleGraph

#: A binding older than this that has never been exercised is reported. Ninety
#: days is the usual review cycle; anything unused across a full cycle is
#: unlikely to be needed.
UNUSED_AFTER = 90 * 86400.0

#: Inheritance depth past which a role stops being readable. Lower than the
#: engine's hard limit on purpose — the engine refuses the unusable, the audit
#: reports the merely unwise.
DEEP_INHERITANCE = 4

#: Break-glass to the same role this many times stops being an emergency.
REPEATED_ELEVATIONS = 3

#: A principal holding more than this share of all roles is flagged. Not wrong
#: by itself; wrong often enough to be worth a look.
CONCENTRATION = 0.5


class Severity(str, Enum):
    """How urgently a finding needs an answer. Ordered."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}[self.value]


class Kind(str, Enum):
    """What was found. The severity of each is fixed, not configurable."""

    SEPARATION_VIOLATED = "separation_violated"
    PERMANENT_ELEVATION = "permanent_elevation"
    REPEATED_ELEVATION = "repeated_elevation"
    WILDCARD_GRANT = "wildcard_grant"
    WEAK_ASSURANCE_HIGH_PRIVILEGE = "weak_assurance_high_privilege"
    EXPIRED_NOT_REVOKED = "expired_not_revoked"
    PRIVILEGE_CONCENTRATION = "privilege_concentration"
    ORPHANED_BINDING = "orphaned_binding"
    UNUSED_ROLE = "unused_role"
    DEEP_INHERITANCE = "deep_inheritance"
    DENY_SHADOWED = "deny_shadowed"

    @property
    def severity(self) -> Severity:
        return _SEVERITY[self]


_SEVERITY: dict[Kind, Severity] = {
    Kind.SEPARATION_VIOLATED: Severity.CRITICAL,
    Kind.PERMANENT_ELEVATION: Severity.CRITICAL,
    Kind.REPEATED_ELEVATION: Severity.HIGH,
    Kind.WEAK_ASSURANCE_HIGH_PRIVILEGE: Severity.HIGH,
    Kind.WILDCARD_GRANT: Severity.HIGH,
    Kind.EXPIRED_NOT_REVOKED: Severity.MEDIUM,
    Kind.PRIVILEGE_CONCENTRATION: Severity.MEDIUM,
    Kind.ORPHANED_BINDING: Severity.MEDIUM,
    Kind.UNUSED_ROLE: Severity.LOW,
    Kind.DEEP_INHERITANCE: Severity.LOW,
    Kind.DENY_SHADOWED: Severity.LOW,
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One exception, with the evidence and what to do about it."""

    kind: Kind
    subject: str
    summary: str
    evidence: tuple[str, ...] = ()
    remediation: str = ""

    @property
    def severity(self) -> Severity:
        return self.kind.severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "subject": self.subject,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "remediation": self.remediation,
        }

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.subject}: {self.summary}"


@dataclass(frozen=True, slots=True)
class Report:
    """The whole review, ranked, with the numbers behind it."""

    findings: tuple[Finding, ...] = ()
    roles: int = 0
    principals: int = 0
    bindings: int = 0
    reviewed_at: float = 0.0

    @property
    def clean(self) -> bool:
        return not self.findings

    def of(self, severity: Severity) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is severity)

    def of_kind(self, kind: Kind) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.kind is kind)

    @property
    def blocking(self) -> tuple[Finding, ...]:
        """Findings that should fail a compliance gate rather than be noted."""
        return tuple(f for f in self.findings if f.severity in
                     (Severity.CRITICAL, Severity.HIGH))

    def render(self) -> str:
        """The review as text, ready to attach to a ticket."""
        if self.clean:
            return (
                f"access review: {self.roles} roles, {self.principals} principals, "
                f"{self.bindings} bindings — no exceptions"
            )
        lines = [
            f"access review: {len(self.findings)} exceptions across {self.roles} "
            f"roles and {self.principals} principals"
        ]
        for finding in self.findings:
            lines.append(f"  {finding}")
            for item in finding.evidence:
                lines.append(f"      · {item}")
            if finding.remediation:
                lines.append(f"      → {finding.remediation}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "counts": {
                severity.value: len(self.of(severity)) for severity in Severity
            },
            "roles": self.roles,
            "principals": self.principals,
            "bindings": self.bindings,
            "clean": self.clean,
            "reviewed_at": self.reviewed_at,
        }

    def __str__(self) -> str:
        return self.render()


def review(
    engine: AccessEngine,
    *,
    known_principals: Iterable[str] = (),
    exercised: Mapping[str, float] | None = None,
    now: float | None = None,
) -> Report:
    """Run every check and return the findings, most severe first.

    `exercised` maps a binding's `(principal, role)` to when it was last used;
    supply it from the decision log or from whatever telemetry exists. Without
    it the unused-role check is skipped rather than guessed at — reporting every
    role as unused because nothing was measured would bury the real findings.
    """
    moment = time.time() if now is None else now
    findings: list[Finding] = []
    roles = engine.roles
    live = [
        binding
        for bindings in (engine.bindings_of(urn, now=moment)
                         for urn in _principals(engine))
        for binding in bindings
    ]

    findings.extend(_separations(engine, live, moment))
    findings.extend(_elevations(engine, moment))
    findings.extend(_wildcards(roles))
    findings.extend(_weak_assurance(engine, roles, live))
    findings.extend(_expired(engine, moment))
    findings.extend(_concentration(roles, live))
    findings.extend(_orphans(live, frozenset(known_principals)))
    findings.extend(_unused(live, exercised, moment))
    findings.extend(_depth(roles))
    findings.extend(_shadowed(roles))

    findings.sort(key=lambda finding: (finding.severity.rank, finding.subject))
    return Report(
        findings=tuple(findings),
        roles=len(roles),
        principals=len({binding.principal_urn for binding in live}),
        bindings=len(live),
        reviewed_at=moment,
    )


def _principals(engine: AccessEngine) -> frozenset[str]:
    return frozenset(binding.principal_urn for binding in engine.log)


def _separations(engine: AccessEngine, live: Sequence[Binding],
                 now: float) -> list[Finding]:
    """The check the whole review exists for.

    Evaluated per principal over the inheritance closure, because holding a role
    that inherits `approver` is holding `approver` — and a control that could be
    evaded by adding one level of inheritance is not a control.
    """
    findings: list[Finding] = []
    by_principal: dict[str, set[str]] = {}
    for binding in live:
        by_principal.setdefault(binding.principal_urn, set()).add(binding.role)

    for urn, held in sorted(by_principal.items()):
        for separation, overlap in engine.roles.conflicts(held):
            findings.append(Finding(
                kind=Kind.SEPARATION_VIOLATED,
                subject=urn,
                summary=(
                    f"holds {' and '.join(sorted(overlap))}, which {separation.name} "
                    f"forbids"
                ),
                evidence=(
                    f"directly assigned: {', '.join(sorted(held))}",
                    f"reason for the control: {separation.reason}",
                ),
                remediation=(
                    f"remove one of {', '.join(sorted(overlap))} from this principal, "
                    f"or record a documented exception"
                ),
            ))
    return findings


def _elevations(engine: AccessEngine, now: float) -> list[Finding]:
    """Two checks on break-glass, and the second is the one that fires.

    `PERMANENT_ELEVATION` catches an elevation still live long after its window.
    The engine caps elevations at four hours, so this can only happen when a
    binding was constructed directly with `elevation=True` rather than through
    `elevate()` — it is a defensive check against a code path that bypasses the
    cap, not the common case.

    `REPEATED_ELEVATION` is the common case. Somebody breaking glass to the same
    role every week is not handling emergencies; they need that role, and the
    honest response is to grant it under review rather than to keep pretending
    each time is exceptional. A model where the audit trail is full of
    emergencies is a model that has stopped meaning anything.
    """
    findings: list[Finding] = []

    for urn in sorted(_principals(engine)):
        for binding in engine.bindings_of(urn, now=now):
            if not binding.elevation:
                continue
            age = now - binding.granted_at
            if age > MAX_ELEVATION:
                findings.append(Finding(
                    kind=Kind.PERMANENT_ELEVATION,
                    subject=urn,
                    summary=(
                        f"break-glass role {binding.role!r} still live after "
                        f"{age / 3600:.0f} hours"
                    ),
                    evidence=(
                        f"granted: {binding.reason or 'no reason recorded'}",
                        "this binding bypassed the elevation cap",
                    ),
                    remediation="revoke it, and find the code path that created it",
                ))

    counts: dict[tuple[str, str], list[Binding]] = {}
    for binding in engine.log:
        if binding.elevation and not binding.revoked_at:
            counts.setdefault((binding.principal_urn, binding.role), []).append(binding)

    for (urn, role), occurrences in sorted(counts.items()):
        if len(occurrences) < REPEATED_ELEVATIONS:
            continue
        findings.append(Finding(
            kind=Kind.REPEATED_ELEVATION,
            subject=urn,
            summary=f"elevated to {role!r} {len(occurrences)} times",
            evidence=tuple(
                f"{binding.reason or 'no reason recorded'}"
                for binding in occurrences[:5]
            ),
            remediation=(
                f"grant {role!r} as a reviewed standing role, or narrow it so the "
                f"emergency stops recurring"
            ),
        ))
    return findings


def _wildcards(roles: RoleGraph) -> list[Finding]:
    findings: list[Finding] = []
    for role in roles:
        broad = role.wildcards
        if not broad:
            continue
        findings.append(Finding(
            kind=Kind.WILDCARD_GRANT,
            subject=role.name,
            summary=f"grants {len(broad)} wildcard permission(s)",
            evidence=tuple(str(permission) for permission in broad),
            remediation=(
                "narrow the resource pattern, or accept it explicitly as an "
                "administrative role"
            ),
        ))
    return findings


def _weak_assurance(engine: AccessEngine, roles: RoleGraph,
                    live: Sequence[Binding]) -> list[Finding]:
    """Roles that reach far but can be exercised from a weak sign-in.

    The dangerous combination is a wildcard or write permission whose required
    assurance is WEAK — a magic link reaching production. Reported against the
    role rather than the person, because that is where the fix belongs.
    """
    findings: list[Finding] = []
    for role in roles:
        if role.assurance.satisfies(Assurance.STRONG):
            continue
        risky = [
            permission for permission in roles.effective_permissions(role.name)
            if permission.allows
            and permission.assurance is Assurance.WEAK
            and (permission.is_wildcard or _is_write(permission.action))
        ]
        if risky:
            findings.append(Finding(
                kind=Kind.WEAK_ASSURANCE_HIGH_PRIVILEGE,
                subject=role.name,
                summary=(
                    f"grants {len(risky)} far-reaching permission(s) at WEAK assurance"
                ),
                evidence=tuple(str(permission) for permission in risky),
                remediation=(
                    "raise the role's assurance to STANDARD or STRONG so a magic "
                    "link or SMS code cannot exercise it"
                ),
            ))
    return findings


def _is_write(action: str) -> bool:
    return any(
        token in action.lower()
        for token in ("write", "delete", "apply", "deploy", "grant", "admin", "destroy")
    )


def _expired(engine: AccessEngine, now: float) -> list[Finding]:
    """Lapsed bindings that were never revoked.

    They grant nothing — the engine checks expiry — but they are invisible to a
    directory export, so a review that reads the directory concludes the person
    still has the role. Reported so the record matches reality.
    """
    findings: list[Finding] = []
    for binding in engine.log:
        if binding.revoked_at or not binding.expires_at:
            continue
        if binding.expires_at < now and binding.active:
            findings.append(Finding(
                kind=Kind.EXPIRED_NOT_REVOKED,
                subject=binding.principal_urn,
                summary=f"{binding.role!r} expired but was never revoked",
                evidence=(f"expired {(now - binding.expires_at) / 86400:.0f} days ago",),
                remediation="revoke it so the record matches what the engine enforces",
            ))
    return findings


def _concentration(roles: RoleGraph, live: Sequence[Binding]) -> list[Finding]:
    if len(roles) < 4:
        return []
    findings: list[Finding] = []
    by_principal: dict[str, set[str]] = {}
    for binding in live:
        by_principal.setdefault(binding.principal_urn, set()).add(binding.role)

    for urn, held in sorted(by_principal.items()):
        share = len(roles.closure(held)) / len(roles)
        if share > CONCENTRATION:
            findings.append(Finding(
                kind=Kind.PRIVILEGE_CONCENTRATION,
                subject=urn,
                summary=f"holds {share:.0%} of every defined role",
                evidence=(f"effective roles: {', '.join(sorted(roles.closure(held)))}",),
                remediation="split the responsibilities, or confirm this is a break-glass account",
            ))
    return findings


def _orphans(live: Sequence[Binding], known: frozenset[str]) -> list[Finding]:
    if not known:
        # Nothing to compare against. Reporting every binding as orphaned
        # because no directory was supplied would be noise, not a finding.
        return []
    return [
        Finding(
            kind=Kind.ORPHANED_BINDING,
            subject=binding.principal_urn,
            summary=f"holds {binding.role!r} but is not in the directory",
            evidence=(f"granted by {binding.granted_by or 'unknown'}",),
            remediation="revoke it — a role held by nobody identifiable is a live credential",
        )
        for binding in live if binding.principal_urn not in known
    ]


def _unused(live: Sequence[Binding], exercised: Mapping[str, float] | None,
            now: float) -> list[Finding]:
    if exercised is None:
        return []
    findings: list[Finding] = []
    for binding in live:
        key = f"{binding.principal_urn}|{binding.role}"
        last = exercised.get(key)
        age = now - binding.granted_at
        if last is None and age > UNUSED_AFTER:
            findings.append(Finding(
                kind=Kind.UNUSED_ROLE,
                subject=binding.principal_urn,
                summary=f"{binding.role!r} granted {age / 86400:.0f} days ago and never used",
                evidence=("no recorded decision has been made against it",),
                remediation="revoke it; an unused grant is pure attack surface",
            ))
    return findings


def _depth(roles: RoleGraph) -> list[Finding]:
    return [
        Finding(
            kind=Kind.DEEP_INHERITANCE,
            subject=role.name,
            summary=f"inherits {len(roles.ancestors(role.name))} roles transitively",
            evidence=(f"ancestors: {', '.join(sorted(roles.ancestors(role.name)))}",),
            remediation="flatten the chain so its effective permissions can be read",
        )
        for role in roles
        if len(roles.ancestors(role.name)) > DEEP_INHERITANCE
    ]


def _shadowed(roles: RoleGraph) -> list[Finding]:
    """Denies that no allow in the same role could ever have reached.

    Harmless, but a deny written against a permission that does not exist means
    somebody believed a grant was there. Either the deny is misspelt or the
    grant was removed and the deny left behind — both worth knowing.
    """
    findings: list[Finding] = []
    for role in roles:
        permissions = roles.effective_permissions(role.name)
        allows = [p for p in permissions if p.allows]
        for refusal in (p for p in permissions if p.effect is Effect.DENY):
            reachable = any(
                permission.matches(refusal.action.rstrip(".*") or refusal.action,
                                   refusal.resource.replace("**", "x").replace("*", "x"))
                for permission in allows
            )
            if not reachable and allows:
                findings.append(Finding(
                    kind=Kind.DENY_SHADOWED,
                    subject=role.name,
                    summary=f"rule '{refusal}' can never fire",
                    evidence=("no allow rule in this role reaches that action and resource",),
                    remediation="remove it, or fix the allow it was meant to constrain",
                ))
    return findings
