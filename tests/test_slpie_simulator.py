"""The simulator: real artifacts, fired scenarios, injected faults, moving time.

Every assertion here is about something written to a real filesystem. If these
passed against mocks they would prove only that the mocks agreed with the test.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from slpie.binding import Guard
from slpie.environment import DeclarationKind, Target, loads
from slpie.errors import BindingError
from slpie.simulator import (
    DAY,
    EPOCH,
    ControlledClock,
    Fault,
    FaultInjector,
    FaultKind,
    SimulatedWorld,
    SystemClock,
    available,
    fire,
    materialize,
)

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
security:
  boundaries:
    - name: cardholder-data
      contains: [payments]
codebase:
  - root: ./services/payments
    language: npm
  - root: ./services/api
    language: python
  - root: ./services/gateway
    language: go
data:
  - folder: ./warehouse/orders
    kind: schema
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
  - name: order-events
    uri: kafka://broker/orders
    kind: event-stream
web:
  - name: storefront
    root: ./apps/storefront
    framework: next
iot:
  - name: fleet
    broker: mqtt://fleet.acme.com
    device_classes: [temp-v2]
providers:
  - name: stripe
"""


@pytest.fixture
def manifest():
    return loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml")


@pytest.fixture
def world(manifest, tmp_path):
    return SimulatedWorld.build(manifest, root=tmp_path / "world")


# --- materialisation -----------------------------------------------------


def test_a_declaration_becomes_a_genuine_npm_lockfile(world):
    lock = json.loads(world.read("payments", "package-lock.json"))

    # lockfileVersion 3 is what npm 9+ writes, and what the discoverer parses.
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["name"] == "payments"
    entry = lock["packages"]["node_modules/lodash"]
    assert entry["version"] == "4.17.20"
    assert entry["resolved"].startswith("https://registry.npmjs.org/lodash/-/")
    assert entry["integrity"].startswith("sha512-")


def test_the_manifest_and_the_lockfile_agree_with_each_other(world):
    package = json.loads(world.read("payments", "package.json"))
    lock = json.loads(world.read("payments", "package-lock.json"))

    for name, declared in package["dependencies"].items():
        resolved = lock["packages"][f"node_modules/{name}"]["version"]
        assert declared == f"^{resolved}"


def test_a_python_codebase_gets_a_real_pyproject_and_requirements(world):
    pyproject = world.read("api", "pyproject.toml")
    assert "[build-system]" in pyproject and 'name = "api"' in pyproject

    requirements = world.read("api", "requirements.txt")
    assert "lodash==4.17.20" in requirements

    import tomllib

    # Parsed by the same stdlib reader discovery will use.
    parsed = tomllib.loads(pyproject)
    assert parsed["project"]["name"] == "api"


def test_a_go_codebase_gets_a_real_go_mod_and_go_sum(world):
    go_mod = world.read("gateway", "go.mod")
    assert go_mod.startswith("module github.com/acme/gateway")
    assert "go 1.21" in go_mod
    assert "github.com/vendor/lodash v4.17.20" in go_mod
    assert "h1:" in world.read("gateway", "go.sum")


def test_source_files_import_what_the_manifest_declares(world):
    source = world.read("payments", "src/index.js")
    assert "require('lodash')" in source


def test_containers_are_materialised_for_every_codebase(world):
    assert "FROM node:20-alpine" in world.read("payments", "Dockerfile")
    deployment = world.read("payments", "k8s/deployment.yaml")
    assert "kind: Deployment" in deployment and "containerPort: 8080" in deployment


def test_a_rest_declaration_gets_openapi_and_an_event_stream_gets_asyncapi(world):
    openapi = json.loads(world.read("payments-api", "openapi.json"))
    assert openapi["openapi"] == "3.1.0"
    assert "/orders" in openapi["paths"]
    assert openapi["servers"][0]["url"] == "https://api.acme.com/v1"

    asyncapi = json.loads(world.read("order-events", "asyncapi.json"))
    assert asyncapi["asyncapi"] == "2.6.0"
    assert "order-events.events" in asyncapi["channels"]


def test_a_data_declaration_gets_real_sql_and_json_schema(world):
    sql = world.read("orders", "schema.sql")
    assert "CREATE TABLE orders" in sql and "CREATE INDEX" in sql

    schema = json.loads(world.read("orders", "schema.json"))
    assert schema["type"] == "object" and "email" in schema["properties"]


def test_iot_and_provider_declarations_are_materialised_too(world):
    devices = json.loads(world.read("fleet", "devices.json"))
    assert devices["device_classes"] == ["temp-v2"]
    assert devices["topics"] == ["fleet/temp-v2/telemetry"]

    provider = json.loads(world.read("stripe", "provider.json"))
    assert provider["name"] == "stripe"


@pytest.mark.skipif(
    subprocess.run(["which", "git"], capture_output=True).returncode != 0,
    reason="git is not installed",
)
def test_a_codebase_gets_a_real_git_repository_with_a_real_commit(world):
    repository = world.path_for("payments")
    assert (repository / ".git").is_dir()

    log = subprocess.run(
        ["git", "-C", str(repository), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    )
    assert "materialised by the SLPIE simulator" in log.stdout


def test_a_world_cleans_up_after_itself_only_when_it_owns_the_directory(manifest, tmp_path):
    kept = tmp_path / "kept"
    with SimulatedWorld.build(manifest, root=kept) as world:
        assert world.files("payments")
    assert kept.exists()      # we supplied the path, so it survives

    world = SimulatedWorld.build(manifest)
    created = world.root
    world.tear_down()
    assert not created.exists()


def test_the_world_reports_what_it_wrote(world):
    payload = world.to_dict()

    assert payload["environment"] == "acme"
    assert payload["target"] == "simulated"
    assert "npm-lockfile" in payload["kinds"]
    assert payload["artifacts"] > 15


# --- scenarios -----------------------------------------------------------


def test_every_registered_scenario_is_addressable_by_name():
    names = available()
    assert "cve" in names and "capability-refused" in names
    assert len(names) >= 10

    with pytest.raises(KeyError, match="no scenario named"):
        fire("does-not-exist", None)


def test_a_cve_scenario_changes_the_lockfile_rather_than_the_graph(world):
    """The platform has to *find* it, through the same discoverer that runs
    against a real repository. A scenario that wrote a finding directly would
    be testing the test."""
    outcome = fire("cve", world, package="lodash", version="4.17.20")

    lock = json.loads(world.read("payments", "package-lock.json"))
    assert lock["packages"]["node_modules/lodash"]["version"] == "4.17.20"
    assert outcome.expect_findings == ("vulnerable_dependency",)
    assert "payments/package-lock.json" in outcome.changed


def test_a_major_bump_leaves_the_declared_range_behind(world):
    fire("major-bump", world, package="lodash", version="5.0.0")

    lock = json.loads(world.read("payments", "package-lock.json"))
    package = json.loads(world.read("payments", "package.json"))

    # The resolved version no longer satisfies what was asked for — which is
    # what makes it a conflict rather than an upgrade.
    assert lock["packages"]["node_modules/lodash"]["version"] == "5.0.0"
    assert package["dependencies"]["lodash"] == "^4.17.20"


def test_a_duplicate_version_scenario_produces_two_resolutions_of_one_package(world):
    fire("duplicate-versions", world, package="semver")
    lock = json.loads(world.read("payments", "package-lock.json"))

    semver_entries = [key for key in lock["packages"] if key.endswith("semver")]
    assert len(semver_entries) == 2


def test_a_relicensing_scenario_rewrites_the_recorded_licence(world):
    fire("license-change", world, package="lodash", license="AGPL-3.0-only")
    lock = json.loads(world.read("payments", "package-lock.json"))

    assert lock["packages"]["node_modules/lodash"]["license"] == "AGPL-3.0-only"


def test_a_shadow_dependency_appears_in_source_but_in_no_manifest(world):
    fire("shadow-dependency", world, package="left-pad")

    assert "require('left-pad')" in world.read("payments", "src/shadow.js")
    assert "left-pad" not in world.read("payments", "package.json")


def test_a_broken_contract_removes_an_operation_consumers_still_call(world):
    fire("contract-broken", world)
    document = json.loads(world.read("payments-api", "openapi.json"))

    assert "/orders" not in document["paths"]


def test_a_boundary_breach_writes_undeclared_egress(world):
    outcome = fire("boundary-breach", world)

    source = world.read("payments", "src/exfil.js")
    assert "analytics.undeclared.invalid" in source
    assert outcome.expect_findings == ("boundary_violation",)


def test_the_unmaintained_scenario_moves_time_rather_than_waiting(world):
    before = world.clock.instant
    fire("unmaintained", world, years=3)

    assert world.clock.instant - before == 3 * 365 * DAY


def test_a_scenario_declares_what_the_platform_ought_to_notice(world):
    outcome = fire("capability-refused", world, capability="static-analysis")
    payload = outcome.to_dict()

    assert payload["expect_gaps"] == ["capability_refused"]
    assert payload["parameters"]["capability"] == "static-analysis"


# --- faults --------------------------------------------------------------


def test_a_refused_capability_removes_only_that_capability(world):
    fire("capability-refused", world, capability="static-analysis")
    connector = world.bind().connector_for("payments")

    assert "static-analysis" not in connector.capabilities
    # Everything else still works, which is the situation that actually arises.
    assert "file-read" in connector.capabilities
    assert connector.read("./services/payments/package.json").content


def test_a_refusal_can_state_its_reason_for_the_gap_it_becomes(world):
    world.faults.refuse("payments", "static-analysis", reason="the tree is vendored")
    connector = world.bind().connector_for("payments")

    assert connector.refusals() == (("static-analysis", "the tree is vendored"),)


def test_an_unavailable_element_stops_answering_entirely(world):
    fire("service-dies", world, element="payments")
    resolver = world.bind()
    connector = resolver.connector_for("payments")

    assert not connector.available()
    with pytest.raises(BindingError, match="stopped responding"):
        connector.list("./services/payments")
    assert [b.element for b in resolver.unavailable] == ["payments"]


def test_a_partial_scan_returns_less_than_it_should(world):
    complete = len(world.bind().connector_for("payments").list("./services/payments"))
    fire("partial-scan", world, element="payments", fraction=0.5)

    truncated = len(world.bind().connector_for("payments").list("./services/payments"))
    assert 0 < truncated < complete


def test_corrupt_content_truncates_a_file_mid_stream(world):
    world.faults.inject(Fault(
        kind=FaultKind.CORRUPT_CONTENT, element="payments", fraction=0.3,
    ))
    connector = world.bind().connector_for("payments")
    resource = connector.read("./services/payments/package-lock.json")

    # A half-written lockfile: the parser must fail cleanly rather than misread.
    with pytest.raises(json.JSONDecodeError):
        json.loads(resource.text)


def test_a_contradiction_fault_makes_reality_disagree_with_the_declaration(world):
    fire("declaration-drift", world, element="payments-api")
    connector = world.bind().connector_for("payments-api")
    # A network element's logical root is its declared URL, so the uri comes
    # from the connector's own listing rather than from a guessed path.
    uri = next(u for u in connector.list("") if u.endswith("openapi.json"))
    document = json.loads(connector.read(uri).text)

    assert document["x-protocol"] == "grpc"


def test_faults_only_touch_the_element_they_name(world):
    world.faults.refuse("payments", "static-analysis")
    resolver = world.bind()

    assert "static-analysis" not in resolver.connector_for("payments").capabilities
    assert "static-analysis" in resolver.connector_for("api").capabilities


def test_faults_can_be_cleared(world):
    world.faults.refuse("payments", "static-analysis")
    assert len(world.faults) == 1

    world.faults.clear("payments")
    assert len(world.faults) == 0
    assert "static-analysis" in world.bind().connector_for("payments").capabilities


def test_which_faults_become_gaps_is_declared_rather_than_inferred():
    assert FaultKind.REFUSE_CAPABILITY.becomes_gap
    assert FaultKind.UNAVAILABLE.becomes_gap
    # A contradiction is a finding, not a gap — it is wrong, not unseen.
    assert not FaultKind.CONTRADICT.becomes_gap


# --- the clock -----------------------------------------------------------


def test_a_controlled_clock_only_moves_when_told():
    clock = ControlledClock()
    start = clock.instant

    for _ in range(5):
        clock.now()
    assert clock.instant == start

    clock.advance(DAY)
    assert clock.instant == start + DAY


def test_successive_reads_never_collide_so_ordering_stays_stable():
    clock = ControlledClock()
    readings = [clock.now() for _ in range(100)]

    assert len(set(readings)) == 100
    assert readings == sorted(readings)


def test_time_cannot_be_run_backwards():
    clock = ControlledClock()
    with pytest.raises(ValueError, match="does not run backwards"):
        clock.advance(-1)


def test_the_clock_can_jump_to_an_absolute_instant():
    clock = ControlledClock()
    clock.set(EPOCH + 5 * DAY)
    assert clock.instant == EPOCH + 5 * DAY


def test_the_system_clock_satisfies_the_same_interface():
    from slpie.simulator.clock import Clock

    assert isinstance(SystemClock(), Clock)
    assert isinstance(ControlledClock(), Clock)
    assert SystemClock().now() > EPOCH
