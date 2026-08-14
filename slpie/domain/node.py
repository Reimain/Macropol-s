"""Nodes — the things an ecosystem is made of.

A node is bitemporal: ``valid_from``/``valid_to`` record when it was true in the
ecosystem, ``observed_at``/``superseded_at`` record when we learned it. Two
independent axes are what make "complete historical evolution" answerable —
"what did production look like in March" and "what did we *believe* production
looked like in March" are different questions, and both get asked.

Like edges, a node cannot exist without evidence. A node asserted with no
justification is a guess wearing a schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from ..errors import EvidenceRequired
from .evidence import Confidence, Evidence, ValidationStatus
from .identity import Identity, Purl, Urn, node_id, parse_identity
from .lifecycle import ArchitectureClass, ComplianceState, LifecycleState, RiskClass


class NodeKind(str, Enum):
    """What a node is. The vocabulary is closed so the graph stays typed."""

    # code and packaging
    REPOSITORY = "repository"
    WORKSPACE = "workspace"
    PACKAGE = "package"
    MODULE = "module"
    COMPONENT = "component"
    # runtime
    SERVICE = "service"
    API = "api"
    EVENT = "event"
    QUEUE = "queue"
    RUNTIME_PROCESS = "runtime_process"
    WEB_APP = "web_app"
    DEVICE_CLASS = "device_class"
    # data
    DATABASE = "database"
    TABLE = "table"
    ENTITY = "entity"
    SCHEMA = "schema"
    DATASET = "dataset"
    AI_MODEL = "ai_model"
    # delivery and infrastructure
    ENVIRONMENT = "environment"
    PIPELINE = "pipeline"
    DEPLOYMENT = "deployment"
    CLOUD_RESOURCE = "cloud_resource"
    CONFIGURATION = "configuration"
    FEATURE_FLAG = "feature_flag"
    SECRET = "secret"
    # organisation and governance
    TEAM = "team"
    DOMAIN = "domain"
    POLICY = "policy"
    LICENSE = "license"
    EXTERNAL_PROVIDER = "external_provider"

    @property
    def is_deployable(self) -> bool:
        return self in _DEPLOYABLE

    @property
    def is_data(self) -> bool:
        return self in _DATA

    @property
    def carries_version(self) -> bool:
        """Kinds for which a version is meaningful — drives constraint solving."""
        return self in _VERSIONED


_DEPLOYABLE = frozenset({
    NodeKind.SERVICE, NodeKind.WEB_APP, NodeKind.RUNTIME_PROCESS,
    NodeKind.CLOUD_RESOURCE, NodeKind.DEPLOYMENT,
})
_DATA = frozenset({
    NodeKind.DATABASE, NodeKind.TABLE, NodeKind.ENTITY, NodeKind.SCHEMA,
    NodeKind.DATASET, NodeKind.AI_MODEL,
})
_VERSIONED = frozenset({
    NodeKind.PACKAGE, NodeKind.API, NodeKind.SCHEMA, NodeKind.AI_MODEL,
    NodeKind.DEPLOYMENT,
})

#: Sentinel for "still true". Comparisons stay simple with an open upper bound.
OPEN = 0


@dataclass(frozen=True, slots=True)
class Node:
    """One thing in the ecosystem, at one point in its recorded history."""

    kind: NodeKind
    identity: Identity
    evidence: tuple[Evidence, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    lifecycle: LifecycleState = LifecycleState.UNKNOWN
    risk: RiskClass = RiskClass.NONE
    compliance: ComplianceState = ComplianceState.UNASSESSED
    architecture: ArchitectureClass = ArchitectureClass.UNCLASSIFIED

    valid_from: int = 0
    valid_to: int = OPEN
    observed_at: int = 0
    superseded_at: int = OPEN

    contradicted: bool = False

    def __post_init__(self) -> None:
        if not self.evidence:
            raise EvidenceRequired(f"{self.kind.value} {self.identity}")

    # -- identity --------------------------------------------------------

    @property
    def id(self) -> str:
        return node_id(self.identity)

    @property
    def name(self) -> str:
        if isinstance(self.identity, Purl):
            return self.identity.name
        return self.identity.name

    @property
    def version(self) -> str:
        return self.identity.version if isinstance(self.identity, Purl) else ""

    @property
    def coordinate(self) -> str:
        """Version-independent identity — what "the same thing" means over time."""
        if isinstance(self.identity, Purl):
            return self.identity.coordinate
        return self.identity.to_string()

    @property
    def display(self) -> str:
        return self.identity.display

    # -- derived state ---------------------------------------------------

    @property
    def confidence(self) -> float:
        """Derived from evidence. There is deliberately no setter."""
        return Confidence.combine(self.evidence, subject=str(self.identity))

    @property
    def validation(self) -> ValidationStatus:
        return Confidence.status(self.evidence, contradicted=self.contradicted)

    @property
    def live(self) -> bool:
        return self.valid_to == OPEN and self.superseded_at == OPEN

    @property
    def declared_only(self) -> bool:
        """True when nothing has ever corroborated the declaration.

        This is what `DECLARED_NOT_FOUND` is built on — the manifest said it
        exists and discovery never met it.
        """
        from .evidence import EvidenceKind

        return all(e.kind is EvidenceKind.DECLARED for e in self.evidence)

    # -- transitions (all return new nodes) ------------------------------

    def corroborated_by(self, *evidence: Evidence) -> "Node":
        """Fold in independent evidence; confidence follows automatically."""
        merged = {e.id: e for e in (*self.evidence, *evidence)}
        return replace(self, evidence=tuple(merged.values()))

    def retired_at(self, when: int) -> "Node":
        return replace(self, valid_to=when, lifecycle=LifecycleState.RETIRED)

    def superseded(self, when: int) -> "Node":
        return replace(self, superseded_at=when)

    def classified(
        self,
        *,
        lifecycle: LifecycleState | None = None,
        risk: RiskClass | None = None,
        compliance: ComplianceState | None = None,
        architecture: ArchitectureClass | None = None,
    ) -> "Node":
        return replace(
            self,
            lifecycle=lifecycle if lifecycle is not None else self.lifecycle,
            risk=risk if risk is not None else self.risk,
            compliance=compliance if compliance is not None else self.compliance,
            architecture=architecture if architecture is not None else self.architecture,
        )

    def with_properties(self, **properties: Any) -> "Node":
        return replace(self, properties={**dict(self.properties), **properties})

    # -- serialisation ---------------------------------------------------

    def to_dict(self, *, include_evidence: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "identity": self.identity.to_string(),
            "name": self.name,
            "version": self.version,
            "display": self.display,
            "properties": dict(self.properties),
            "lifecycle": self.lifecycle.value,
            "risk": self.risk.value,
            "compliance": self.compliance.value,
            "architecture": self.architecture.value,
            "confidence": self.confidence,
            "validation": self.validation.value,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "observed_at": self.observed_at,
            "superseded_at": self.superseded_at,
            "live": self.live,
            "evidence_ids": [e.id for e in self.evidence],
        }
        if include_evidence:
            out["evidence"] = [e.to_dict() for e in self.evidence]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], evidence: Sequence[Evidence]) -> "Node":
        return cls(
            kind=NodeKind(data["kind"]),
            identity=parse_identity(data["identity"]),
            evidence=tuple(evidence),
            properties=dict(data.get("properties", {})),
            lifecycle=LifecycleState(data.get("lifecycle", "unknown")),
            risk=RiskClass(data.get("risk", "none")),
            compliance=ComplianceState(data.get("compliance", "unassessed")),
            architecture=ArchitectureClass(data.get("architecture", "unclassified")),
            valid_from=int(data.get("valid_from", 0)),
            valid_to=int(data.get("valid_to", OPEN)),
            observed_at=int(data.get("observed_at", 0)),
            superseded_at=int(data.get("superseded_at", OPEN)),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Node {self.kind.value} {self.display} conf={self.confidence:.2f}>"
