"""Classification axes carried by every node.

These are deliberately separate enums rather than one status field. A component
can be architecturally CORE, lifecycle DEPRECATED, and compliance VIOLATING all
at once — collapsing them into a single label loses exactly the information a
governance report needs.
"""

from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    """Where something sits in its own lifetime."""

    PROPOSED = "proposed"      # declared but not yet observed
    ACTIVE = "active"
    DEPRECATED = "deprecated"  # still present, should not gain new dependents
    RETIRED = "retired"        # no longer present
    UNKNOWN = "unknown"

    @property
    def accepts_new_dependents(self) -> bool:
        return self in (LifecycleState.PROPOSED, LifecycleState.ACTIVE)


class RiskClass(str, Enum):
    """Aggregate risk, derived from findings — never hand-set."""

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]

    @classmethod
    def from_rank(cls, rank: int) -> "RiskClass":
        return _BY_RANK[max(0, min(rank, 4))]


_RISK_RANK = {
    RiskClass.NONE: 0, RiskClass.LOW: 1, RiskClass.MODERATE: 2,
    RiskClass.HIGH: 3, RiskClass.CRITICAL: 4,
}
_BY_RANK = {v: k for k, v in _RISK_RANK.items()}


class ComplianceState(str, Enum):
    COMPLIANT = "compliant"
    EXEMPT = "exempt"            # a violation with a recorded, referenced waiver
    VIOLATING = "violating"
    UNASSESSED = "unassessed"    # no policy has been evaluated against it yet


class ArchitectureClass(str, Enum):
    """Domain-Driven Design classification — what kind of thing this is to the business."""

    CORE = "core"                # competitive advantage lives here
    SUPPORTING = "supporting"    # necessary, not differentiating
    GENERIC = "generic"          # should probably be bought, not built
    EXPERIMENTAL = "experimental"
    LEGACY = "legacy"
    UNCLASSIFIED = "unclassified"


class Severity(str, Enum):
    """Finding severity."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def blocks_release(self) -> bool:
        return self in (Severity.HIGH, Severity.CRITICAL)

    def to_risk(self) -> RiskClass:
        return RiskClass.from_rank(self.rank)


_SEVERITY_RANK = {
    Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
    Severity.HIGH: 3, Severity.CRITICAL: 4,
}
