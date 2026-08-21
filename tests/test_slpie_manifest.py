"""The environment manifest: parsing, the skeleton graph, and reconciliation."""

from __future__ import annotations

import time

import pytest

from slpie.core import CommandBus, RecordObservation
from slpie.domain import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceKind,
    LifecycleState,
    Node,
    NodeKind,
    Severity,
    SourceLocation,
    Urn,
)
from slpie.environment import (
    DeclarationKind,
    Manifest,
    Target,
    findings,
    load,
    loads,
    parse_yaml,
    reconcile,
    scaffold,
    skeleton,
    validate,
)
from slpie.errors import ManifestError
from slpie.graph import GraphProjection, SqliteGraph
from slpie.ledger import InMemoryLedger, ProjectionSet

MANIFEST = """apiVersion: slpie/v1
environment: acme-production
target: simulated            # the one tag

security:
  concerns: [pci-dss, gdpr, soc2]
  secret_scanners: [entropy, patterns]
  boundaries:
    - name: cardholder-data
      contains: [payments, vault]

codebase:
  - root: ./services/payments
    team: payments
    domain: billing
  - root: ./services/orders
    team: orders

data:
  - uri: postgres://analytics/orders
    kind: database
    classification: pii

network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest

web:
  - name: storefront
    root: ./apps/storefront
    framework: next

iot:
  - name: fleet-sensors
    broker: mqtt://fleet.acme.com
    device_classes: [temp-v2, gps-v1]

providers:
  - name: stripe
    kind: external-api
"""


@pytest.fixture
def manifest():
    return loads(MANIFEST, source_uri="file:///r/slpie.environment.yaml")


@pytest.fixture
def graph_world():
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    graph = SqliteGraph()
    ProjectionSet(GraphProjection(graph)).subscribe_to(commands.events)
    return ledger, commands, graph


# --- the YAML subset -----------------------------------------------------


def test_a_full_manifest_parses_without_a_yaml_library(manifest):
    assert manifest.environment == "acme-production"
    assert manifest.target is Target.SIMULATED
    assert len(manifest.declarations) == 7


def test_flow_sequences_use_yaml_rules_not_json_rules():
    # `[pci-dss, gdpr]` has no quotes; json.loads would reject it outright.
    document = parse_yaml("apiVersion: slpie/v1\nenvironment: a\nsecurity:\n  concerns: [x, y-z]\n")
    assert document["security"]["concerns"] == ["x", "y-z"]


def test_flow_mappings_parse_too():
    document = parse_yaml("apiVersion: slpie/v1\nenvironment: a\nmetadata: {rps: 100, tier: gold}\n")
    assert document["metadata"] == {"rps": 100, "tier": "gold"}


def test_scalars_keep_the_types_an_author_meant():
    document = parse_yaml(
        "apiVersion: slpie/v1\nenvironment: a\n"
        "metadata:\n  count: 3\n  ratio: 0.5\n  on: true\n  off: false\n"
        "  nothing: null\n  quoted: '7'\n  url: https://x.invalid/v1\n"
    )
    metadata = document["metadata"]
    assert metadata["count"] == 3 and metadata["ratio"] == 0.5
    assert metadata["on"] is True and metadata["off"] is False
    assert metadata["nothing"] is None
    # A quoted number stays a string, and a URL's colon is not a key separator.
    assert metadata["quoted"] == "7"
    assert metadata["url"] == "https://x.invalid/v1"


def test_a_hash_inside_a_quoted_string_is_not_a_comment():
    document = parse_yaml('apiVersion: slpie/v1\nenvironment: "a#b"   # real comment\n')
    assert document["environment"] == "a#b"


def test_a_misspelled_section_is_refused_rather_than_ignored():
    # Silently accepting `codebse:` would mean reporting on an environment with
    # no code in it, confidently.
    with pytest.raises(ManifestError, match="codebse"):
        loads("apiVersion: slpie/v1\nenvironment: a\ncodebse:\n  - root: ./x\n")


@pytest.mark.parametrize("text,expected", [
    ("environment: a\n", "apiVersion"),
    ("apiVersion: slpie/v2\nenvironment: a\n", "unsupported apiVersion"),
    ("apiVersion: slpie/v1\n", "must name its environment"),
    ("apiVersion: slpie/v1\nenvironment: a\ntarget: production\n", "target must be"),
    ("apiVersion: slpie/v1\nenvironment: a\ncodebase: notalist\n", "must be a list"),
    ("apiVersion: slpie/v1\nenvironment: a\ncodebase:\n  - team: x\n", "must set one of"),
    ("apiVersion: slpie/v1\nenvironment: a\nenvironment: b\n", "duplicate key"),
    ("apiVersion: slpie/v1\nenvironment: a\nsecurity:\n\tconcerns: [x]\n", "tabs"),
])
def test_a_malformed_manifest_names_what_is_wrong(text, expected):
    with pytest.raises(ManifestError, match=expected):
        loads(text)


def test_two_declarations_with_one_name_in_one_section_are_refused():
    with pytest.raises(ManifestError, match="duplicate declaration"):
        loads(
            "apiVersion: slpie/v1\nenvironment: a\n"
            "codebase:\n  - name: svc\n    root: ./a\n  - name: svc\n    root: ./b\n"
        )


def test_the_same_name_in_two_sections_is_allowed_because_they_are_two_things():
    manifest = loads(
        "apiVersion: slpie/v1\nenvironment: a\n"
        "codebase:\n  - name: orders\n    root: ./orders\n"
        "data:\n  - name: orders\n    uri: postgres://x/orders\n"
    )
    assert len(manifest.declarations) == 2
    # And they get distinct identities, so neither overwrites the other.
    urns = {d.urn.to_string() for d in manifest.declarations}
    assert len(urns) == 2


def test_the_scaffold_is_a_valid_manifest_defaulting_to_simulated():
    manifest = loads(scaffold("my-shop"))
    assert manifest.environment == "my-shop"
    assert manifest.target is Target.SIMULATED


def test_a_manifest_loads_from_a_directory_by_convention(tmp_path):
    (tmp_path / "slpie.environment.yaml").write_text(MANIFEST, encoding="utf-8")
    assert load(tmp_path).environment == "acme-production"

    with pytest.raises(ManifestError, match="no manifest"):
        load(tmp_path / "nothing")


# --- declarations --------------------------------------------------------


def test_declarations_take_the_specific_node_kind_they_state(manifest):
    kinds = {d.name: d.node_kind for d in manifest}

    assert kinds["payments"] is NodeKind.REPOSITORY
    assert kinds["orders"] is NodeKind.DATABASE       # data, kind: database
    assert kinds["payments-api"] is NodeKind.API
    assert kinds["storefront"] is NodeKind.WEB_APP
    assert kinds["fleet-sensors"] is NodeKind.DEVICE_CLASS
    assert kinds["stripe"] is NodeKind.EXTERNAL_PROVIDER


def test_a_declaration_is_named_by_its_author_where_one_was_given(manifest):
    # An explicit name wins; a path falls back to its last segment.
    assert manifest.named("payments-api") is not None
    assert manifest.named("payments").location == "./services/payments"


def test_local_declarations_are_the_ones_a_simulator_can_materialise(manifest):
    local = {str(d) for d in manifest.local}
    assert "codebase:payments" in local and "web:storefront" in local
    # A postgres uri and an mqtt broker are not filesystem locations.
    assert "data:orders" not in local and "iot:fleet-sensors" not in local


def test_the_manifest_digest_covers_content_and_not_the_target(manifest):
    # `target` is how a manifest is *run*, not what it declares. Flipping it
    # must not make the platform think it is a different environment.
    assert manifest.with_target(Target.LIVE).digest == manifest.digest
    changed = loads(MANIFEST.replace("acme-production", "acme-staging"))
    assert changed.digest != manifest.digest


def test_a_manifest_is_iterable_and_groupable(manifest):
    assert len(manifest) == 7
    assert len(manifest.of_kind(DeclarationKind.CODEBASE)) == 2
    assert [d.name for d in manifest][:2] == ["payments", "orders"]


# --- the skeleton graph --------------------------------------------------


def test_the_skeleton_exists_before_anything_is_discovered(manifest):
    nodes, edges = skeleton(manifest)

    # Seven declarations plus one boundary node.
    assert len(nodes) == 8
    assert all(node.confidence == 0.92 for node in nodes if node.kind is not NodeKind.POLICY)


def test_declared_nodes_are_proposed_rather_than_active(manifest):
    nodes, _ = skeleton(manifest)
    declared = [node for node in nodes if node.kind is not NodeKind.POLICY]

    # They are claimed to exist and have not been met.
    assert all(node.lifecycle is LifecycleState.PROPOSED for node in declared)
    assert all(node.declared_only for node in declared)


def test_declared_evidence_cites_the_manifest_itself(manifest):
    nodes, _ = skeleton(manifest)
    evidence = nodes[0].evidence[0]

    assert evidence.kind is EvidenceKind.DECLARED
    assert evidence.location.uri == "file:///r/slpie.environment.yaml"
    assert "codebase" in evidence.excerpt


def test_boundaries_become_graph_edges_so_violations_are_a_traversal_question(manifest):
    nodes, edges = skeleton(manifest)

    boundary = next(node for node in nodes if node.kind is NodeKind.POLICY)
    assert boundary.identity.to_string() == "urn:slpie:boundary:cardholder-data"
    # `contains: [payments]` is a prefix match, so it covers both the payments
    # repository and the payments API — which is what an author means by it.
    secures = [edge for edge in edges if edge.kind is EdgeKind.SECURES]
    assert len(secures) == 2
    assert all(edge.src == boundary.id for edge in secures)


def test_the_skeleton_reaches_the_graph_through_the_ordinary_event_path(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(
        nodes=nodes, edges=edges, source_uri=manifest.source_uri, discoverer="manifest",
    ))

    assert graph.counts()["nodes"] == 8
    assert graph.counts()["edges"] == 2


# --- reconciliation ------------------------------------------------------


def declared_node(manifest, name):
    nodes, _ = skeleton(manifest)
    return next(node for node in nodes if node.name == name)


def test_before_discovery_everything_declared_is_not_yet_found(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    result = reconcile(manifest, graph)

    assert len(result.declared_not_found) == 7
    assert result.coverage == 0.0
    assert not result.clean


def test_discovery_corroborating_a_declaration_closes_the_delta(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    observed = Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation("file:///r/services/payments/main.py", line=1),
        extractor="python",
    )
    corroborated = declared_node(manifest, "payments").corroborated_by(observed)
    commands.dispatch(RecordObservation(
        nodes=(corroborated,), source_uri="file:///r/services/payments/main.py",
    ))

    result = reconcile(manifest, graph)
    assert "codebase:payments" in result.corroborated
    assert "codebase:payments" not in result.declared_not_found
    assert result.coverage == pytest.approx(1 / 7, abs=1e-4)


def test_declarations_are_reported_with_their_section_so_two_orders_stay_distinct(graph_world):
    _, commands, graph = graph_world
    manifest = loads(
        "apiVersion: slpie/v1\nenvironment: a\n"
        "codebase:\n  - name: orders\n    root: ./orders\n"
        "data:\n  - name: orders\n    uri: postgres://x/orders\n"
    )
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri="file:///m.yaml"))

    result = reconcile(manifest, graph)
    assert set(result.declared_not_found) == {"codebase:orders", "data:orders"}


def test_something_observed_that_nobody_declared_is_a_delta_too(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    observed = Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation("file:///r/services/payments/main.py", line=9),
        extractor="python",
    )
    shadow = Node(
        kind=NodeKind.SERVICE, identity=Urn.create("service", "shadow-analytics"),
        evidence=(observed,),
    )
    commands.dispatch(RecordObservation(nodes=(shadow,), source_uri="file:///r/x.py"))

    result = reconcile(manifest, graph)
    assert shadow.id in result.undeclared


def test_the_transitive_package_tree_is_not_reported_as_undeclared(manifest, graph_world):
    """Nobody declares forty thousand npm packages, and burying the one
    undeclared *service* under them would make the delta useless."""
    from slpie.domain import Purl

    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    lock = Evidence(
        kind=EvidenceKind.LOCKFILE_PIN,
        location=SourceLocation("file:///r/package-lock.json", line=4),
        extractor="npm",
    )
    package = Node(
        kind=NodeKind.PACKAGE, identity=Purl.create("npm", "lodash", version="4.17.20"),
        evidence=(lock,),
    )
    commands.dispatch(RecordObservation(nodes=(package,), source_uri="file:///r/package-lock.json"))

    assert package.id not in reconcile(manifest, graph).undeclared


def test_an_observation_contradicting_a_declaration_is_wrong_not_uncertain(
    manifest, graph_world
):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    observed = Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation("file:///r/api.proto", line=1), extractor="grpc",
    )
    # Declared `kind: rest`, observed speaking gRPC.
    api = declared_node(manifest, "payments-api").corroborated_by(observed)
    api = api.with_properties(observed_kind="grpc")
    commands.dispatch(RecordObservation(nodes=(api,), source_uri="file:///r/api.proto"))

    result = reconcile(manifest, graph)
    assert result.contradictions
    _, attribute, declared, observed_value = result.contradictions[0]
    assert (attribute, declared, observed_value) == ("kind", "rest", "grpc")


def test_silence_is_not_contradiction(manifest, graph_world):
    """A discoverer with nothing to say about `kind` is not disagreeing."""
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    observed = Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation("file:///r/x.py", line=1), extractor="python",
    )
    api = declared_node(manifest, "payments-api").corroborated_by(observed)
    commands.dispatch(RecordObservation(nodes=(api,), source_uri="file:///r/x.py"))

    assert reconcile(manifest, graph).contradictions == ()


def test_undeclared_egress_from_a_boundary_is_a_violation(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    observed = Evidence(
        kind=EvidenceKind.STATIC_IMPORT,
        location=SourceLocation("file:///r/services/payments/exfil.js", line=3),
        extractor="javascript",
    )
    outside = Node(
        kind=NodeKind.EXTERNAL_PROVIDER, identity=Urn.create("provider", "undeclared-analytics"),
        evidence=(observed,),
    )
    payments = declared_node(manifest, "payments")
    leak = Edge(
        kind=EdgeKind.DEPENDS_ON, src=payments.id, dst=outside.id, evidence=(observed,),
    )
    commands.dispatch(RecordObservation(
        nodes=(outside,), edges=(leak,), source_uri="file:///r/services/payments/exfil.js",
    ))

    result = reconcile(manifest, graph)
    assert result.boundary_violations == (
        ("cardholder-data", "payments", "undeclared-analytics"),
    )


def test_each_delta_becomes_a_finding_with_evidence_and_a_remedy(manifest, graph_world):
    _, commands, graph = graph_world
    nodes, edges = skeleton(manifest)
    commands.dispatch(RecordObservation(nodes=nodes, edges=edges, source_uri=manifest.source_uri))

    raised = findings(reconcile(manifest, graph), manifest)

    assert raised
    assert all(finding.evidence for finding in raised)
    assert all(finding.remediation for finding in raised)
    assert all(finding.family == "reconciliation" for finding in raised)


def test_a_boundary_violation_outranks_every_other_delta(manifest, graph_world):
    _, commands, graph = graph_world
    from slpie.environment.reconcile import Reconciliation

    raised = findings(
        Reconciliation(boundary_violations=(("cardholder-data", "payments", "leak"),)),
        manifest,
    )
    assert raised[0].severity is Severity.CRITICAL
    assert raised[0].blocks_release


def test_a_reconciliation_with_nothing_to_report_says_so():
    from slpie.environment.reconcile import Reconciliation

    assert Reconciliation(corroborated=("a",)).clean
    assert Reconciliation(corroborated=("a",)).summary() == "declaration and reality agree"
    assert Reconciliation(corroborated=("a",)).coverage == 1.0
