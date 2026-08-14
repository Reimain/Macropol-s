"""The station: attach, negotiate, hand over, detach — and the gaps that result."""

from __future__ import annotations

import pytest

from slpie.core import CommandBus
from slpie.domain import EvidenceKind, GapKind
from slpie.environment import loads
from slpie.errors import StationError
from slpie.ledger import InMemoryLedger, ProjectionSet, StationProjection
from slpie.simulator import DAY, ControlledClock, SimulatedWorld, fire
from slpie.station import (
    KNOWN_CAPABILITIES,
    STALE_AFTER_NS,
    Capability,
    Negotiation,
    Station,
    negotiate,
)

MANIFEST = """apiVersion: slpie/v1
environment: acme
codebase:
  - root: ./services/payments
  - root: ./services/orders
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
"""

WANTED = ("file-read", "lockfile-read", "static-analysis", "scm-history")


@pytest.fixture
def manifest():
    return loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml")


@pytest.fixture
def station(manifest, tmp_path):
    world = SimulatedWorld.build(manifest, root=tmp_path / "world")
    resolver = world.bind()
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    projections = ProjectionSet(StationProjection())
    projections.subscribe_to(commands.events)
    built = Station(commands, resolver, clock=world.clock)
    return built, world, projections, ledger


# --- capabilities --------------------------------------------------------


def test_a_refusal_must_carry_a_reason():
    """A refusal with no reason produces a gap nobody can act on."""
    refused = Capability.refuse("static-analysis", "")
    assert refused.reason == "no reason given"

    explained = Capability.refuse("static-analysis", "the tree is vendored")
    assert explained.reason == "the tree is vendored"


def test_a_refusal_costs_what_the_evidence_it_blocks_was_worth():
    """Losing a lockfile read costs more than losing message inspection —
    a lockfile is the only route to certainty."""
    lockfile = Capability.refuse("lockfile-read", "no access")
    naming = Capability.refuse("registry-query", "offline")

    assert lockfile.impact > 0
    assert lockfile.impact >= naming.impact


def test_an_unknown_capability_is_visibly_unknown():
    assert Capability.grant("file-read").known
    assert not Capability.grant("teleportation").known


def test_negotiation_records_both_halves(station):
    built, world, _, _ = station
    connector = built.resolver.connector_for("payments")
    result = negotiate("payments", connector, wanted=WANTED)

    assert result.allows("file-read")
    assert not result.allows("scm-history")
    assert not result.complete
    # Both are kept: "no source to analyse" and "would not let us look" are
    # different facts.
    assert {c.name for c in result.granted} | {c.name for c in result.refused} == set(WANTED)


def test_a_capability_wanted_but_not_offered_is_refused_by_omission(station):
    built, _, _, _ = station
    result = negotiate(
        "payments", built.resolver.connector_for("payments"), wanted=("teleportation",),
    )
    assert [c.name for c in result.refused] == ["teleportation"]
    assert "does not offer" in result.refused[0].reason


def test_a_connector_that_can_explain_its_refusal_does(station):
    built, world, _, _ = station
    world.faults.refuse("payments", "static-analysis", reason="the tree is vendored")
    resolver = world.bind()

    result = negotiate("payments", resolver.connector_for("payments"), wanted=WANTED)
    refused = {c.name: c.reason for c in result.refused}
    assert refused["static-analysis"] == "the tree is vendored"


# --- attaching -----------------------------------------------------------


def test_attaching_negotiates_and_records_what_was_granted(station):
    built, _, projections, _ = station
    registration = built.attach(
        next(iter(built.resolver.bindings)).declaration, wanted=WANTED
    )

    assert registration.attached
    assert "file-read" in registration.capabilities
    assert projections["station"].elements["payments"].attached


def test_a_refusal_reaches_the_ledger_so_it_can_become_a_gap(station, manifest):
    """A refusal the ledger never saw cannot become a gap, and a gap nobody
    recorded is indistinguishable from a question that was answered."""
    built, _, projections, ledger = station
    built.attach_all(manifest, wanted=WANTED)

    refused = projections["station"].elements["payments"].refused
    assert "scm-history" in refused
    kinds = {record.kind.value for record in ledger.read()}
    assert "capability_refused" in kinds


def test_every_declared_element_attaches_in_one_call(station, manifest):
    built, _, _, _ = station
    registrations = built.attach_all(manifest, wanted=WANTED)

    assert len(registrations) == 3
    assert len(built.attached) == 3


def test_the_station_says_which_elements_can_answer_a_question(station, manifest):
    built, world, _, _ = station
    world.faults.refuse("payments", "static-analysis", reason="vendored")
    built.resolver = world.bind()
    built.attach_all(manifest, wanted=WANTED)

    capable = {r.element for r in built.with_capability("static-analysis")}
    assert "payments" not in capable
    assert "orders" in capable


# --- gaps ----------------------------------------------------------------


def test_every_refusal_becomes_a_gap_with_something_to_do_about_it(station, manifest):
    built, _, _, _ = station
    built.attach_all(manifest, wanted=WANTED)

    gaps = built.gaps()
    assert gaps
    assert all(gap.kind is GapKind.CAPABILITY_REFUSED for gap in gaps)
    assert all(gap.remediation for gap in gaps)
    assert all(gap.actionable for gap in gaps)


def test_gaps_are_computed_not_accumulated(station, manifest):
    """A capability granted after a refusal stops producing a gap, without
    anyone having to remember to clear it."""
    built, world, _, _ = station
    world.faults.refuse("payments", "static-analysis", reason="vendored")
    built.resolver = world.bind()
    built.attach_all(manifest, wanted=("static-analysis",))
    assert any("static-analysis" in gap.detail for gap in built.gaps())

    world.faults.clear("payments")
    built.resolver = world.bind()
    built.attach_all(manifest, wanted=("static-analysis",))
    assert not any(
        gap.subject == "payments" and "static-analysis" in gap.detail
        for gap in built.gaps()
    )


def test_coverage_reports_how_much_of_the_environment_answered_fully(station, manifest):
    built, _, _, _ = station
    built.attach_all(manifest, wanted=("file-read",))
    assert built.coverage() == 1.0

    built.attach_all(manifest, wanted=WANTED)
    assert built.coverage() == 0.0


# --- tracking ------------------------------------------------------------


def test_a_silent_element_becomes_a_stale_gap(station, manifest):
    built, world, _, _ = station
    built.attach_all(manifest, wanted=("file-read",))
    assert not any(gap.kind is GapKind.STALE_HEARTBEAT for gap in built.gaps())

    world.clock.advance(STALE_AFTER_NS + DAY)
    stale = [gap for gap in built.gaps() if gap.kind is GapKind.STALE_HEARTBEAT]
    assert len(stale) == 3
    assert "has not reported" in stale[0].detail


def test_a_heartbeat_clears_staleness(station, manifest):
    built, world, _, _ = station
    built.attach_all(manifest, wanted=("file-read",))
    world.clock.advance(STALE_AFTER_NS + DAY)

    built.heartbeat("payments", fingerprint="abc123")
    stale = {gap.subject for gap in built.gaps() if gap.kind is GapKind.STALE_HEARTBEAT}
    assert "payments" not in stale


def test_a_handover_moves_an_element_without_changing_what_it_is(station, manifest):
    """A relocated repository that re-attached under a new name would split its
    history in two."""
    built, _, projections, ledger = station
    built.attach_all(manifest, wanted=("file-read",))
    before = built.registration("payments")

    moved = built.handover("payments", to="./platform/payments")

    assert moved.element == before.element
    assert moved.attached
    assert moved.location == "./platform/payments"
    assert moved.handovers == (("./services/payments", "./platform/payments"),)
    # And the whole history is still under one subject.
    assert len(ledger.subject_history("payments")) >= 3


def test_detaching_preserves_history_and_produces_a_gap(station, manifest):
    built, _, projections, _ = station
    built.attach_all(manifest, wanted=("file-read",))

    built.detach("orders", reason="decommissioned")

    assert {r.element for r in built.attached} == {"payments", "payments-api"}
    assert not projections["station"].elements["orders"].attached
    gaps = built.registration("orders").gaps()
    assert any(gap.kind is GapKind.NOT_ATTACHED for gap in gaps)


def test_operating_on_something_that_never_attached_is_an_error(station):
    built, _, _, _ = station

    with pytest.raises(StationError, match="not attached"):
        built.heartbeat("never-declared")
    with pytest.raises(StationError, match="not attached"):
        built.handover("never-declared", to="./x")
    with pytest.raises(StationError, match="not attached"):
        built.detach("never-declared")


def test_the_station_renders_its_whole_state_for_the_ui(station, manifest):
    built, _, _, _ = station
    built.attach_all(manifest, wanted=WANTED)

    payload = built.to_dict()
    assert payload["attached"] == 3
    assert payload["gaps"]
    assert payload["refusals"]
    assert payload["elements"][0]["capabilities"]
