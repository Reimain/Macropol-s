"""The CQRS core: command to event to projection, and rebuilding from zero."""

from __future__ import annotations

import pytest

from slpie.core import (
    AskQuestion,
    AttachElement,
    Causation,
    ChangeTarget,
    CommandBus,
    DeclareEnvironment,
    DeriveEnrichment,
    DetachElement,
    DomainEvent,
    EventBus,
    EventKind,
    HandOverElement,
    History,
    LedgerIntegrity,
    NegotiateCapability,
    OpenFindings,
    ProjectionStatus,
    QueryBus,
    RaiseFinding,
    Recorder,
    RecordHeartbeat,
    RecordObservation,
    RecordSourceChange,
    StaleEvidence,
    StationStatus,
    SuppressFinding,
    causation_chain,
    emit,
)
from slpie.domain import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceKind,
    Finding,
    FindingKind,
    Node,
    NodeKind,
    Purl,
    Severity,
    SourceLocation,
)
from slpie.errors import BusError, LedgerError, SchemaViolation, TargetRefused
from slpie.ledger import (
    CountingProjection,
    FindingProjection,
    InMemoryLedger,
    ProjectionSet,
    SourceProjection,
    StationProjection,
)

LOCKFILE = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=1204),
    extractor="npm",
    excerpt='"lodash": {"version": "4.17.20"}',
)
LODASH = Node(kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/lodash@4.17.20"),
              evidence=(LOCKFILE,))
APP = Node(kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/app@1.0.0"),
           evidence=(LOCKFILE,))
DEPENDS = Edge(kind=EdgeKind.DEPENDS_ON, src=APP.id, dst=LODASH.id, evidence=(LOCKFILE,))


@pytest.fixture
def platform():
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    projections = ProjectionSet(
        StationProjection(), FindingProjection(), SourceProjection(), CountingProjection(),
    )
    projections.subscribe_to(commands.events)
    queries = QueryBus(projections, ledger)
    return ledger, commands, projections, queries


# --- events --------------------------------------------------------------


def test_an_event_is_identified_by_what_happened_not_by_where_it_landed():
    event = emit(EventKind.HEARTBEAT_RECORDED, "payments", fingerprint="a")
    assert event.sequenced(7).event_id == event.event_id


def test_payload_keys_may_shadow_the_signature_because_real_payloads_do():
    # A manifest declaration genuinely has `kind` and `subject` fields.
    event = emit(EventKind.MANIFEST_DECLARED, "payments", {"kind": "service", "subject": "x"})
    assert event.kind is EventKind.MANIFEST_DECLARED
    assert event.payload["kind"] == "service"


def test_a_derived_event_records_what_caused_it():
    root = emit(EventKind.OBSERVATION_RECORDED, "file:///r/package-lock.json")
    child = root.caused(EventKind.NODE_ASSERTED, LODASH.id)
    grandchild = child.caused(EventKind.ENRICHMENT_DERIVED, LODASH.id)

    assert child.causation_id == root.event_id
    assert grandchild.correlation_id == root.event_id

    by_id = {e.event_id: e for e in (root, child, grandchild)}
    chain = causation_chain(grandchild, by_id=by_id)
    assert [e.kind for e in chain] == [
        EventKind.OBSERVATION_RECORDED, EventKind.NODE_ASSERTED, EventKind.ENRICHMENT_DERIVED,
    ]


def test_an_event_without_a_subject_is_refused():
    with pytest.raises(SchemaViolation, match="has no subject"):
        DomainEvent(kind=EventKind.NODE_ASSERTED, subject="")


# --- the bus -------------------------------------------------------------


def test_the_bus_delivers_in_order_and_deduplicates_on_event_id():
    bus = EventBus()
    recorder = Recorder()
    bus.subscribe(recorder.name, recorder.handle)

    first = emit(EventKind.HEARTBEAT_RECORDED, "a").sequenced(1)
    second = emit(EventKind.HEARTBEAT_RECORDED, "b").sequenced(2)
    report = bus.publish(first, second, first)

    assert [e.subject for e in recorder.events] == ["a", "b"]
    assert report.duplicates == 1
    assert report.clean


def test_a_subscriber_only_sees_the_kinds_it_asked_for():
    bus = EventBus()
    recorder = Recorder()
    bus.subscribe("graph", recorder.handle, kinds=[EventKind.NODE_ASSERTED])

    bus.publish(
        emit(EventKind.NODE_ASSERTED, "n1"),
        emit(EventKind.HEARTBEAT_RECORDED, "payments"),
    )
    assert [e.kind for e in recorder.events] == [EventKind.NODE_ASSERTED]


def test_one_broken_subscriber_does_not_stop_the_others():
    bus = EventBus()
    recorder = Recorder()

    def explode(_event):
        raise RuntimeError("the report view is broken")

    bus.subscribe("broken", explode)
    bus.subscribe(recorder.name, recorder.handle)

    report = bus.publish(emit(EventKind.NODE_ASSERTED, "n1"))

    # The graph still updated; the failure is counted and reported, not swallowed.
    assert len(recorder) == 1
    assert not report.clean
    assert report.failures[0][0] == "broken"
    assert bus.health()["broken"]["failures"] == 1


def test_registering_the_same_subscriber_twice_is_refused():
    bus = EventBus()
    bus.subscribe("graph", lambda event: None)
    with pytest.raises(BusError, match="already registered"):
        bus.subscribe("graph", lambda event: None)


def test_replay_ignores_the_dedupe_window_so_a_rebuild_is_always_possible():
    bus = EventBus()
    recorder = Recorder()
    bus.subscribe(recorder.name, recorder.handle)

    events = [emit(EventKind.NODE_ASSERTED, f"n{n}").sequenced(n) for n in range(1, 4)]
    bus.publish(*events)
    recorder.clear()
    bus.replay(events, into=recorder.name)

    assert len(recorder) == 3


# --- commands to events --------------------------------------------------


def test_a_manifest_becomes_one_event_per_declaration(platform):
    ledger, commands, projections, _ = platform
    produced = commands.dispatch(DeclareEnvironment(
        environment="acme",
        declarations=({"name": "payments", "kind": "service"}, {"root": "./svc/api"}),
    ))

    assert len(produced) == 3        # the manifest itself, plus two declarations
    assert [e.subject for e in produced[1:]] == ["payments", "./svc/api"]
    # Every declaration is chained to the manifest that declared it.
    assert all(e.causation_id == produced[0].event_id for e in produced[1:])


def test_flipping_to_a_live_target_is_refused_without_confirmation(platform):
    _, commands, _, _ = platform

    with pytest.raises(TargetRefused):
        commands.dispatch(ChangeTarget(environment="acme", target="live"))

    # And the refusal reaches the ledger as nothing at all — a refused command
    # never happened.
    assert commands.ledger.version == 0

    produced = commands.dispatch(
        ChangeTarget(environment="acme", target="live", confirmed=True, reason="cutover")
    )
    assert produced[0].kind is EventKind.TARGET_CHANGED


def test_attaching_grants_capabilities_as_separate_recorded_events(platform):
    _, commands, projections, _ = platform
    commands.dispatch(AttachElement(
        element="payments", kind="service", capabilities=("lockfile-read", "static-analysis"),
    ))

    station = projections["station"]
    assert station.elements["payments"].granted == ("lockfile-read", "static-analysis")
    assert station.elements["payments"].attached


def test_a_refused_capability_is_recorded_as_durably_as_a_grant(platform):
    _, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments", capabilities=("static-analysis",)))
    commands.dispatch(NegotiateCapability(
        element="payments", capability="static-analysis", granted=False, reason="vendored tree",
    ))

    station = projections["station"]
    assert station.elements["payments"].granted == ()
    assert station.elements["payments"].refused == {"static-analysis": "vendored tree"}
    assert station.refusals() == (("payments", "static-analysis", "vendored tree"),)


def test_a_later_grant_supersedes_an_earlier_refusal(platform):
    _, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments"))
    commands.dispatch(NegotiateCapability(
        element="payments", capability="static-analysis", granted=False, reason="no access",
    ))
    commands.dispatch(NegotiateCapability(
        element="payments", capability="static-analysis", granted=True,
    ))

    element = projections["station"].elements["payments"]
    assert element.granted == ("static-analysis",)
    assert element.refused == {}


def test_detaching_preserves_the_history_it_leaves_behind(platform):
    ledger, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments", capabilities=("lockfile-read",)))
    commands.dispatch(DetachElement(element="payments", reason="decommissioned"))

    element = projections["station"].elements["payments"]
    assert not element.attached
    assert element.detached_at
    # The grant is still in the ledger, which is where history lives.
    assert len(ledger.subject_history("payments")) == 3


def test_a_handover_preserves_identity(platform):
    _, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments", kind="service"))
    commands.dispatch(HandOverElement(
        element="payments", from_location="./old", to_location="./new",
    ))

    element = projections["station"].elements["payments"]
    assert element.handovers == 1
    assert element.attached


def test_an_observation_records_evidence_before_anything_claims_to_use_it(platform):
    _, commands, _, _ = platform
    produced = commands.dispatch(RecordObservation(
        nodes=(APP, LODASH), edges=(DEPENDS,),
        source_uri="file:///r/package-lock.json", discoverer="npm",
    ))

    kinds = [e.kind for e in produced]
    assert kinds[0] is EventKind.OBSERVATION_RECORDED
    assert kinds.index(EventKind.EVIDENCE_RECORDED) < kinds.index(EventKind.NODE_ASSERTED)
    assert kinds.count(EventKind.NODE_ASSERTED) == 2
    assert kinds.count(EventKind.EDGE_ASSERTED) == 1
    # Three claims share one observation, so the evidence is recorded once.
    assert kinds.count(EventKind.EVIDENCE_RECORDED) == 1


def test_an_enrichment_that_cites_nothing_is_refused_at_the_boundary(platform):
    _, commands, _, _ = platform

    with pytest.raises(LedgerError):
        commands.dispatch(DeriveEnrichment(
            subject=LODASH.id, attribute="risk", value="high", layer="L9",
        ))

    produced = commands.dispatch(DeriveEnrichment(
        subject=LODASH.id, attribute="risk", value="high", layer="L9",
        derived_from=(LOCKFILE.id,),
    ))
    assert produced[0].kind is EventKind.ENRICHMENT_DERIVED


def test_a_waiver_without_a_reason_never_reaches_the_ledger(platform):
    ledger, commands, _, _ = platform

    with pytest.raises(LedgerError):
        commands.dispatch(SuppressFinding(finding_id="f1", reason=""))
    assert ledger.version == 0


def test_an_unregistered_command_is_an_error_not_a_silent_no_op(platform):
    _, commands, _, _ = platform

    class Unknown:
        actor = "system"
        correlation_id = ""

    with pytest.raises(LedgerError):
        commands.dispatch(Unknown())


def test_a_correlation_id_groups_everything_one_operation_produced(platform):
    ledger, commands, _, _ = platform
    commands.dispatch(RecordObservation(
        nodes=(APP,), source_uri="file:///r/a.json", discoverer="npm",
        correlation_id="scan-1",
    ))
    commands.dispatch(RecordObservation(
        nodes=(LODASH,), source_uri="file:///r/b.json", discoverer="npm",
        correlation_id="scan-1",
    ))

    correlated = [r for r in ledger.read() if r.event.correlation_id == "scan-1"]
    assert len(correlated) == len(ledger.read())


# --- findings ------------------------------------------------------------


def a_finding(**overrides):
    defaults = dict(
        kind=FindingKind.VULNERABLE_DEPENDENCY, severity=Severity.HIGH,
        subject=LODASH.id, title="Prototype pollution", code="CVE-2020-8203",
        evidence=(LOCKFILE,),
    )
    return Finding(**{**defaults, **overrides})


def test_raising_and_waiving_a_finding_moves_it_out_of_the_open_view(platform):
    _, commands, projections, _ = platform
    finding = a_finding()

    commands.dispatch(RaiseFinding(finding=finding))
    assert len(projections["findings"].open) == 1
    assert len(projections["findings"].blocking()) == 1

    commands.dispatch(SuppressFinding(finding_id=finding.id, reason="not reachable"))
    assert projections["findings"].open == {}
    assert projections["findings"].suppressed[finding.id] == "not reachable"


def test_a_finding_raised_again_after_resolution_is_open_again(platform):
    from slpie.core import ResolveFinding

    _, commands, projections, _ = platform
    finding = a_finding()

    commands.dispatch(RaiseFinding(finding=finding))
    commands.dispatch(ResolveFinding(finding_id=finding.id, reason="upgraded"))
    assert projections["findings"].open == {}

    # A regression is a genuinely open finding, not a duplicate of a closed one.
    commands.dispatch(RaiseFinding(finding=a_finding(title="Prototype pollution again")))
    assert len(projections["findings"].open) == 1
    assert finding.id not in projections["findings"].resolved


# --- projections rebuild -------------------------------------------------


def test_every_projection_rebuilds_from_sequence_zero(platform):
    ledger, commands, projections, _ = platform
    commands.dispatch(DeclareEnvironment(
        environment="acme", declarations=({"name": "payments", "kind": "service"},),
    ))
    commands.dispatch(AttachElement(element="payments", capabilities=("lockfile-read",)))
    commands.dispatch(NegotiateCapability(
        element="payments", capability="static-analysis", granted=False, reason="vendored",
    ))
    commands.dispatch(RecordObservation(
        nodes=(APP, LODASH), edges=(DEPENDS,), source_uri="file:///r/package-lock.json",
    ))
    commands.dispatch(RaiseFinding(finding=a_finding()))

    before = {
        "station": projections["station"].to_dict(),
        "counts": dict(projections["counts"].counts),
        "findings": dict(projections["findings"].open),
        "sources": {k: sorted(v) for k, v in projections["sources"].evidence_by_uri.items()},
    }

    projections.rebuild(ledger.read())

    assert projections["station"].to_dict() == before["station"]
    assert dict(projections["counts"].counts) == before["counts"]
    assert dict(projections["findings"].open) == before["findings"]
    assert {
        k: sorted(v) for k, v in projections["sources"].evidence_by_uri.items()
    } == before["sources"]


def test_a_projection_added_later_catches_up_without_disturbing_the_others(platform):
    ledger, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments"))
    commands.dispatch(RecordHeartbeat(element="payments", fingerprint="a"))

    late = projections.add(CountingProjection())
    late.name = "late"
    assert late.total() == 0

    late.rebuild(ledger.read())
    assert late.total() == ledger.version
    # The projections that were already current are untouched.
    assert projections["station"].elements["payments"].attached


def test_catch_up_folds_only_what_a_projection_has_not_seen(platform):
    ledger, commands, projections, _ = platform
    commands.dispatch(AttachElement(element="payments"))
    counts = projections["counts"]
    applied_before = counts.applied

    assert counts.catch_up(ledger.read()) == 0
    assert counts.applied == applied_before


# --- the source index ----------------------------------------------------


def test_a_changed_file_invalidates_exactly_the_evidence_drawn_from_it(platform):
    _, commands, projections, queries = platform
    commands.dispatch(RecordObservation(
        nodes=(APP, LODASH), edges=(DEPENDS,), source_uri="file:///r/package-lock.json",
    ))

    stale = queries.ask(StaleEvidence(uris=("file:///r/package-lock.json",))).value
    assert stale == [LOCKFILE.id]
    assert queries.ask(StaleEvidence(uris=("file:///r/other.json",))).value == []

    commands.dispatch(RecordSourceChange(uri="file:///r/package-lock.json", digest="b2new"))
    assert queries.ask(StaleEvidence(uris=("file:///r/package-lock.json",))).value == []


def test_the_source_index_reports_which_files_actually_changed(platform):
    _, commands, projections, _ = platform
    commands.dispatch(RecordSourceChange(uri="file:///r/a.json", digest="aaa"))

    sources = projections["sources"]
    assert sources.changed({"file:///r/a.json": "aaa"}) == ()
    assert sources.changed({"file:///r/a.json": "bbb"}) == ("file:///r/a.json",)
    assert sources.changed({"file:///r/new.json": "ccc"}) == ("file:///r/new.json",)


# --- the query side ------------------------------------------------------


def test_queries_answer_from_projections_and_carry_the_version_they_saw(platform):
    ledger, commands, _, queries = platform
    commands.dispatch(AttachElement(element="payments", capabilities=("lockfile-read",)))

    result = queries.ask(StationStatus())
    assert result.version == ledger.version
    assert len(result.value["elements"]) == 1


def test_findings_can_be_filtered_by_severity(platform):
    _, commands, _, queries = platform
    commands.dispatch(RaiseFinding(finding=a_finding(code="A", severity=Severity.HIGH)))
    commands.dispatch(RaiseFinding(finding=a_finding(code="B", severity=Severity.LOW)))

    assert len(queries.ask(OpenFindings()).value) == 2
    assert len(queries.ask(OpenFindings(severity="high")).value) == 1


def test_history_returns_everything_that_ever_touched_one_subject(platform):
    _, commands, _, queries = platform
    commands.dispatch(AttachElement(element="payments"))
    commands.dispatch(RecordHeartbeat(element="payments", fingerprint="a"))
    commands.dispatch(DetachElement(element="payments", reason="done"))

    history = queries.ask(History(subject="payments")).value
    assert [entry["event"]["kind"] for entry in history] == [
        "element_attached", "heartbeat_recorded", "element_detached",
    ]


def test_the_integrity_query_confirms_the_chain(platform):
    _, commands, _, queries = platform
    commands.dispatch(AttachElement(element="payments"))

    integrity = queries.ask(LedgerIntegrity()).value
    assert integrity["intact"]
    assert integrity["version"] == 1


def test_projection_status_reports_how_far_each_read_model_has_folded(platform):
    ledger, commands, _, queries = platform
    commands.dispatch(AttachElement(element="payments"))

    status = queries.ask(ProjectionStatus()).value
    assert status["counts"]["last_sequence"] == ledger.version


def test_an_unregistered_query_is_an_error(platform):
    _, _, _, queries = platform

    class Unknown:
        pass

    with pytest.raises(Exception):
        queries.ask(Unknown())


def test_the_query_bus_offers_no_way_to_write(platform):
    _, _, _, queries = platform
    # The read side holds a ledger but exposes no append path of its own.
    assert not hasattr(queries, "append")
    assert not hasattr(queries, "dispatch")
