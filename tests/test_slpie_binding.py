"""Binding: the same code paths simulated and live, and the gate between them.

The central test here is that a discoverer reading through a simulated
connector and one reading through a live connector — against the *same*
materialised files — produce byte-identical results. If that ever stops being
true, "prove it in the simulator, then fire it at production" stops being a
guarantee and becomes a hope.
"""

from __future__ import annotations

import pytest

from slpie.binding import (
    Binding,
    FilesystemConnector,
    Guard,
    RefusingConnector,
    Resolver,
    TargetSelection,
    filesystem_factory,
)
from slpie.environment import DeclarationKind, Target, loads
from slpie.errors import BindingError, TargetRefused
from slpie.simulator import SimulatedWorld

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
codebase:
  - root: ./services/payments
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
"""


@pytest.fixture
def manifest():
    return loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml")


@pytest.fixture
def world(manifest, tmp_path):
    built = SimulatedWorld.build(manifest, root=tmp_path / "world")
    yield built


# --- the filesystem connector -------------------------------------------


def test_a_connector_reports_logical_uris_not_the_physical_ones(world):
    connector = world.bind().connector_for("payments")
    resource = connector.read("./services/payments/package.json")

    # The temp directory never leaks into an answer. Evidence gathered here
    # cites the same path it would cite against the real checkout.
    assert resource.uri == "./services/payments/package.json"
    assert str(world.root) not in resource.uri
    assert b'"name"' in resource.content


def test_listing_returns_logical_uris_and_skips_scm_internals(world):
    connector = world.bind().connector_for("payments")
    listing = connector.list("./services/payments")

    assert all(uri.startswith("./services/payments/") for uri in listing)
    assert not any("/.git/" in uri for uri in listing)


def test_listing_can_be_filtered_by_pattern(world):
    connector = world.bind().connector_for("payments")
    assert connector.list("./services/payments", pattern="*package-lock.json") == (
        "./services/payments/package-lock.json",
    )


def test_a_path_that_escapes_the_bound_root_is_refused(world):
    """A `..` assembled from a relative import must not reach the real disk."""
    connector = world.bind().connector_for("payments")

    with pytest.raises(BindingError, match="outside the bound root"):
        connector.read("./services/payments/../../../../etc/passwd")


def test_reading_something_that_is_not_there_is_an_error_not_an_empty_file(world):
    connector = world.bind().connector_for("payments")

    with pytest.raises(BindingError, match="no such file"):
        connector.read("./services/payments/nothing.json")
    assert not connector.exists("./services/payments/nothing.json")
    assert connector.exists("./services/payments/package.json")


def test_every_connector_refuses_to_write_by_default(world):
    connector = world.bind().connector_for("payments")

    with pytest.raises(BindingError, match="read-only"):
        connector.write("./services/payments/x", b"y")
    with pytest.raises(BindingError, match="read-only"):
        connector.delete("./services/payments/package.json")


def test_a_resource_carries_a_digest_so_change_detection_is_cheap(world):
    connector = world.bind().connector_for("payments")
    first = connector.read("./services/payments/package.json")
    again = connector.read("./services/payments/package.json")

    assert first.digest == again.digest
    world.rewrite("payments", "package.json", '{"name":"changed"}')
    assert connector.read("./services/payments/package.json").digest != first.digest


def test_an_oversized_file_is_refused_rather_than_loaded(world, monkeypatch):
    import slpie.binding.resolver as resolver_module

    monkeypatch.setattr(resolver_module, "MAX_READ_BYTES", 10)
    connector = world.bind().connector_for("payments")

    with pytest.raises(BindingError, match="read limit"):
        connector.read("./services/payments/package-lock.json")


# --- simulated and live are the same code -------------------------------


def test_simulated_and_live_connectors_return_identical_results(world, manifest):
    """The guarantee, stated as an assertion.

    Both connectors are pointed at the same materialised directory — one via
    the simulated factory, one via the live factory reading the same absolute
    path. If the two ever diverge, a green simulated case stops being evidence
    about the live path.
    """
    physical = world.root / "payments"
    simulated = FilesystemConnector("./services/payments", physical, mode="simulated")
    live = FilesystemConnector("./services/payments", physical, mode="live")

    assert simulated.list("./services/payments") == live.list("./services/payments")
    for uri in simulated.list("./services/payments"):
        left, right = simulated.read(uri), live.read(uri)
        assert (left.uri, left.content, left.digest) == (right.uri, right.content, right.digest)


def test_only_the_mode_field_distinguishes_the_two(world):
    physical = world.root / "payments"
    simulated = FilesystemConnector("./services/payments", physical, mode="simulated")
    live = FilesystemConnector("./services/payments", physical, mode="live")

    assert simulated.mode != live.mode
    assert simulated.capabilities == live.capabilities
    assert type(simulated) is type(live)


def test_flipping_the_one_tag_changes_the_target_and_nothing_else(manifest, world):
    live_manifest = manifest.with_target(Target.LIVE)

    assert live_manifest.digest == manifest.digest
    assert [d.name for d in live_manifest] == [d.name for d in manifest]
    assert live_manifest.target is not manifest.target


# --- the guard -----------------------------------------------------------


def test_binding_to_a_live_target_is_refused_without_confirmation(manifest, world):
    live = SimulatedWorld(manifest=manifest.with_target(Target.LIVE), root=world.root)

    with pytest.raises(TargetRefused, match="explicit confirmation"):
        live.bind()


def test_a_confirmed_live_binding_is_allowed_and_recorded(manifest, world):
    live = SimulatedWorld(manifest=manifest.with_target(Target.LIVE), root=world.root)
    resolver = live.bind(confirmed=True)

    assert all(binding.target is Target.LIVE for binding in resolver.bindings)
    audit = resolver.guard.audit()
    assert audit["selection"]["touches_reality"]
    assert audit["allowed"]


def test_writing_to_a_live_target_needs_a_separate_grant(manifest, world):
    live = SimulatedWorld(manifest=manifest.with_target(Target.LIVE), root=world.root)
    resolver = live.bind(confirmed=True)

    # Confirming that you want to *look* at production is not agreeing that the
    # platform may change it.
    with pytest.raises(TargetRefused, match="read-only"):
        resolver.capability("payments", "write")

    resolver.guard.grant_writes(reason="approved cutover CHG-441")
    assert resolver.guard.writes_granted


def test_granting_writes_requires_a_reason():
    guard = Guard()
    with pytest.raises(ValueError, match="reason"):
        guard.grant_writes(reason="")


def test_a_simulated_target_needs_no_confirmation_for_anything(world):
    resolver = world.bind()
    resolver.capability("payments", "file-read")
    assert resolver.guard.clean


def test_a_refusal_is_recorded_as_well_as_raised(manifest, world):
    selection = TargetSelection(default=Target.LIVE, confirmed=False)
    guard = Guard(selection=selection)

    with pytest.raises(TargetRefused):
        guard.check_binding("payments")
    assert not guard.clean
    assert guard.audit()["refused"][0]["operation"] == "bind payments"


# --- hybrid --------------------------------------------------------------


def test_under_hybrid_anything_not_pointed_at_reality_stays_simulated():
    manifest = loads(
        "apiVersion: slpie/v1\nenvironment: a\ntarget: hybrid\n"
        "codebase:\n"
        "  - name: payments\n    root: ./payments\n    target: live\n"
        "  - name: orders\n    root: ./orders\n"
    )
    selection = TargetSelection.from_manifest(manifest, confirmed=True)

    assert selection.for_element("payments") is Target.LIVE
    # The safe side is the default; forgetting to mark an element cannot
    # accidentally point it at production.
    assert selection.for_element("orders") is Target.SIMULATED
    assert selection.for_element("never-mentioned") is Target.SIMULATED
    assert selection.live_elements == ("payments",)
    assert selection.touches_reality


def test_a_single_live_element_makes_the_whole_selection_touch_reality():
    manifest = loads(
        "apiVersion: slpie/v1\nenvironment: a\ntarget: hybrid\n"
        "codebase:\n  - name: payments\n    root: ./p\n    target: live\n"
    )
    assert TargetSelection.from_manifest(manifest).touches_reality


# --- refusals ------------------------------------------------------------


def test_an_element_with_no_connector_gets_one_that_explains_itself(manifest, world):
    """A refusal is an outcome, not an absence.

    The network element has no registered connector, so it binds to a refusing
    one — which reports why. Leaving it unbound would make it vanish from the
    scan instead of appearing as a gap.
    """
    resolver = Resolver(guard=Guard())
    resolver.register("codebase", Target.SIMULATED, filesystem_factory(world.root))

    bindings = resolver.bind_all(manifest)
    network = next(b for b in bindings if b.element == "payments-api")

    assert not network.available
    assert "no simulated connector" in network.connector.describe()["reason"]
    assert network.connector.list("anything") == ()


def test_asking_for_a_capability_an_element_does_not_have_says_what_it_does_have(world):
    resolver = world.bind()

    with pytest.raises(BindingError, match="does not offer"):
        resolver.capability("payments", "topic-list")


def test_asking_for_an_element_that_was_never_bound_is_an_error(world):
    resolver = world.bind()

    with pytest.raises(BindingError, match="not bound"):
        resolver.connector_for("never-declared")


def test_unavailable_bindings_are_collected_for_reporting(manifest, world):
    resolver = Resolver(guard=Guard())
    resolver.register("codebase", Target.SIMULATED, filesystem_factory(world.root))
    resolver.bind_all(manifest)

    assert [binding.element for binding in resolver.unavailable] == ["payments-api"]


def test_a_non_filesystem_declaration_refuses_a_filesystem_binding(world):
    manifest = loads(
        "apiVersion: slpie/v1\nenvironment: a\n"
        "data:\n  - name: orders\n    uri: postgres://analytics/orders\n"
    )
    resolver = Resolver(guard=Guard())
    resolver.register("data", Target.SIMULATED, filesystem_factory(world.root))
    binding = resolver.bind_all(manifest)[0]

    assert not binding.available
    assert "not a filesystem location" in binding.connector.describe()["reason"]


def test_the_resolver_renders_its_whole_state_for_the_ui(world):
    payload = world.bind().to_dict()

    assert len(payload["bindings"]) == 2
    assert payload["guard"]["selection"]["default"] == "simulated"
