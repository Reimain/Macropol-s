"""The graph: bitemporal projection, in-SQL traversal, snapshots and diffs."""

from __future__ import annotations

import time

import pytest

from slpie.core import (
    CommandBus,
    DeriveEnrichment,
    RecordObservation,
    RetireEdge,
    RetireNode,
)
from slpie.domain import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceKind,
    Node,
    NodeKind,
    Purl,
    SourceLocation,
    Urn,
)
from slpie.graph import (
    GraphProjection,
    SnapshotStore,
    SqliteGraph,
    Traverser,
    root_digest,
)
from slpie.ledger import InMemoryLedger, ProjectionSet

LOCK = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=10),
    extractor="npm",
    excerpt='"lodash": {"version": "4.17.20"}',
)
DYNAMIC = Evidence(
    kind=EvidenceKind.DYNAMIC_LOAD,
    location=SourceLocation("file:///r/plugins.py", line=3),
    extractor="python",
    excerpt="importlib.import_module(name)",
)


def package(name, version="1.0.0", evidence=LOCK):
    return Node(
        kind=NodeKind.PACKAGE,
        identity=Purl.create("npm", name, version=version),
        evidence=(evidence,),
    )


def depends(src, dst, evidence=LOCK, kind=EdgeKind.DEPENDS_ON):
    return Edge(kind=kind, src=src.id, dst=dst.id, evidence=(evidence,))


@pytest.fixture
def world():
    """A small ecosystem, built the way the platform builds one: through events.

    Nothing writes to the graph directly. If the projector is wrong, every test
    here fails — which is the point.
    """
    ledger = InMemoryLedger()
    commands = CommandBus(ledger)
    graph = SqliteGraph()
    projection = GraphProjection(graph)
    ProjectionSet(projection).subscribe_to(commands.events)
    return ledger, commands, graph, projection


@pytest.fixture
def populated(world):
    ledger, commands, graph, projection = world
    app, web, util = package("app"), package("web", "2.0.0"), package("util", "1.5.0")
    lodash = package("lodash", "4.17.20")
    plugin = package("plugin", "0.1.0", DYNAMIC)

    commands.dispatch(RecordObservation(
        nodes=(app, web, util, lodash, plugin),
        edges=(
            depends(app, web),
            depends(web, util),
            depends(util, lodash),
            depends(app, lodash),
            depends(plugin, lodash, DYNAMIC),
            depends(app, util, kind=EdgeKind.OWNS),
        ),
        source_uri="file:///r/package-lock.json",
    ))
    return ledger, commands, graph, projection, {
        "app": app, "web": web, "util": util, "lodash": lodash, "plugin": plugin,
    }


# --- projection ----------------------------------------------------------


def test_the_graph_is_built_entirely_from_ledger_events(populated):
    _, _, graph, projection, _ = populated

    assert graph.counts()["nodes"] == 5
    assert graph.counts()["edges"] == 6
    assert projection.skipped == ()


def test_a_node_comes_back_with_the_evidence_that_justified_it(populated):
    _, _, graph, _, nodes = populated
    lodash = graph.node(nodes["lodash"].id)

    assert lodash is not None
    assert lodash.confidence == 1.0
    assert lodash.evidence[0].location.reference == "package-lock.json:10"


def test_a_claim_whose_evidence_never_arrived_is_skipped_rather_than_invented(world):
    from slpie.core import DomainEvent, EventKind

    _, commands, graph, projection = world
    orphan = package("orphan")
    # A node event with no preceding evidence event — a corrupted stream.
    commands.ledger.append(DomainEvent(
        kind=EventKind.NODE_ASSERTED, subject=orphan.id, payload=orphan.to_dict(),
    ))
    projection.rebuild(commands.ledger.read())

    assert graph.node(orphan.id) is None
    assert projection.skipped == (orphan.id,)
    assert projection.status()["skipped"] == 1


def test_asserting_the_same_node_again_updates_it_without_duplicating_it(populated):
    _, commands, graph, _, nodes = populated
    before = graph.counts()["nodes"]

    commands.dispatch(RecordObservation(
        nodes=(nodes["lodash"],), source_uri="file:///r/package-lock.json",
    ))
    assert graph.counts()["nodes"] == before


def test_the_whole_graph_rebuilds_from_sequence_zero(populated):
    ledger, _, graph, projection, _ = populated
    before = (
        graph.counts(),
        sorted(node.id for node in graph.nodes()),
        sorted(edge.id for edge in graph.edges()),
    )

    projection.rebuild(ledger.read())

    assert (
        graph.counts(),
        sorted(node.id for node in graph.nodes()),
        sorted(edge.id for edge in graph.edges()),
    ) == before


# --- traversal -----------------------------------------------------------


def test_blast_radius_finds_everything_that_breaks_and_how_far_away_it_is(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).impact(nodes["lodash"].id)

    by_id = {item.node_id: item for item in result}
    assert by_id[nodes["app"].id].distance == 1
    assert by_id[nodes["util"].id].distance == 1
    assert by_id[nodes["web"].id].distance == 2
    assert result.total == 4


def test_impact_propagates_the_weakest_confidence_along_the_path(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).impact(nodes["lodash"].id)

    plugin = next(item for item in result if item.node_id == nodes["plugin"].id)
    # Reached only through a dynamic load, and reported as such rather than
    # merged in with the certain results.
    assert plugin.confidence == pytest.approx(0.40)
    assert not plugin.certain
    assert result.uncertain == (plugin,)
    assert "low-confidence" in result.summary()


def test_a_confidence_floor_excludes_weak_paths_entirely(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).impact(nodes["lodash"].id, min_confidence=0.9)

    assert nodes["plugin"].id not in {item.node_id for item in result}
    assert result.total == 3


def test_traversal_ignores_organisational_edges(populated):
    _, _, graph, _, nodes = populated
    # `app owns util` exists, but ownership does not mechanically break anything.
    result = Traverser(graph).dependencies(nodes["app"].id)
    routes = {item.node_id: item.distance for item in result}

    # util is reachable at depth 2 through web, not at depth 1 through `owns`.
    assert routes[nodes["util"].id] == 2


def test_forward_reachability_finds_transitive_dependencies(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).dependencies(nodes["app"].id)

    assert {item.node_id for item in result} == {
        nodes["web"].id, nodes["util"].id, nodes["lodash"].id
    }


def test_depth_limits_are_honoured_and_reported_as_truncation(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).impact(nodes["lodash"].id, max_depth=1)

    assert all(item.distance <= 1 for item in result)
    assert result.truncated
    assert "truncated" in result.summary()


def test_a_node_nothing_depends_on_has_an_empty_blast_radius(populated):
    _, _, graph, _, nodes = populated
    result = Traverser(graph).impact(nodes["app"].id)

    assert result.total == 0
    assert result.summary() == "nothing depends on this"


def test_a_cycle_terminates_the_traversal_instead_of_the_query(world):
    _, commands, graph, _ = world
    a, b, c = package("a"), package("b"), package("c")
    commands.dispatch(RecordObservation(
        nodes=(a, b, c),
        edges=(depends(a, b), depends(b, c), depends(c, a)),
        source_uri="file:///r/lock.json",
    ))

    # Every node in a 3-cycle reaches the other two, and the query returns.
    result = Traverser(graph).impact(a.id)
    assert result.total == 2


def test_circular_dependencies_are_found_and_reported_once(world):
    _, commands, graph, _ = world
    a, b, c, outside = package("a"), package("b"), package("c"), package("outside")
    commands.dispatch(RecordObservation(
        nodes=(a, b, c, outside),
        edges=(depends(a, b), depends(b, c), depends(c, a), depends(outside, a)),
        source_uri="file:///r/lock.json",
    ))

    cycles = Traverser(graph).cycles()
    # One cycle, not three — `a→b→c→a` and `b→c→a→b` are the same cycle.
    assert len(cycles) == 1
    assert cycles[0].length == 3
    assert cycles[0].signature == frozenset({a.id, b.id, c.id})


def test_an_acyclic_graph_reports_no_cycles(populated):
    _, _, graph, _, _ = populated
    assert Traverser(graph).cycles() == ()


def test_paths_between_two_nodes_come_back_shortest_first(populated):
    _, _, graph, _, nodes = populated
    paths = Traverser(graph).paths(nodes["app"].id, nodes["lodash"].id)

    assert len(paths) == 3
    assert [path.length for path in paths] == [1, 2, 3]


# --- bitemporality and retirement ---------------------------------------


def test_retiring_a_node_removes_it_from_the_live_graph_but_not_from_history(populated):
    _, commands, graph, _, nodes = populated
    commands.dispatch(RetireNode(node_id=nodes["plugin"].id, reason="removed", valid_to=999))

    assert graph.counts()["nodes"] == 4
    assert graph.counts()["retired_nodes"] == 1
    assert nodes["plugin"].id not in {n.id for n in graph.nodes(live=True)}
    assert nodes["plugin"].id in {n.id for n in graph.nodes(live=False)}


def test_retiring_a_node_retires_the_edges_that_touched_it(populated):
    _, commands, graph, _, nodes = populated
    commands.dispatch(RetireNode(node_id=nodes["plugin"].id, reason="removed", valid_to=999))

    # An edge to a node that no longer exists is not a relationship, and
    # traversal must not be able to walk into it.
    result = Traverser(graph).impact(nodes["lodash"].id)
    assert nodes["plugin"].id not in {item.node_id for item in result}


def test_retiring_an_edge_leaves_both_endpoints_alone(populated):
    _, commands, graph, _, nodes = populated
    edge = graph.edges_from(nodes["app"].id, kind=EdgeKind.DEPENDS_ON)[0]
    commands.dispatch(RetireEdge(edge_id=edge.id, valid_to=999))

    assert graph.counts()["nodes"] == 5
    assert graph.counts()["retired_edges"] == 1


def test_retiring_something_already_retired_reports_that_it_did_nothing(populated):
    _, _, graph, _, nodes = populated
    assert graph.retire_node(nodes["plugin"].id, valid_to=999)
    assert not graph.retire_node(nodes["plugin"].id, valid_to=999)


# --- the invalidation index ---------------------------------------------


def test_evidence_is_indexed_by_the_file_it_came_from(populated):
    _, _, graph, _, _ = populated

    assert graph.evidence_by_uri("file:///r/package-lock.json") == (LOCK.id,)
    assert graph.evidence_by_uri("file:///r/nothing.json") == ()


def test_the_index_names_exactly_what_a_changed_file_would_invalidate(populated):
    _, _, graph, _, nodes = populated
    subjects = graph.subjects_of_evidence([DYNAMIC.id])

    # Only the plugin node and its one edge rest on plugins.py.
    assert {kind for kind, _ in subjects} == {"node", "edge"}
    assert nodes["plugin"].id in {identifier for _, identifier in subjects}
    assert nodes["lodash"].id not in {identifier for _, identifier in subjects}


# --- search and statistics ----------------------------------------------


def test_nodes_can_be_found_by_name(populated):
    _, _, graph, _, _ = populated
    found = graph.search("lodash")

    assert [node.name for node in found] == ["lodash"]
    assert graph.search("") == ()
    assert graph.search("nothing-like-this") == ()


def test_search_survives_input_a_full_text_index_cannot_parse(populated):
    _, _, graph, _, _ = populated
    # Punctuation that is FTS5 syntax rather than a term. The scan takes over.
    assert isinstance(graph.search('"unbalanced ('), tuple)


def test_versions_of_one_package_share_a_coordinate(world):
    _, commands, graph, _ = world
    old, new = package("lodash", "4.17.20"), package("lodash", "4.17.21")
    commands.dispatch(RecordObservation(nodes=(old, new), source_uri="file:///r/lock.json"))

    found = graph.by_coordinate("pkg:npm/lodash")
    assert len(found) == 2
    assert {node.version for node in found} == {"4.17.20", "4.17.21"}


def test_the_confidence_distribution_shows_how_much_of_the_graph_is_guesswork(populated):
    _, _, graph, _, _ = populated
    distribution = graph.confidence_distribution()

    assert distribution["settled"] == 5
    assert distribution["weak"] == 1


def test_orphans_are_nodes_nothing_connects_to(world):
    _, commands, graph, _ = world
    connected_a, connected_b, alone = package("a"), package("b"), package("alone")
    commands.dispatch(RecordObservation(
        nodes=(connected_a, connected_b, alone),
        edges=(depends(connected_a, connected_b),),
        source_uri="file:///r/lock.json",
    ))

    assert graph.orphans() == (alone.id,)


def test_enrichments_are_recorded_against_their_subject(populated):
    _, commands, graph, _, nodes = populated
    commands.dispatch(DeriveEnrichment(
        subject=nodes["lodash"].id, attribute="risk", value="high", layer="L9",
        derived_from=(LOCK.id,), confidence=0.9, rationale="a CVE matched this purl",
    ))

    assert graph.counts()["enrichments"] == 1


def test_urn_identities_live_in_the_graph_alongside_purls(world):
    _, commands, graph, _ = world
    service = Node(
        kind=NodeKind.SERVICE, identity=Urn.create("service", "payments", "api"),
        evidence=(LOCK,),
    )
    commands.dispatch(RecordObservation(nodes=(service,), source_uri="file:///r/lock.json"))

    assert graph.node(service.id).identity.to_string() == "urn:slpie:service:payments/api"


# --- snapshots -----------------------------------------------------------


def test_an_unchanged_rescan_produces_an_identical_snapshot_id(populated):
    ledger, commands, graph, _, nodes = populated
    snapshots = SnapshotStore(graph)
    baseline = snapshots.seal(ledger_version=ledger.version, label="baseline")

    # The same discovery again: same files, same findings, nothing new.
    commands.dispatch(RecordObservation(
        nodes=tuple(nodes.values()), source_uri="file:///r/package-lock.json",
    ))
    rerun = snapshots.seal(ledger_version=baseline.ledger_version, label="rerun")

    assert rerun.id == baseline.id
    assert rerun.root_digest == baseline.root_digest


def test_a_snapshot_digest_ignores_the_order_things_were_discovered_in():
    assert root_digest(["a", "b"], ["x"]) == root_digest(["b", "a"], ["x"])
    assert root_digest(["a"], ["x"]) != root_digest(["a"], ["y"])


def test_a_snapshot_diff_reports_only_what_actually_changed(populated):
    ledger, commands, graph, _, nodes = populated
    snapshots = SnapshotStore(graph)
    baseline = snapshots.seal(ledger_version=ledger.version, label="baseline")

    added = package("new-thing")
    commands.dispatch(RecordObservation(
        nodes=(added,), edges=(depends(nodes["app"], added),),
        source_uri="file:///r/other.json",
    ))
    later = snapshots.seal(ledger_version=ledger.version, label="after")

    difference = snapshots.diff(baseline, later)
    assert difference.added_nodes == (added.id,)
    assert len(difference.added_edges) == 1
    assert not difference.empty
    assert difference.summary() == "+1 nodes, +1 edges"


def test_diffing_a_snapshot_against_itself_shows_nothing(populated):
    ledger, _, graph, _, _ = populated
    snapshots = SnapshotStore(graph)
    baseline = snapshots.seal(ledger_version=ledger.version, label="baseline")

    difference = snapshots.diff(baseline, baseline)
    assert difference.empty
    assert difference.summary() == "no change"


def test_a_retirement_shows_up_as_a_removal(populated):
    ledger, commands, graph, _, nodes = populated
    snapshots = SnapshotStore(graph)
    before = snapshots.seal(ledger_version=ledger.version, label="before")

    commands.dispatch(RetireNode(node_id=nodes["plugin"].id, reason="removed", valid_to=999))
    after = snapshots.seal(ledger_version=ledger.version, label="after")

    difference = snapshots.diff(before, after)
    assert difference.removed_nodes == (nodes["plugin"].id,)


def test_snapshots_can_be_looked_up_by_label(populated):
    ledger, _, graph, _, _ = populated
    snapshots = SnapshotStore(graph)
    sealed = snapshots.seal(ledger_version=ledger.version, label="baseline")

    assert snapshots.get("baseline").id == sealed.id
    assert snapshots.get(sealed.id).label == "baseline"
    assert snapshots.get("never-sealed") is None


# --- scale ---------------------------------------------------------------


@pytest.mark.slow
def test_traversal_stays_in_sql_on_a_graph_of_ten_thousand_nodes(world):
    """A chain 10k long, traversed in one query rather than 10k round trips."""
    _, _, graph, _ = world

    nodes = [package(f"pkg-{index}") for index in range(10_000)]
    graph.assert_nodes(nodes, sequence=1)
    graph.assert_edges(
        (depends(nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1)),
        sequence=1,
    )

    assert graph.counts()["nodes"] == 10_000

    started = time.monotonic()
    result = Traverser(graph).impact(nodes[50].id, max_depth=50)
    elapsed = time.monotonic() - started

    assert result.total == 50
    # Generous: the point is that this is a query, not a Python walk. A per-node
    # round trip would not come close.
    assert elapsed < 5.0
