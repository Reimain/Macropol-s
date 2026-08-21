"""Evidence, derived confidence, and the nodes and edges that require it.

These tests are about one invariant: no relationship exists without evidence,
and nobody gets to pick the confidence number.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from slpie.domain.edge import Edge, EdgeKind
from slpie.domain.evidence import (
    HEURISTIC_CEILING,
    INFERENCE_CEILING,
    Confidence,
    Evidence,
    EvidenceKind,
    SourceLocation,
    ValidationStatus,
    strongest,
)
from slpie.domain.identity import Purl, node_id
from slpie.domain.lifecycle import LifecycleState, RiskClass
from slpie.domain.node import OPEN, Node, NodeKind
from slpie.errors import EvidenceRequired


def evidence(kind, uri, *, line=1, extractor="test"):
    return Evidence(
        kind=kind,
        location=SourceLocation(uri, line=line),
        extractor=extractor,
        excerpt=f"{kind.value} at {uri}:{line}",
    )


LOCKFILE = evidence(EvidenceKind.LOCKFILE_PIN, "file:///r/package-lock.json", line=1204)
MANIFEST = evidence(EvidenceKind.MANIFEST_DECLARED, "file:///r/package.json", line=12)
IMPORT_A = evidence(EvidenceKind.STATIC_IMPORT, "file:///r/src/a.js", line=3)
IMPORT_B = evidence(EvidenceKind.STATIC_IMPORT, "file:///r/src/b.js", line=7)
GUESS = evidence(EvidenceKind.NAME_HEURISTIC, "file:///r/src/c.js", line=1)


# --- derivation ----------------------------------------------------------


def test_a_resolved_lockfile_pin_is_the_only_route_to_certainty():
    assert Confidence.combine([LOCKFILE]) == 1.0
    # Everything else is inference, however much of it accumulates.
    assert Confidence.combine([MANIFEST, IMPORT_A, IMPORT_B]) <= INFERENCE_CEILING


def test_independent_observations_reinforce_each_other():
    infra = evidence(EvidenceKind.IAC_DECLARATION, "file:///r/deploy.yaml")
    single = Confidence.combine([IMPORT_A])
    corroborated = Confidence.combine([IMPORT_A, infra])

    assert corroborated > single
    assert corroborated == pytest.approx(1 - (1 - 0.90) * (1 - 0.88))


def test_reading_the_same_file_twice_is_one_observation_not_two():
    once = Confidence.combine([IMPORT_A])
    twice = Confidence.combine([IMPORT_A, evidence(EvidenceKind.STATIC_IMPORT,
                                                   "file:///r/src/a.js", line=40)])

    assert twice == once


def test_guesswork_is_capped_no_matter_how_much_of_it_there_is():
    guesses = [
        evidence(EvidenceKind.NAME_HEURISTIC, f"file:///r/src/{n}.js") for n in range(30)
    ]
    derivation = Confidence.explain(guesses)

    assert derivation.value == HEURISTIC_CEILING
    assert derivation.capped
    assert "heuristic" in derivation.rule


def test_one_real_observation_lifts_a_pile_of_guesses_past_the_ceiling():
    guesses = [evidence(EvidenceKind.NAME_HEURISTIC, f"file:///r/{n}.js") for n in range(5)]

    assert Confidence.combine(guesses) == HEURISTIC_CEILING
    assert Confidence.combine([*guesses, MANIFEST]) > HEURISTIC_CEILING


def test_inference_never_rounds_up_into_a_fact():
    # Enough independent inferences to reach 0.9995 under plain noisy-OR.
    many = [MANIFEST, IMPORT_A, IMPORT_B,
            evidence(EvidenceKind.IAC_DECLARATION, "file:///r/deploy.yaml")]
    derivation = Confidence.explain(many)

    assert derivation.value == INFERENCE_CEILING
    assert derivation.capped
    assert derivation.value < 1.0


def test_a_derivation_shows_its_working():
    derivation = Confidence.explain([MANIFEST, IMPORT_A])

    assert len(derivation.groups) == 2
    contributions = derivation.to_dict()["contributions"]
    assert {c["kind"] for c in contributions} == {"manifest_declared", "static_import"}
    assert {c["source"] for c in contributions} == {
        "file:///r/package.json", "file:///r/src/a.js"
    }


def test_deriving_confidence_from_nothing_is_an_error_not_a_zero():
    with pytest.raises(EvidenceRequired):
        Confidence.combine([])


# --- the validation axis -------------------------------------------------


def test_validation_is_a_separate_axis_from_confidence():
    assert Confidence.status([IMPORT_A]) is ValidationStatus.UNVERIFIED
    assert Confidence.status([IMPORT_A, MANIFEST]) is ValidationStatus.CORROBORATED
    assert Confidence.status([LOCKFILE]) is ValidationStatus.CONFIRMED
    # A contradiction is not "lower confidence" — it is a different thing entirely.
    assert Confidence.status([LOCKFILE], contradicted=True) is ValidationStatus.CONTRADICTED


def test_the_strongest_observation_is_what_an_explanation_leads_with():
    assert strongest([GUESS, IMPORT_A, LOCKFILE]) is LOCKFILE
    assert strongest([]) is None


def test_identical_observations_are_the_same_evidence():
    duplicate = evidence(EvidenceKind.STATIC_IMPORT, "file:///r/src/a.js", line=3)
    assert duplicate.id == IMPORT_A.id
    assert evidence(EvidenceKind.STATIC_IMPORT, "file:///r/src/a.js", line=4).id != IMPORT_A.id


def test_evidence_survives_a_round_trip_through_serialisation():
    restored = Evidence.from_dict(IMPORT_A.to_dict())
    assert restored.id == IMPORT_A.id
    assert restored.location.reference == "a.js:3"


# --- the invariant on nodes and edges ------------------------------------


LODASH = Purl.parse("pkg:npm/lodash@4.17.21")
APP = Purl.parse("pkg:npm/app@1.0.0")


def test_a_node_cannot_be_asserted_without_evidence():
    with pytest.raises(EvidenceRequired):
        Node(kind=NodeKind.PACKAGE, identity=LODASH)


def test_an_edge_cannot_be_asserted_without_evidence():
    with pytest.raises(EvidenceRequired):
        Edge(kind=EdgeKind.DEPENDS_ON, src=node_id(APP), dst=node_id(LODASH))


def test_confidence_cannot_be_assigned_by_a_caller():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(IMPORT_A,))
    edge = Edge(kind=EdgeKind.DEPENDS_ON, src=node_id(APP), dst=node_id(LODASH),
                evidence=(IMPORT_A,))

    # There is no setter to route through — confidence is derived, structurally.
    assert type(node).confidence.fset is None
    assert type(edge).confidence.fset is None
    # And the evidence it derives from is itself immutable.
    with pytest.raises(FrozenInstanceError):
        node.evidence = ()
    with pytest.raises(FrozenInstanceError):
        edge.evidence = ()


def test_corroborating_a_node_raises_its_confidence_without_touching_the_number():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(IMPORT_A,))
    stronger = node.corroborated_by(MANIFEST)

    assert stronger.confidence > node.confidence
    assert stronger.validation is ValidationStatus.CORROBORATED
    # The original is untouched — every transition returns a new value.
    assert node.validation is ValidationStatus.UNVERIFIED


def test_corroborating_with_evidence_already_present_changes_nothing():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(IMPORT_A,))
    assert node.corroborated_by(IMPORT_A).confidence == node.confidence


def test_an_edge_that_loses_its_only_evidence_ceases_to_exist():
    edge = Edge(kind=EdgeKind.DEPENDS_ON, src=node_id(APP), dst=node_id(LODASH),
                evidence=(IMPORT_A,))

    assert edge.without(IMPORT_A.id) is None


def test_an_edge_with_other_support_survives_losing_one_source():
    edge = Edge(kind=EdgeKind.DEPENDS_ON, src=node_id(APP), dst=node_id(LODASH),
                evidence=(IMPORT_A, LOCKFILE))
    surviving = edge.without(IMPORT_A.id)

    assert surviving is not None
    assert surviving.evidence == (LOCKFILE,)
    assert surviving.confidence == 1.0


def test_a_declared_only_node_is_distinguishable_from_a_corroborated_one():
    declared = evidence(EvidenceKind.DECLARED, "file:///r/slpie.environment.yaml", line=8)
    node = Node(kind=NodeKind.SERVICE, identity=Purl.parse("pkg:npm/payments@1.0.0"),
                evidence=(declared,))

    assert node.declared_only
    assert not node.corroborated_by(IMPORT_A).declared_only


def test_retirement_closes_the_valid_interval_and_leaves_history_intact():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(LOCKFILE,), valid_from=100)
    retired = node.retired_at(200)

    assert node.live and node.valid_to == OPEN
    assert not retired.live
    assert retired.valid_to == 200
    assert retired.lifecycle is LifecycleState.RETIRED
    assert retired.valid_from == 100


def test_the_two_time_axes_move_independently():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(LOCKFILE,),
                valid_from=100, observed_at=150)
    corrected = node.superseded(300)

    # What we believed changed; when it was true in the ecosystem did not.
    assert corrected.superseded_at == 300
    assert corrected.valid_from == 100 and corrected.valid_to == OPEN
    assert not corrected.live


def test_classification_axes_are_independent_of_one_another():
    node = Node(kind=NodeKind.SERVICE, identity=LODASH, evidence=(LOCKFILE,))
    classified = node.classified(lifecycle=LifecycleState.DEPRECATED, risk=RiskClass.HIGH)

    assert classified.lifecycle is LifecycleState.DEPRECATED
    assert classified.risk is RiskClass.HIGH
    assert not classified.lifecycle.accepts_new_dependents
    # Setting one axis leaves the others where they were.
    assert classified.architecture is node.architecture


def test_blast_radius_follows_mechanical_edges_and_ignores_organisational_ones():
    assert EdgeKind.DEPENDS_ON.propagates_impact
    assert EdgeKind.CALLS.propagates_impact
    # A team changing does not break code; traversing ownership would make every
    # impact analysis span the whole organisation.
    assert not EdgeKind.OWNS.propagates_impact
    assert not EdgeKind.GOVERNS.propagates_impact


def test_inverse_edge_kinds_pair_up_symmetrically():
    for kind in EdgeKind:
        if (inverse := kind.inverse) is not None:
            assert inverse.inverse is kind


def test_an_edge_round_trips_through_serialisation_with_its_evidence():
    edge = Edge(kind=EdgeKind.DEPENDS_ON, src=node_id(APP), dst=node_id(LODASH),
                evidence=(LOCKFILE, IMPORT_A), qualifier="dev")
    payload = edge.to_dict(include_evidence=True)
    restored = Edge.from_dict(payload, [Evidence.from_dict(e) for e in payload["evidence"]])

    assert restored.id == edge.id
    assert restored.confidence == edge.confidence
    assert payload["derivation"]["value"] == 1.0


def test_a_node_round_trips_through_serialisation():
    node = Node(kind=NodeKind.PACKAGE, identity=LODASH, evidence=(LOCKFILE,),
                properties={"registry": "npmjs.org"})
    payload = node.to_dict(include_evidence=True)
    restored = Node.from_dict(payload, [Evidence.from_dict(e) for e in payload["evidence"]])

    assert restored.id == node.id
    assert restored.identity == node.identity
    assert restored.properties["registry"] == "npmjs.org"
