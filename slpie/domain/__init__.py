"""Domain types — the vocabulary every other layer is written in.

Nothing here touches storage, the network, or the filesystem. These are the
values that flow through discovery, the ledger, the graph, the reasoning
pipeline and the UI unchanged, which is what keeps provenance intact from a
parsed line of a lockfile all the way to a rendered explanation.
"""

from __future__ import annotations

from .edge import Edge, EdgeKind
from .evidence import (
    HEURISTIC_CEILING,
    INFERENCE_CEILING,
    SETTLED,
    Confidence,
    ConfidenceDerivation,
    Evidence,
    EvidenceKind,
    SourceLocation,
    ValidationStatus,
    strongest,
)
from .finding import (
    Finding,
    FindingKind,
    Gap,
    GapKind,
    Remediation,
    aggregate_risk,
    merge_gaps,
    rank,
)
from .identity import (
    Identity,
    Purl,
    Urn,
    digest,
    edge_id,
    node_id,
    parse_identity,
)
from .license import (
    CompatibilityVerdict,
    Distribution,
    LicenseExpression,
    LicenseTerm,
    Linkage,
    Obligation,
    canonical_id,
    check_compatibility,
    combined_obligation,
    normalize,
    obligation_of,
    parse_expression,
    spdx_ids,
)
from .lifecycle import (
    ArchitectureClass,
    ComplianceState,
    LifecycleState,
    RiskClass,
    Severity,
)
from .node import OPEN, Node, NodeKind
from .reasoning import (
    ActionKind,
    Clarification,
    Enrichment,
    Guidance,
    LayerNumber,
    Option,
    Outcome,
    Question,
    ReasoningPath,
    ReasoningStep,
    SuggestedAction,
    rank_questions,
    trace_to_evidence,
)
from .version import Comparator, Op, Version, VersionRange, parse_range

__all__ = [
    # identity
    "Identity", "Purl", "Urn", "digest", "edge_id", "node_id", "parse_identity",
    # evidence
    "Confidence", "ConfidenceDerivation", "Evidence", "EvidenceKind", "SourceLocation",
    "ValidationStatus", "strongest", "SETTLED", "HEURISTIC_CEILING", "INFERENCE_CEILING",
    # graph elements
    "Node", "NodeKind", "Edge", "EdgeKind", "OPEN",
    # classification
    "ArchitectureClass", "ComplianceState", "LifecycleState", "RiskClass", "Severity",
    # versions
    "Version", "VersionRange", "Comparator", "Op", "parse_range",
    # licences
    "LicenseExpression", "LicenseTerm", "Obligation", "Distribution", "Linkage",
    "CompatibilityVerdict", "parse_expression", "check_compatibility", "canonical_id",
    "obligation_of", "combined_obligation", "normalize", "spdx_ids",
    # findings and gaps
    "Finding", "FindingKind", "Gap", "GapKind", "Remediation",
    "aggregate_risk", "merge_gaps", "rank",
    # reasoning
    "Enrichment", "ReasoningStep", "ReasoningPath", "LayerNumber", "Question",
    "SuggestedAction", "ActionKind", "Guidance", "Clarification", "Option", "Outcome",
    "rank_questions", "trace_to_evidence",
]
