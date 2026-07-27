"""Commands — the only way to change anything.

A command is a *request* that something happen; it can be refused. An event is a
record that something *did* happen; it cannot. Keeping the two apart is what
makes the ledger trustworthy — nothing reaches it that was not accepted by a
handler, and nothing a handler accepted fails to reach it.

Commands carry no timestamps and no ids of their own. They are inert data: the
handler decides what actually occurred, and the ledger decides when.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..domain.evidence import Evidence
from ..domain.finding import Finding
from ..domain.node import Node
from ..domain.edge import Edge


@dataclass(frozen=True, slots=True)
class Command:
    """Base for every command. ``actor`` propagates into the resulting events."""

    actor: str = "system"
    correlation_id: str = ""


@dataclass(frozen=True, slots=True)
class DeclareEnvironment(Command):
    """Ingest an environment manifest — the declare-first entry point."""

    environment: str = ""
    target: str = "simulated"
    declarations: tuple[Mapping[str, Any], ...] = ()
    manifest_digest: str = ""
    source_uri: str = ""


@dataclass(frozen=True, slots=True)
class ChangeTarget(Command):
    """Flip between the simulator and the real environment.

    The one dangerous command in the platform, which is why it carries an
    explicit ``confirmed`` flag that the guard checks rather than inferring
    intent from context.
    """

    environment: str = ""
    target: str = "simulated"
    confirmed: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AttachElement(Command):
    element: str = ""
    kind: str = ""
    capabilities: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DetachElement(Command):
    element: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HandOverElement(Command):
    """An element moved but is still the same element — identity is preserved."""

    element: str = ""
    from_location: str = ""
    to_location: str = ""


@dataclass(frozen=True, slots=True)
class NegotiateCapability(Command):
    element: str = ""
    capability: str = ""
    granted: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecordHeartbeat(Command):
    element: str = ""
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class RecordObservation(Command):
    """A discoverer's output: nodes and edges, each with its evidence.

    Submitted as one command rather than as a stream of assertions so that a
    single file's contribution to the graph lands atomically. Half a lockfile in
    the graph is worse than none of it.
    """

    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    source_uri: str = ""
    discoverer: str = ""


@dataclass(frozen=True, slots=True)
class RecordSourceChange(Command):
    """A file changed — the trigger for incremental invalidation."""

    uri: str = ""
    digest: str = ""
    change: str = "modified"       # created | modified | deleted


@dataclass(frozen=True, slots=True)
class RetireNode(Command):
    node_id: str = ""
    reason: str = ""
    valid_to: int = 0


@dataclass(frozen=True, slots=True)
class RetireEdge(Command):
    edge_id: str = ""
    src: str = ""
    dst: str = ""
    reason: str = ""
    valid_to: int = 0


@dataclass(frozen=True, slots=True)
class DeriveEnrichment(Command):
    subject: str = ""
    attribute: str = ""
    value: Any = None
    layer: str = ""
    derived_from: tuple[str, ...] = ()
    confidence: float = 0.0
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class RaiseFinding(Command):
    finding: Finding | None = None


@dataclass(frozen=True, slots=True)
class ResolveFinding(Command):
    finding_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SuppressFinding(Command):
    finding_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SealSnapshot(Command):
    label: str = ""
    valid_time: int = 0


@dataclass(frozen=True, slots=True)
class GenerateArtifact(Command):
    artifact: str = ""             # "sbom" | "c4" | "togaf-application" | ...
    path: str = ""
    format: str = ""


@dataclass(frozen=True, slots=True)
class FireScenario(Command):
    """Run a simulated condition — a CVE landing, a service dying, a major bump."""

    scenario: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AskQuestion(Command):
    """A console turn. Recorded so the conversation is part of the history."""

    question: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegisterPlugin(Command):
    plugin_id: str = ""
    version: str = ""
    kinds: tuple[str, ...] = ()
    runtime: str = "in-process"


@dataclass(frozen=True, slots=True)
class QuarantinePlugin(Command):
    plugin_id: str = ""
    reason: str = ""
