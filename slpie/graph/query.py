"""The graph projector — ledger events folded into the graph tables.

This is the bridge between phase 2 and phase 3, and it is deliberately thin.
It reconstructs domain objects from event payloads and hands them to the store;
it computes nothing. Any derivation done here would be state that exists only in
the projection and therefore vanishes on rebuild — the exact failure that makes
event-sourced systems drift.

Evidence arrives before the claims that rest on it, because
:func:`slpie.core.handlers.handle_observation` emits it in that order. This
projector depends on that ordering, and it is worth being explicit about: a node
event carries only evidence *ids*, so the evidence rows must already exist for
the join to resolve.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..core.events import DomainEvent, EventKind
from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.identity import parse_identity
from ..domain.lifecycle import ArchitectureClass, ComplianceState, LifecycleState, RiskClass
from ..domain.node import OPEN, Node, NodeKind
from ..domain.reasoning import Enrichment
from ..ledger.projection import Projection


class GraphProjection(Projection):
    """Folds node, edge, evidence and enrichment events into a graph store."""

    kinds = frozenset({
        EventKind.EVIDENCE_RECORDED,
        EventKind.NODE_ASSERTED,
        EventKind.NODE_RETIRED,
        EventKind.EDGE_ASSERTED,
        EventKind.EDGE_RETIRED,
        EventKind.ENRICHMENT_DERIVED,
    })

    def __init__(self, graph: Any) -> None:
        super().__init__("graph")
        self.graph = graph
        self._evidence: dict[str, Evidence] = {}
        self._skipped: list[str] = []

    def reset(self) -> None:
        self.graph.clear()
        self._evidence.clear()
        self._skipped.clear()

    def rebuild(self, records: Any) -> int:
        """Refold the whole ledger inside one transaction.

        A rebuild replays every event ever written. Committing each assertion
        separately would make the operation slow enough that nobody runs it,
        which would quietly turn the rebuildable read model into a fiction.
        """
        transaction = getattr(self.graph, "transaction", None)
        if transaction is None:
            return super().rebuild(records)
        with transaction():
            return super().rebuild(records)

    def apply(self, event: DomainEvent) -> None:
        if event.kind is EventKind.EVIDENCE_RECORDED:
            self._record_evidence(event)
        elif event.kind is EventKind.NODE_ASSERTED:
            self._assert_node(event)
        elif event.kind is EventKind.EDGE_ASSERTED:
            self._assert_edge(event)
        elif event.kind is EventKind.NODE_RETIRED:
            self.graph.retire_node(
                event.subject,
                valid_to=int(event.payload.get("valid_to", 0)) or event.occurred_at,
                sequence=event.sequence,
            )
        elif event.kind is EventKind.EDGE_RETIRED:
            self.graph.retire_edge(
                event.subject,
                valid_to=int(event.payload.get("valid_to", 0)) or event.occurred_at,
                sequence=event.sequence,
            )
        elif event.kind is EventKind.ENRICHMENT_DERIVED:
            self._record_enrichment(event)

    # -- folds -----------------------------------------------------------

    def _record_evidence(self, event: DomainEvent) -> None:
        payload = event.payload
        evidence = Evidence(
            kind=EvidenceKind(payload.get("kind", "name_heuristic")),
            location=SourceLocation(
                str(payload.get("uri", event.subject)),
                line=int(payload.get("line", 0)),
                column=int(payload.get("column", 0)),
                commit_sha=str(payload.get("commit_sha", "")),
            ),
            extractor=str(payload.get("extractor", "unknown")),
            extractor_version=str(payload.get("extractor_version", "0")),
            content_digest=str(payload.get("content_digest", "")),
            excerpt=str(payload.get("excerpt", "")),
            observed_at=event.occurred_at,
            labels=dict(payload.get("labels", {})),
        )
        # Keyed by the id the ledger recorded, not by recomputing it. The
        # payload is a projection of the evidence and may be lossy — a truncated
        # excerpt alone would change the hash — and a node must resolve the
        # evidence it actually cites, not a near-identical reconstruction.
        self._evidence[str(payload.get("evidence_id", evidence.id))] = evidence

    def _assert_node(self, event: DomainEvent) -> None:
        evidence = self._resolve(event.payload.get("evidence_ids", ()), event.subject)
        if not evidence:
            # Every claim needs its justification present. A node whose evidence
            # rows are missing is a corrupted stream, not a node with no reason.
            self._skipped.append(event.subject)
            return

        payload = event.payload
        node = Node(
            kind=NodeKind(payload["kind"]),
            identity=parse_identity(payload["identity"]),
            evidence=evidence,
            properties=dict(payload.get("properties", {})),
            lifecycle=LifecycleState(payload.get("lifecycle", "unknown")),
            risk=RiskClass(payload.get("risk", "none")),
            compliance=ComplianceState(payload.get("compliance", "unassessed")),
            architecture=ArchitectureClass(payload.get("architecture", "unclassified")),
            valid_from=int(payload.get("valid_from", 0)),
            valid_to=int(payload.get("valid_to", OPEN) or OPEN),
            observed_at=int(payload.get("observed_at", 0)) or event.occurred_at,
            superseded_at=int(payload.get("superseded_at", OPEN) or OPEN),
        )
        self.graph.assert_node(node, sequence=event.sequence)

    def _assert_edge(self, event: DomainEvent) -> None:
        evidence = self._resolve(event.payload.get("evidence_ids", ()), event.subject)
        if not evidence:
            self._skipped.append(event.subject)
            return

        payload = event.payload
        edge = Edge(
            kind=EdgeKind(payload["kind"]),
            src=payload["src"],
            dst=payload["dst"],
            evidence=evidence,
            properties=dict(payload.get("properties", {})),
            qualifier=str(payload.get("qualifier", "")),
            valid_from=int(payload.get("valid_from", 0)),
            valid_to=int(payload.get("valid_to", OPEN) or OPEN),
            observed_at=int(payload.get("observed_at", 0)) or event.occurred_at,
            superseded_at=int(payload.get("superseded_at", OPEN) or OPEN),
        )
        self.graph.assert_edge(edge, sequence=event.sequence)

    def _record_enrichment(self, event: DomainEvent) -> None:
        payload = event.payload
        enrichment = Enrichment(
            subject=event.subject,
            attribute=str(payload.get("attribute", "")),
            value=payload.get("value"),
            layer=str(payload.get("layer", "")),
            derived_from=tuple(payload.get("derived_from", ())),
            confidence=float(payload.get("confidence", 0.0)),
            rationale=str(payload.get("rationale", "")),
            derived_at=event.occurred_at,
        )
        self.graph.record_enrichment(enrichment, sequence=event.sequence)

    def _resolve(self, ids: Any, subject: str) -> tuple[Evidence, ...]:
        return tuple(
            self._evidence[eid] for eid in ids if eid in self._evidence
        )

    # -- diagnostics -----------------------------------------------------

    @property
    def skipped(self) -> tuple[str, ...]:
        """Claims dropped because their evidence never arrived.

        Non-empty means the event stream is inconsistent — surfaced rather than
        silently tolerated, because a graph quietly missing nodes is worse than
        one that says it is.
        """
        return tuple(self._skipped)

    def status(self) -> dict[str, Any]:
        return {**super().status(), "skipped": len(self._skipped)}
