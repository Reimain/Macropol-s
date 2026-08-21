"""Degree of interest — the render set is bounded by the question, not the estate.

The property under test throughout is Furnas's: what gets drawn is decided by
importance minus distance, distance is hops along the graph, and everything that
loses is elided with a count rather than dropped. A test suite that only checked
the arithmetic would miss the two things that actually matter — that the same
question always yields the same picture, and that the picture says how much of
the estate it is not showing.
"""

from __future__ import annotations

import pytest

from slpie.domain import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceKind,
    Node,
    NodeKind,
    Purl,
    SourceLocation,
)
from slpie.domain.lifecycle import Severity
from slpie.graph import SqliteGraph
from slpie.graph.interest import (
    BUDGET,
    Elision,
    Field,
    Interest,
    Signals,
    Surveyor,
    score,
    survey,
)

LOCK = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=1),
    extractor="npm",
    excerpt='"x": "1.0.0"',
)
DECLARED = Evidence(
    kind=EvidenceKind.DECLARED,
    location=SourceLocation("file:///r/slpie.environment.yaml", line=4),
    extractor="manifest",
    excerpt="- root: ./services/payments",
)


def package(name: str, evidence: Evidence = LOCK) -> Node:
    return Node(
        kind=NodeKind.PACKAGE,
        identity=Purl.create("npm", name, version="1.0.0"),
        evidence=(evidence,),
    )


def depends(src: Node, dst: Node) -> Edge:
    return Edge(kind=EdgeKind.DEPENDS_ON, src=src.id, dst=dst.id, evidence=(LOCK,))


def mark(node_id: str, distance: int, importance: float, kind: str = "package") -> Interest:
    return Interest(
        node_id=node_id, display=node_id, kind=kind, distance=distance,
        importance=importance, score=score(importance, distance),
    )


@pytest.fixture
def chain():
    """A ten-link chain, so hop distance is unambiguous and easy to reason about."""
    graph = SqliteGraph()
    nodes = [package(f"p{index}") for index in range(10)]
    for node in nodes:
        graph.assert_node(node)
    for left, right in zip(nodes, nodes[1:]):
        graph.assert_edge(depends(left, right))
    yield graph, nodes
    graph.close()


# -- importance is weighed, and every part of it names itself ----------------


def test_importance_is_derived_from_signals_the_platform_already_computed():
    plain = Signals(degree=1)
    linked = Signals(degree=40)
    assert linked.weigh()[0] > plain.weigh()[0]


def test_every_contribution_to_importance_says_what_it_was():
    weight, reasons = Signals(
        degree=3, severity=Severity.CRITICAL, boundary=True, declared_only=True,
    ).weigh()
    assert weight > 0
    assert "3 links" in reasons
    assert "critical finding" in reasons
    assert "on a declared boundary" in reasons
    assert "declared, never observed" in reasons


def test_a_node_nothing_is_known_about_scores_zero_and_says_nothing():
    weight, reasons = Signals().weigh()
    assert weight == 0.0
    assert reasons == ()


def test_degree_saturates_so_one_hub_cannot_buy_the_field():
    hundred = Signals(degree=100).weigh()[0]
    thousand = Signals(degree=1000).weigh()[0]
    # Both are at the ceiling. A linear weight would make the second ten times
    # the first and bury everything else in the estate underneath it.
    assert hundred == pytest.approx(thousand)


def test_a_critical_finding_outranks_a_hub():
    assert Signals(severity=Severity.CRITICAL).weigh()[0] > Signals(degree=64).weigh()[0]


# -- distance is hops, and it costs ------------------------------------------


def test_interest_falls_off_with_hops_not_with_pixels():
    near = score(0.8, 1)
    far = score(0.8, 5)
    assert near > far


def test_a_severe_node_stays_visible_further_than_a_popular_one():
    """The calibration in `HOP_COST`, pinned rather than asserted in a comment.

    Four hops out the finding is still the thing worth drawing; five hops out it
    is not. Retuning the constant without meaning to breaks this.
    """
    severe = Signals(severity=Severity.CRITICAL).weigh()[0]
    popular = Signals(degree=64).weigh()[0]
    assert score(severe, 4) > score(popular, 1)
    assert score(severe, 5) < score(popular, 1)


def test_independent_signals_corroborate_rather_than_average_away():
    severe = Signals(severity=Severity.CRITICAL).weigh()[0]
    both = Signals(severity=Severity.CRITICAL, degree=64).weigh()[0]
    assert both > severe


# -- the cut ------------------------------------------------------------------


def test_the_render_set_is_bounded_by_the_budget():
    field = survey([mark(f"n{i}", 1, 0.5 + i / 1000) for i in range(500)], budget=25)
    assert len(field.rendered) == 25
    assert field.hidden == 475


def test_the_same_selection_yields_the_same_set():
    candidates = [mark(f"n{i}", i % 4, 0.5) for i in range(200)]
    first = survey(candidates, focus=("n0",), budget=30)
    second = survey(list(reversed(candidates)), focus=("n0",), budget=30)
    assert [item.node_id for item in first] == [item.node_id for item in second]
    assert first.threshold == second.threshold


def test_the_focus_always_renders_however_badly_it_scores():
    candidates = [mark(f"n{i}", 1, 0.9) for i in range(50)]
    candidates.append(mark("chosen", 0, 0.0))
    field = survey(candidates, focus=("chosen",), budget=5)
    assert "chosen" in {item.node_id for item in field.rendered}


def test_what_falls_below_the_threshold_is_counted_never_dropped():
    candidates = [mark(f"a{i}", 1, 0.9, kind="package") for i in range(5)]
    candidates += [mark(f"b{i}", 4, 0.1, kind="service") for i in range(12)]
    field = survey(candidates, budget=5)
    assert field.hidden == 12
    services = [item for item in field.elided if item.key == "service"]
    assert services and services[0].count == 12
    assert services[0].nearest == 4
    assert "12 elided" in field.summary()


def test_zoom_moves_the_threshold_rather_than_the_render():
    candidates = [mark(f"n{i}", i % 6, 0.6) for i in range(120)]
    tight = survey(candidates, threshold=0.4, budget=BUDGET)
    loose = survey(candidates, threshold=-0.5, budget=BUDGET)
    assert len(loose.rendered) > len(tight.rendered)
    # Descending reveals more of the neighbourhood; it does not redraw the same
    # marks larger, so everything visible at the tight threshold stays visible.
    assert {item.node_id for item in tight} <= {item.node_id for item in loose}


def test_an_empty_selection_renders_nothing_at_all():
    field = survey([])
    assert len(field) == 0
    assert "nothing" in field.summary()


# -- over a real graph --------------------------------------------------------


def test_the_walk_measures_hops_along_the_graph(chain):
    graph, nodes = chain
    field = Surveyor(graph).field([nodes[0].id], horizon=9, budget=100)
    distances = {item.node_id: item.distance for item in field.rendered}
    assert distances[nodes[0].id] == 0
    assert distances[nodes[3].id] == 3
    assert distances[nodes[9].id] == 9


def test_the_walk_goes_both_ways_because_interest_is_not_directional(chain):
    graph, nodes = chain
    field = Surveyor(graph).field([nodes[5].id], horizon=2, budget=100)
    reached = {item.node_id for item in field.rendered}
    assert nodes[3].id in reached      # what this depends on
    assert nodes[7].id in reached      # what depends on this


def test_the_horizon_bounds_the_neighbourhood(chain):
    graph, nodes = chain
    field = Surveyor(graph).field([nodes[0].id], horizon=2, budget=100)
    assert max(item.distance for item in field.rendered) <= 2
    assert nodes[9].id not in {item.node_id for item in field.rendered}


def test_a_selection_that_names_nothing_answers_with_an_empty_field(chain):
    graph, _nodes = chain
    assert len(Surveyor(graph).field([])) == 0


def test_a_declared_but_never_observed_node_earns_its_place(chain):
    graph, nodes = chain
    ghost = package("ghost", evidence=DECLARED)
    graph.assert_node(ghost)
    graph.assert_edge(depends(nodes[0], ghost))

    field = Surveyor(graph).field([nodes[0].id], horizon=1, budget=100)
    found = next(item for item in field.rendered if item.node_id == ghost.id)
    assert "declared, never observed" in found.reasons


def test_an_injected_severity_reaches_the_score(chain):
    graph, nodes = chain
    plain = Surveyor(graph).field([nodes[0].id], horizon=3, budget=100)
    graded = Surveyor(
        graph, severities={nodes[2].id: Severity.CRITICAL},
    ).field([nodes[0].id], horizon=3, budget=100)

    before = next(item for item in plain if item.node_id == nodes[2].id)
    after = next(item for item in graded if item.node_id == nodes[2].id)
    assert after.score > before.score
    assert "critical finding" in after.reasons


def test_a_boundary_member_earns_its_place(chain):
    graph, nodes = chain
    inside = {nodes[4].id}
    field = Surveyor(graph, boundary=lambda node: node.id in inside).field(
        [nodes[0].id], horizon=5, budget=100,
    )
    found = next(item for item in field.rendered if item.node_id == nodes[4].id)
    assert "on a declared boundary" in found.reasons


def test_the_walk_reports_when_it_stopped_early(chain):
    graph, nodes = chain
    field = Surveyor(graph).field([nodes[0].id], horizon=9, budget=100, reach=4)
    assert field.truncated
    assert "the walk stopped" in field.summary()


def test_degree_is_counted_for_every_node_the_walk_reached(chain):
    graph, nodes = chain
    field = Surveyor(graph).field([nodes[0].id], horizon=9, budget=100, reach=4)
    # Truncated or not, no node may be reported with an importance that silently
    # understates it — every rendered node's degree was actually measured.
    interior = [
        item for item in field.rendered
        if item.node_id in {node.id for node in nodes[1:4]}
    ]
    assert interior
    assert all(item.reasons for item in interior)


def test_the_field_renders_as_a_body_the_interface_can_draw(chain):
    graph, nodes = chain
    body = Surveyor(graph).field([nodes[0].id], horizon=3, budget=4).to_dict()
    assert body["focus"] == [nodes[0].id]
    assert body["budget"] == 4
    assert isinstance(body["rendered"], list)
    assert all("reasons" in item for item in body["rendered"])
    assert "summary" in body


# -- through the verb ---------------------------------------------------------


def test_interest_is_reachable_as_a_verb():
    from slpie.compose.registry import registry

    found = registry().get("interest")
    assert found is not None
    assert found.consumes.value == "nodes"
    assert found.examples


def test_interest_refuses_a_selection_it_does_not_have():
    from slpie.compose.flow import Flow, Kind
    from slpie.compose.verb import VerbError
    from slpie.compose.verbs.environment import _interest

    class _Context:
        def require_engine(self, _name):
            return object()

    with pytest.raises(VerbError, match="needs a selection"):
        _interest(Flow(Kind.NODES, ()), {}, _Context())


def test_the_verb_reads_the_manifest_s_own_boundary_rule(tmp_path):
    """The wiring, not the mechanism.

    The unit tests above inject a predicate, which proves the surveyor weighs
    boundary membership and proves nothing about whether the verb ever asks the
    manifest. That gap is the vacuous-pass shape §29 exists to close, and it hid
    a real defect here — the verb called a method the manifest does not have.
    """
    from slpie.compose.pipeline import Composition
    from slpie.compose.registry import registry
    from slpie.compose.verb import Context
    from slpie.engine import Engine

    manifest = tmp_path / "slpie.environment.yaml"
    manifest.write_text(
        "apiVersion: slpie/v1\n"
        "environment: interest-check\n"
        "target: simulated\n"
        "security:\n"
        "  concerns: [pci-dss]\n"
        "  boundaries:\n"
        "    - name: cardholder-data\n"
        "      contains: [payments]\n"
        "codebase:\n"
        "  - root: ./services/payments\n"
        "    team: payments\n"
        "  - root: ./services/orders\n"
        "    team: orders\n",
        encoding="utf-8",
    )

    engine = Engine.from_manifest(manifest)
    engine.declare()
    result = Composition.read(
        "graph --limit 20 | interest --horizon 3", verbs=registry(),
    ).run(Context(engine=engine, root=str(tmp_path)))

    assert result.ok, result.error
    assert result.flow.facts["boundaries"] is True
    rendered = {row["display"]: row for row in result.flow.value["rendered"]}
    assert "on a declared boundary" in rendered["payments"]["reasons"]
    assert "on a declared boundary" not in rendered["orders"]["reasons"]
    # The boundary is what makes payments the thing to draw first.
    assert rendered["payments"]["score"] > rendered["orders"]["score"]
