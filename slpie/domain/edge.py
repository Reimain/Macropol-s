"""Edges — the relationships, and the platform's central invariant.

**No relationship may exist without supporting evidence.** That is enforced
structurally here, not by review: :meth:`Edge.__post_init__` refuses to
construct an edge with an empty evidence tuple, and ``confidence`` is a
read-only property derived from that evidence. There is no code path that
produces an unjustified relationship, because there is no constructor for one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from ..errors import EvidenceRequired, SchemaViolation
from .evidence import (
    Confidence,
    ConfidenceDerivation,
    Evidence,
    ValidationStatus,
    strongest,
)
from .identity import edge_id
from .node import OPEN


class EdgeKind(str, Enum):
    """How two things relate. Closed vocabulary; every kind has a direction."""

    DEPENDS_ON = "depends_on"
    IMPORTS = "imports"
    EXPORTS = "exports"
    CALLS = "calls"
    OWNS = "owns"
    PUBLISHES = "publishes"
    SUBSCRIBES = "subscribes"
    READS = "reads"
    WRITES = "writes"
    DEPLOYS_TO = "deploys_to"
    AUTHENTICATES_WITH = "authenticates_with"
    SECURES = "secures"
    VALIDATES = "validates"
    TRANSFORMS = "transforms"
    GENERATES = "generates"
    REFERENCES = "references"
    MIRRORS = "mirrors"
    SUPERSEDES = "supersedes"
    REPLACES = "replaces"
    GOVERNS = "governs"

    @property
    def propagates_impact(self) -> bool:
        """Whether a change at the target can break the source.

        This is what blast radius traverses. `owns` and `governs` are
        organisational, not mechanical — a team changing does not break code, so
        following them would inflate every impact analysis with noise.
        """
        return self in _PROPAGATES

    @property
    def is_structural(self) -> bool:
        """Relationships that define the dependency skeleton."""
        return self in (EdgeKind.DEPENDS_ON, EdgeKind.IMPORTS, EdgeKind.EXPORTS)

    @property
    def inverse(self) -> "EdgeKind | None":
        return _INVERSE.get(self)


_PROPAGATES = frozenset({
    EdgeKind.DEPENDS_ON, EdgeKind.IMPORTS, EdgeKind.CALLS, EdgeKind.READS,
    EdgeKind.WRITES, EdgeKind.SUBSCRIBES, EdgeKind.PUBLISHES, EdgeKind.DEPLOYS_TO,
    EdgeKind.AUTHENTICATES_WITH, EdgeKind.VALIDATES, EdgeKind.TRANSFORMS,
    EdgeKind.REFERENCES,
})

_INVERSE: dict[EdgeKind, EdgeKind] = {
    EdgeKind.IMPORTS: EdgeKind.EXPORTS,
    EdgeKind.EXPORTS: EdgeKind.IMPORTS,
    EdgeKind.PUBLISHES: EdgeKind.SUBSCRIBES,
    EdgeKind.SUBSCRIBES: EdgeKind.PUBLISHES,
    EdgeKind.SUPERSEDES: EdgeKind.REPLACES,
    EdgeKind.REPLACES: EdgeKind.SUPERSEDES,
}


@dataclass(frozen=True, slots=True)
class Edge:
    """A relationship between two nodes, and the evidence that justifies it."""

    kind: EdgeKind
    src: str                       # node id
    dst: str                       # node id
    evidence: tuple[Evidence, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)
    qualifier: str = ""            # distinguishes parallel edges (e.g. "dev", "optional")

    valid_from: int = 0
    valid_to: int = OPEN
    observed_at: int = 0
    superseded_at: int = OPEN

    contradicted: bool = False

    def __post_init__(self) -> None:
        if not self.evidence:
            raise EvidenceRequired(f"{self.src} -{self.kind.value}-> {self.dst}")
        if not self.src or not self.dst:
            raise SchemaViolation("an edge needs both endpoints")

    # -- identity --------------------------------------------------------

    @property
    def id(self) -> str:
        return edge_id(self.kind.value, self.src, self.dst, qualifier=self.qualifier)

    # -- derived state ---------------------------------------------------

    @property
    def confidence(self) -> float:
        """Derived from evidence. Deliberately a property, so it cannot be assigned."""
        return Confidence.combine(self.evidence, subject=self.id)

    @property
    def derivation(self) -> ConfidenceDerivation:
        """The audit trail for this edge's confidence, for explanations and the UI."""
        return Confidence.explain(self.evidence, subject=self.id)

    @property
    def validation(self) -> ValidationStatus:
        return Confidence.status(self.evidence, contradicted=self.contradicted)

    @property
    def live(self) -> bool:
        return self.valid_to == OPEN and self.superseded_at == OPEN

    @property
    def primary_evidence(self) -> Evidence:
        """The strongest observation — what an explanation leads with."""
        best = strongest(self.evidence)
        assert best is not None  # guaranteed by __post_init__
        return best

    # -- transitions (all return new edges) ------------------------------

    def corroborated_by(self, *evidence: Evidence) -> "Edge":
        merged = {e.id: e for e in (*self.evidence, *evidence)}
        return replace(self, evidence=tuple(merged.values()))

    def retired_at(self, when: int) -> "Edge":
        return replace(self, valid_to=when)

    def superseded(self, when: int) -> "Edge":
        return replace(self, superseded_at=when)

    def contradict(self) -> "Edge":
        return replace(self, contradicted=True)

    def without(self, *evidence_ids: str) -> "Edge | None":
        """Drop evidence (because its source changed). None when nothing remains.

        This is the incremental engine's core move: a file changes, its evidence
        goes stale, and an edge that had no other support ceases to be justified
        — so it ceases to exist, rather than lingering as a stale assertion.
        """
        dropped = set(evidence_ids)
        remaining = tuple(e for e in self.evidence if e.id not in dropped)
        return replace(self, evidence=remaining) if remaining else None

    # -- serialisation ---------------------------------------------------

    def to_dict(self, *, include_evidence: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "src": self.src,
            "dst": self.dst,
            "qualifier": self.qualifier,
            "properties": dict(self.properties),
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
            out["derivation"] = self.derivation.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], evidence: Sequence[Evidence]) -> "Edge":
        return cls(
            kind=EdgeKind(data["kind"]),
            src=data["src"],
            dst=data["dst"],
            evidence=tuple(evidence),
            properties=dict(data.get("properties", {})),
            qualifier=data.get("qualifier", ""),
            valid_from=int(data.get("valid_from", 0)),
            valid_to=int(data.get("valid_to", OPEN)),
            observed_at=int(data.get("observed_at", 0)),
            superseded_at=int(data.get("superseded_at", OPEN)),
            contradicted=bool(data.get("contradicted", False)),
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<Edge {self.src[:8]} -{self.kind.value}-> {self.dst[:8]} "
            f"conf={self.confidence:.2f} via {self.primary_evidence.location.reference}>"
        )
