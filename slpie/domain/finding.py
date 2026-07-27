"""Findings — what governance produces, and gaps — what it admits it cannot see.

Two types live here because they are two halves of one honest answer. A
:class:`Finding` says *this is wrong*. A :class:`Gap` says *this is what I could
not check*. A platform that emits only the first is claiming a completeness it
does not have — the second is what turns "no findings" from a reassurance into a
statement with a known scope.

Every finding carries evidence and a remediation. A finding without evidence is
an opinion, and a finding without a remediation is a complaint.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import EvidenceRequired
from .evidence import Evidence, SourceLocation
from .identity import digest
from .lifecycle import RiskClass, Severity


class FindingKind(str, Enum):
    """The closed vocabulary of things governance can say is wrong."""

    # dependency health
    VULNERABLE_DEPENDENCY = "vulnerable_dependency"
    DEPRECATED_PACKAGE = "deprecated_package"
    UNMAINTAINED_PACKAGE = "unmaintained_package"
    OUTDATED_MAJOR = "outdated_major"
    UNPINNED_DEPENDENCY = "unpinned_dependency"
    DUPLICATE_VERSIONS = "duplicate_versions"
    # constraint and structure
    CONSTRAINT_CONFLICT = "constraint_conflict"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    UNSUPPORTED_RUNTIME = "unsupported_runtime"
    PHANTOM_DEPENDENCY = "phantom_dependency"   # imported, never declared
    UNUSED_DEPENDENCY = "unused_dependency"     # declared, never imported
    # licence and supply chain
    LICENSE_INCOMPATIBLE = "license_incompatible"
    LICENSE_UNKNOWN = "license_unknown"
    SUPPLY_CHAIN_RISK = "supply_chain_risk"
    TYPOSQUAT_SUSPECT = "typosquat_suspect"
    INTEGRITY_MISMATCH = "integrity_mismatch"
    # security
    SECRET_EXPOSED = "secret_exposed"
    BOUNDARY_VIOLATION = "boundary_violation"
    INSECURE_TRANSPORT = "insecure_transport"
    MISSING_AUTHENTICATION = "missing_authentication"
    # reconciliation — the declare-first deltas
    DECLARED_NOT_FOUND = "declared_not_found"
    UNDECLARED_ELEMENT = "undeclared_element"
    CONTRADICTED = "contradicted"
    # architecture
    LAYER_VIOLATION = "layer_violation"
    ORPHANED_COMPONENT = "orphaned_component"
    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"
    UNDOCUMENTED_INTERFACE = "undocumented_interface"
    SCHEMA_DRIFT = "schema_drift"
    # policy
    POLICY_VIOLATION = "policy_violation"

    @property
    def family(self) -> str:
        """Grouping for the Findings view and for report sections."""
        return _FAMILY.get(self, "other")

    @property
    def is_reconciliation(self) -> bool:
        """Deltas between what was declared and what was observed."""
        return self in (
            FindingKind.DECLARED_NOT_FOUND,
            FindingKind.UNDECLARED_ELEMENT,
            FindingKind.CONTRADICTED,
        )


_FAMILY: dict[FindingKind, str] = {
    FindingKind.VULNERABLE_DEPENDENCY: "security",
    FindingKind.SECRET_EXPOSED: "security",
    FindingKind.BOUNDARY_VIOLATION: "security",
    FindingKind.INSECURE_TRANSPORT: "security",
    FindingKind.MISSING_AUTHENTICATION: "security",
    FindingKind.SUPPLY_CHAIN_RISK: "supply-chain",
    FindingKind.TYPOSQUAT_SUSPECT: "supply-chain",
    FindingKind.INTEGRITY_MISMATCH: "supply-chain",
    FindingKind.UNMAINTAINED_PACKAGE: "supply-chain",
    FindingKind.LICENSE_INCOMPATIBLE: "compliance",
    FindingKind.LICENSE_UNKNOWN: "compliance",
    FindingKind.POLICY_VIOLATION: "compliance",
    FindingKind.DEPRECATED_PACKAGE: "dependency-health",
    FindingKind.OUTDATED_MAJOR: "dependency-health",
    FindingKind.UNPINNED_DEPENDENCY: "dependency-health",
    FindingKind.DUPLICATE_VERSIONS: "dependency-health",
    FindingKind.CONSTRAINT_CONFLICT: "resolution",
    FindingKind.CIRCULAR_DEPENDENCY: "resolution",
    FindingKind.UNSUPPORTED_RUNTIME: "resolution",
    FindingKind.PHANTOM_DEPENDENCY: "resolution",
    FindingKind.UNUSED_DEPENDENCY: "resolution",
    FindingKind.DECLARED_NOT_FOUND: "reconciliation",
    FindingKind.UNDECLARED_ELEMENT: "reconciliation",
    FindingKind.CONTRADICTED: "reconciliation",
    FindingKind.LAYER_VIOLATION: "architecture",
    FindingKind.ORPHANED_COMPONENT: "architecture",
    FindingKind.SINGLE_POINT_OF_FAILURE: "architecture",
    FindingKind.UNDOCUMENTED_INTERFACE: "architecture",
    FindingKind.SCHEMA_DRIFT: "architecture",
}


class GapKind(str, Enum):
    """Why part of the picture is missing. Each maps to something actionable."""

    CAPABILITY_REFUSED = "capability_refused"      # the element would not let us look
    NOT_DECLARED = "not_declared"                  # absent from the environment manifest
    NOT_ATTACHED = "not_attached"                  # declared but never registered
    STALE_HEARTBEAT = "stale_heartbeat"            # attached, but silent
    LOW_CONFIDENCE = "low_confidence"              # the answer path rests on weak evidence
    UNRESOLVED_DEPENDENCY = "unresolved_dependency"
    PLUGIN_UNAVAILABLE = "plugin_unavailable"      # no analyser for this language
    PLUGIN_QUARANTINED = "plugin_quarantined"
    SIMULATED_ONLY = "simulated_only"              # observed in the simulator, never live
    PARSE_FAILURE = "parse_failure"
    ACCESS_DENIED = "access_denied"
    NOT_IMPLEMENTED = "not_implemented"

    @property
    def resolvable_by_user(self) -> bool:
        """Whether the operator can close this gap themselves.

        Drives the difference between "attach the payments repo" (actionable, and
        offered as a suggested action) and "no Rust analyser is installed"
        (a platform limitation, reported but not asked of the user).
        """
        return self in (
            GapKind.NOT_DECLARED, GapKind.NOT_ATTACHED, GapKind.CAPABILITY_REFUSED,
            GapKind.ACCESS_DENIED, GapKind.SIMULATED_ONLY, GapKind.STALE_HEARTBEAT,
        )


@dataclass(frozen=True, slots=True)
class Gap:
    """Something the platform could not see, attached to every answer it limits.

    This is what separates a low-confidence answer from a misleading one. The
    answer still comes back; it comes back saying which part of the ecosystem it
    could not inspect and what would close that.
    """

    kind: GapKind
    subject: str                      # node id, element name, or path
    detail: str
    remediation: str = ""
    confidence_impact: float = 0.0    # how much certainty this costs, 0..1
    location: SourceLocation | None = None

    @property
    def id(self) -> str:
        return digest({"kind": self.kind.value, "subject": self.subject, "detail": self.detail})

    @property
    def actionable(self) -> bool:
        return self.kind.resolvable_by_user and bool(self.remediation)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "subject": self.subject,
            "detail": self.detail,
            "remediation": self.remediation,
            "confidence_impact": self.confidence_impact,
            "actionable": self.actionable,
        }
        if self.location is not None:
            out["location"] = self.location.to_dict()
        return out

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.subject} — {self.detail}"


@dataclass(frozen=True, slots=True)
class Remediation:
    """What to do about a finding, and what doing it would cost.

    ``breaking`` and ``blast_radius`` are why this is a type rather than a
    string: "upgrade to 4.17.21" and "upgrade to 5.0.0, which changes 340 call
    sites" are not the same recommendation.
    """

    summary: str
    action: str = ""                   # machine-actionable: "upgrade", "pin", "replace", "declare"
    target: str = ""                   # the version, package, or declaration to move to
    breaking: bool = False
    blast_radius: int = 0              # nodes affected if applied
    automated: bool = False            # whether SLPIE can apply it

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary, "action": self.action, "target": self.target,
            "breaking": self.breaking, "blast_radius": self.blast_radius,
            "automated": self.automated,
        }

    def __str__(self) -> str:
        return self.summary


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing that is wrong, with the evidence that shows it.

    Findings are content-addressed on ``(kind, subject, code)`` so the same
    condition rediscovered on the next scan is the *same* finding rather than a
    duplicate — which is what lets suppression, ageing and "new since baseline"
    work at all.
    """

    kind: FindingKind
    severity: Severity
    subject: str                                    # node id or identity string
    title: str
    detail: str = ""
    code: str = ""                                  # CVE / GHSA / rule id
    evidence: tuple[Evidence, ...] = ()
    remediation: Remediation | None = None
    rule_id: str = ""
    rule_digest: str = ""                           # the rule's own fingerprint
    related: tuple[str, ...] = ()                   # other node ids implicated
    properties: Mapping[str, Any] = field(default_factory=dict)
    raised_at: int = 0
    suppressed: bool = False
    suppression_reason: str = ""

    def __post_init__(self) -> None:
        if not self.evidence:
            raise EvidenceRequired(f"finding {self.kind.value} on {self.subject}")
        if not self.subject:
            raise ValueError("a finding needs a subject")

    @property
    def id(self) -> str:
        """Stable across scans: the same condition yields the same id."""
        return digest({
            "kind": self.kind.value,
            "subject": self.subject,
            "code": self.code,
            "rule_id": self.rule_id,
        })

    @property
    def family(self) -> str:
        return self.kind.family

    @property
    def risk(self) -> RiskClass:
        return RiskClass.NONE if self.suppressed else self.severity.to_risk()

    @property
    def blocks_release(self) -> bool:
        return not self.suppressed and self.severity.blocks_release

    @property
    def location(self) -> SourceLocation | None:
        """Where a human should look first."""
        return self.evidence[0].location if self.evidence else None

    def suppress(self, reason: str) -> "Finding":
        """Waive a finding — with a recorded reason, never silently.

        The finding stays in the ledger and in reports marked as waived. A
        suppression that erased the finding would make the compliance history
        unauditable, which is the opposite of the point.
        """
        if not reason:
            raise ValueError("a suppression must record why")
        return replace(self, suppressed=True, suppression_reason=reason)

    def escalate(self, severity: Severity) -> "Finding":
        return replace(self, severity=severity)

    def with_remediation(self, remediation: Remediation) -> "Finding":
        return replace(self, remediation=remediation)

    def to_dict(self, *, include_evidence: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "family": self.family,
            "severity": self.severity.value,
            "risk": self.risk.value,
            "subject": self.subject,
            "title": self.title,
            "detail": self.detail,
            "code": self.code,
            "rule_id": self.rule_id,
            "rule_digest": self.rule_digest,
            "related": list(self.related),
            "properties": dict(self.properties),
            "raised_at": self.raised_at,
            "suppressed": self.suppressed,
            "suppression_reason": self.suppression_reason,
            "blocks_release": self.blocks_release,
            "evidence_ids": [e.id for e in self.evidence],
        }
        if self.remediation is not None:
            out["remediation"] = self.remediation.to_dict()
        if self.location is not None:
            out["location"] = self.location.to_dict()
        if include_evidence:
            out["evidence"] = [e.to_dict() for e in self.evidence]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], evidence: Sequence[Evidence]) -> "Finding":
        remediation_data = data.get("remediation")
        return cls(
            kind=FindingKind(data["kind"]),
            severity=Severity(data["severity"]),
            subject=data["subject"],
            title=data["title"],
            detail=data.get("detail", ""),
            code=data.get("code", ""),
            evidence=tuple(evidence),
            remediation=(
                Remediation(**remediation_data) if isinstance(remediation_data, Mapping) else None
            ),
            rule_id=data.get("rule_id", ""),
            rule_digest=data.get("rule_digest", ""),
            related=tuple(data.get("related", ())),
            properties=dict(data.get("properties", {})),
            raised_at=int(data.get("raised_at", 0)),
            suppressed=bool(data.get("suppressed", False)),
            suppression_reason=data.get("suppression_reason", ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        mark = " (waived)" if self.suppressed else ""
        return f"<Finding {self.severity.value}/{self.kind.value} {self.subject}{mark}>"


def aggregate_risk(findings: Iterable[Finding]) -> RiskClass:
    """The risk class a node inherits from its findings — highest unwaived wins."""
    ranks = [f.risk.rank for f in findings if not f.suppressed]
    return RiskClass.from_rank(max(ranks)) if ranks else RiskClass.NONE


def rank(findings: Iterable[Finding]) -> list[Finding]:
    """Sort for presentation: severity first, then blast radius, then stable id.

    Waived findings sink to the bottom rather than disappearing.
    """
    return sorted(
        findings,
        key=lambda f: (
            f.suppressed,
            -f.severity.rank,
            -(f.remediation.blast_radius if f.remediation else 0),
            f.id,
        ),
    )


def merge_gaps(*groups: Iterable[Gap]) -> tuple[Gap, ...]:
    """Deduplicate gaps across contributors, keeping the worst impact for each."""
    merged: dict[str, Gap] = {}
    for group in groups:
        for gap in group:
            existing = merged.get(gap.id)
            if existing is None or gap.confidence_impact > existing.confidence_impact:
                merged[gap.id] = gap
    return tuple(
        sorted(merged.values(), key=lambda g: (-g.confidence_impact, g.kind.value, g.subject))
    )
