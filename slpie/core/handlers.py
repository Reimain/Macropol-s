"""Command handlers — the write side, and the only writers of events.

Handlers translate a command into the events that record what happened. They
never write a projection directly: they append to the ledger, the ledger
publishes, and the projections fold. That indirection is the whole point — it
means a read model can be added, changed or rebuilt years later without the
write side knowing it exists.

One rule holds everywhere here: a handler that emits an event has already
decided the thing is true. Validation happens *before* emission, never after.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..domain.edge import Edge
from ..domain.evidence import Evidence
from ..domain.node import Node
from ..errors import LedgerError, TargetRefused
from .bus import EventBus
from .commands import (
    AskQuestion,
    AttachElement,
    ChangeTarget,
    Command,
    DeclareEnvironment,
    DeriveEnrichment,
    DetachElement,
    FireScenario,
    GenerateArtifact,
    HandOverElement,
    NegotiateCapability,
    QuarantinePlugin,
    RaiseFinding,
    RecordHeartbeat,
    RecordObservation,
    RecordSourceChange,
    RegisterPlugin,
    ResolveFinding,
    RetireEdge,
    RetireNode,
    SealSnapshot,
    SuppressFinding,
)
from .events import DomainEvent, EventKind, emit

Handler = Callable[[Command], Sequence[DomainEvent]]


class CommandBus:
    """Routes commands to handlers, appends what they produce, publishes it.

    The sequence is deliberate and fixed: **handle, append, publish**. Publishing
    before the append would let a projection show state that a crash could erase;
    appending without publishing would leave every read model behind the truth.
    """

    def __init__(self, ledger: Any, events: EventBus | None = None) -> None:
        self.ledger = ledger
        self.events = events or EventBus()
        self._handlers: dict[type, Handler] = {}
        self._register_defaults()

    def register(self, command_type: type, handler: Handler) -> None:
        self._handlers[command_type] = handler

    def dispatch(self, command: Command) -> tuple[DomainEvent, ...]:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise LedgerError(f"no handler registered for {type(command).__name__}")

        produced = tuple(handler(command))
        if not produced:
            return ()

        if command.correlation_id:
            # An explicit correlation id names the *operation*, so it overrides
            # the one derived events inherit from their root. Otherwise a scan
            # spanning several commands would fragment into one group per
            # command, which is the opposite of what grouping is for.
            produced = tuple(
                _with_correlation(event, command.correlation_id) for event in produced
            )

        records = self.ledger.append(*produced)
        sealed = tuple(record.event for record in records)
        self.events.publish(*sealed)
        return sealed

    def __call__(self, command: Command) -> tuple[DomainEvent, ...]:
        return self.dispatch(command)

    # -- built-in handlers -----------------------------------------------

    def _register_defaults(self) -> None:
        self.register(DeclareEnvironment, handle_declare_environment)
        self.register(ChangeTarget, handle_change_target)
        self.register(AttachElement, handle_attach)
        self.register(DetachElement, handle_detach)
        self.register(HandOverElement, handle_handover)
        self.register(NegotiateCapability, handle_negotiate_capability)
        self.register(RecordHeartbeat, handle_heartbeat)
        self.register(RecordObservation, handle_observation)
        self.register(RecordSourceChange, handle_source_change)
        self.register(RetireNode, handle_retire_node)
        self.register(RetireEdge, handle_retire_edge)
        self.register(DeriveEnrichment, handle_enrichment)
        self.register(RaiseFinding, handle_raise_finding)
        self.register(ResolveFinding, handle_resolve_finding)
        self.register(SuppressFinding, handle_suppress_finding)
        self.register(SealSnapshot, handle_seal_snapshot)
        self.register(GenerateArtifact, handle_generate_artifact)
        self.register(FireScenario, handle_fire_scenario)
        self.register(AskQuestion, handle_ask_question)
        self.register(RegisterPlugin, handle_register_plugin)
        self.register(QuarantinePlugin, handle_quarantine_plugin)


def _with_correlation(event: DomainEvent, correlation_id: str) -> DomainEvent:
    from dataclasses import replace

    return replace(event, correlation_id=correlation_id)


# --- environment and target ----------------------------------------------


def handle_declare_environment(command: DeclareEnvironment) -> list[DomainEvent]:
    """A manifest becomes one event per declaration, plus one for the manifest.

    Per declaration rather than one bulk event so that each declared element has
    its own point in history — which is what lets the reconciliation view say
    when a declaration was made and by whom, element by element.
    """
    root = emit(
        EventKind.MANIFEST_DECLARED, command.environment or "environment",
        actor=command.actor,
        target=command.target,
        digest=command.manifest_digest,
        source_uri=command.source_uri,
        declaration_count=len(command.declarations),
    )
    events = [root]
    for declaration in command.declarations:
        subject = str(
            declaration.get("name")
            or declaration.get("uri")
            or declaration.get("root")
            or declaration.get("folder")
            or "unnamed"
        )
        events.append(root.caused(EventKind.MANIFEST_DECLARED, subject, dict(declaration)))
    return events


def handle_change_target(command: ChangeTarget) -> list[DomainEvent]:
    """Flipping to live is gated here, at the write side, not in the UI.

    A guard that lives only in the interface is a guard that an API call, a
    script, or an agent walks straight past.
    """
    if command.target == "live" and not command.confirmed:
        raise TargetRefused(
            "target live",
            "flipping to a live environment requires explicit confirmation",
        )
    return [emit(
        EventKind.TARGET_CHANGED, command.environment or "environment",
        actor=command.actor, target=command.target,
        confirmed=command.confirmed, reason=command.reason,
    )]


# --- station -------------------------------------------------------------


def handle_attach(command: AttachElement) -> list[DomainEvent]:
    attached = emit(
        EventKind.ELEMENT_ATTACHED, command.element, actor=command.actor,
        kind=command.kind, properties=dict(command.properties),
    )
    return [
        attached,
        *(
            attached.caused(EventKind.CAPABILITY_GRANTED, command.element, capability=capability)
            for capability in command.capabilities
        ),
    ]


def handle_detach(command: DetachElement) -> list[DomainEvent]:
    return [emit(
        EventKind.ELEMENT_DETACHED, command.element,
        actor=command.actor, reason=command.reason,
    )]


def handle_handover(command: HandOverElement) -> list[DomainEvent]:
    return [emit(
        EventKind.ELEMENT_HANDED_OVER, command.element, actor=command.actor,
        from_location=command.from_location, to_location=command.to_location,
    )]


def handle_negotiate_capability(command: NegotiateCapability) -> list[DomainEvent]:
    """A refusal is recorded exactly as durably as a grant.

    It has to be: the refusal is what becomes a reported gap, and a gap the
    platform forgot is indistinguishable from a question it answered.
    """
    kind = EventKind.CAPABILITY_GRANTED if command.granted else EventKind.CAPABILITY_REFUSED
    return [emit(
        kind, command.element, actor=command.actor,
        capability=command.capability, reason=command.reason,
    )]


def handle_heartbeat(command: RecordHeartbeat) -> list[DomainEvent]:
    return [emit(
        EventKind.HEARTBEAT_RECORDED, command.element,
        actor=command.actor, fingerprint=command.fingerprint,
    )]


# --- discovery -----------------------------------------------------------


def handle_observation(command: RecordObservation) -> list[DomainEvent]:
    """Turn a discoverer's nodes and edges into evidence, node and edge events.

    Evidence is emitted first and separately. That ordering is what the
    incremental engine relies on: the evidence-to-source index must exist before
    anything claims to be derived from it.
    """
    events: list[DomainEvent] = []
    seen_evidence: set[str] = set()

    def record_evidence(items: Sequence[Evidence], root: DomainEvent | None) -> None:
        for item in items:
            if item.id in seen_evidence:
                continue
            seen_evidence.add(item.id)
            payload = {
                "evidence_id": item.id,
                "uri": item.location.uri,
                "kind": item.kind.value,
                "line": item.location.line,
                "extractor": item.extractor,
                "content_digest": item.content_digest,
                "excerpt": item.excerpt[:500],
            }
            events.append(
                root.caused(EventKind.EVIDENCE_RECORDED, item.location.uri, **payload)
                if root is not None
                else emit(
                    EventKind.EVIDENCE_RECORDED, item.location.uri,
                    actor=command.actor, **payload,
                )
            )

    observation = emit(
        EventKind.OBSERVATION_RECORDED, command.source_uri or command.discoverer or "discovery",
        actor=command.actor, discoverer=command.discoverer,
        nodes=len(command.nodes), edges=len(command.edges),
    )
    events.append(observation)

    for node in command.nodes:
        record_evidence(node.evidence, observation)
    for edge in command.edges:
        record_evidence(edge.evidence, observation)

    for node in command.nodes:
        events.append(observation.caused(
            EventKind.NODE_ASSERTED, node.id, **node.to_dict(),
        ))
    for edge in command.edges:
        events.append(observation.caused(
            EventKind.EDGE_ASSERTED, edge.id, **edge.to_dict(),
        ))
    return events


def handle_source_change(command: RecordSourceChange) -> list[DomainEvent]:
    return [emit(
        EventKind.SOURCE_CHANGED, command.uri, actor=command.actor,
        uri=command.uri, digest=command.digest, change=command.change,
    )]


def handle_retire_node(command: RetireNode) -> list[DomainEvent]:
    return [emit(
        EventKind.NODE_RETIRED, command.node_id, actor=command.actor,
        reason=command.reason, valid_to=command.valid_to,
    )]


def handle_retire_edge(command: RetireEdge) -> list[DomainEvent]:
    return [emit(
        EventKind.EDGE_RETIRED, command.edge_id, actor=command.actor,
        src=command.src, dst=command.dst,
        reason=command.reason, valid_to=command.valid_to,
    )]


# --- reasoning and governance -------------------------------------------


def handle_enrichment(command: DeriveEnrichment) -> list[DomainEvent]:
    """An enrichment that cites nothing is refused at the boundary.

    The provenance chain is only walkable if every link exists. A layer that
    cannot say what it read has not derived anything.
    """
    if not command.derived_from:
        raise LedgerError(
            f"enrichment {command.attribute!r} on {command.subject} cites no inputs"
        )
    return [emit(
        EventKind.ENRICHMENT_DERIVED, command.subject, actor=command.actor,
        attribute=command.attribute, value=command.value, layer=command.layer,
        derived_from=list(command.derived_from), confidence=command.confidence,
        rationale=command.rationale,
    )]


def handle_raise_finding(command: RaiseFinding) -> list[DomainEvent]:
    finding = command.finding
    if finding is None:
        raise LedgerError("RaiseFinding carries no finding")
    return [emit(
        EventKind.FINDING_RAISED, finding.subject, actor=command.actor,
        finding_id=finding.id, **{
            key: value for key, value in finding.to_dict().items()
            if key not in ("id", "subject")
        },
    )]


def handle_resolve_finding(command: ResolveFinding) -> list[DomainEvent]:
    return [emit(
        EventKind.FINDING_RESOLVED, command.finding_id, actor=command.actor,
        finding_id=command.finding_id, reason=command.reason,
    )]


def handle_suppress_finding(command: SuppressFinding) -> list[DomainEvent]:
    """A waiver without a reason is refused — an unexplained waiver is not audit."""
    if not command.reason:
        raise LedgerError(f"suppressing {command.finding_id} requires a reason")
    return [emit(
        EventKind.FINDING_SUPPRESSED, command.finding_id, actor=command.actor,
        finding_id=command.finding_id, reason=command.reason,
    )]


# --- outputs and operations ---------------------------------------------


def handle_seal_snapshot(command: SealSnapshot) -> list[DomainEvent]:
    return [emit(
        EventKind.SNAPSHOT_SEALED, command.label or "snapshot", actor=command.actor,
        label=command.label, valid_time=command.valid_time,
    )]


def handle_generate_artifact(command: GenerateArtifact) -> list[DomainEvent]:
    return [emit(
        EventKind.ARTIFACT_GENERATED, command.path or command.artifact, actor=command.actor,
        artifact=command.artifact, path=command.path, format=command.format,
    )]


def handle_fire_scenario(command: FireScenario) -> list[DomainEvent]:
    return [emit(
        EventKind.SCENARIO_FIRED, command.scenario, actor=command.actor,
        scenario=command.scenario, parameters=dict(command.parameters),
    )]


def handle_ask_question(command: AskQuestion) -> list[DomainEvent]:
    return [emit(
        EventKind.QUESTION_ASKED, command.question[:200] or "question",
        actor=command.actor, question=command.question, context=dict(command.context),
    )]


def handle_register_plugin(command: RegisterPlugin) -> list[DomainEvent]:
    return [emit(
        EventKind.PLUGIN_REGISTERED, command.plugin_id, actor=command.actor,
        version=command.version, kinds=list(command.kinds), runtime=command.runtime,
    )]


def handle_quarantine_plugin(command: QuarantinePlugin) -> list[DomainEvent]:
    return [emit(
        EventKind.PLUGIN_QUARANTINED, command.plugin_id, actor=command.actor,
        reason=command.reason,
    )]
