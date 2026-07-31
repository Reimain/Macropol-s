"""The layered pipeline, and the walk that has to terminate in a file and a line.

The headline case is `test_an_l4_conclusion_traces_back_to_a_file_and_a_line`. It
is the platform's central claim: any conclusion, however derived, can be walked
backwards to raw evidence. If that walk ever terminates somewhere else, the
answer is unfounded and every other feature is built on sand.

The second theme is abstention. A layer that raises must not cost the other nine,
and must not be invisible either — the gap travels with every answer built
afterwards, which is the difference between a low-confidence answer and a
misleading one.
"""

from __future__ import annotations

import pytest

from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.domain.finding import GapKind
from slpie.domain.reasoning import Enrichment, LayerNumber
from slpie.plugins.protocol import Observation
from slpie.reasoning import (
    BaseLayer,
    DiscoveryLayer,
    GraphConstructionLayer,
    LayerContext,
    LayerError,
    NormalizationLayer,
    Pipeline,
    SemanticLinkingLayer,
)


def evidence(uri: str, line: int, kind: EvidenceKind = EvidenceKind.MANIFEST_DECLARED,
             excerpt: str = "") -> Evidence:
    return Evidence(
        kind=kind, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=excerpt or f"{uri}:{line}",
    )


def repository() -> list[Observation]:
    """A small world: a range, its pin, and an import of another package."""
    return [
        Observation(
            kind="declares", subject="pkg:npm/lodash",
            evidence=evidence(
                "file:///r/package.json", 12, excerpt='"lodash": "^4.17.0"',
            ),
            properties={"range": "^4.17.0", "license": "MIT"},
        ),
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.21",
            evidence=evidence(
                "file:///r/package-lock.json", 1204, EvidenceKind.LOCKFILE_PIN,
                '"version": "4.17.21"',
            ),
        ),
        Observation(
            kind="imports", subject="urn:slpie:module:app/main",
            object="pkg:pypi/pyyaml",
            evidence=evidence(
                "file:///r/app/main.py", 3, EvidenceKind.STATIC_IMPORT, "import yaml",
            ),
            properties={"module": "yaml"},
        ),
        Observation(
            kind="declares", subject="pkg:pypi/PyYAML@6.0.1",
            evidence=evidence("file:///r/requirements.txt", 7, excerpt="PyYAML==6.0.1"),
        ),
    ]


# --- the central claim ---------------------------------------------------


def test_an_l4_conclusion_traces_back_to_a_file_and_a_line():
    pipeline = Pipeline()
    result = pipeline.run(repository(), element="repo")

    conclusions = [
        item for item in result.enrichments if item.layer == "semantic_linking"
    ]
    assert conclusions, "L4 reached a conclusion to trace"

    for conclusion in conclusions:
        found = pipeline.evidence_for(result, conclusion.id)
        assert found, f"{conclusion.attribute} traced to nothing"
        assert all(item.location.uri for item in found)
        assert any(item.location.line > 0 for item in found), (
            "an explanation that cannot name a line is not an explanation"
        )


def test_the_rendered_explanation_names_the_files_it_read():
    pipeline = Pipeline()
    result = pipeline.run(repository())

    resolved = next(
        item for item in result.enrichments
        if item.layer == "semantic_linking" and item.attribute == "resolved_version"
    )
    path = pipeline.explain(result, resolved.id)

    assert path.grounded
    assert any("package-lock.json:1204" in source for source in path.sources)
    assert "4.17.21" in path.render()


def test_a_chain_through_two_layers_renders_in_pipeline_order():
    pipeline = Pipeline()
    result = pipeline.run(repository())

    coordinate = next(
        item for item in result.enrichments
        if item.layer == "normalization" and item.attribute == "coordinate"
    )
    path = pipeline.explain(result, coordinate.id)

    assert len(path) >= 2, "L2 cites the L1 fact it normalised"
    assert [step.layer for step in path] == sorted(
        [step.layer for step in path],
        key=lambda name: int(LayerNumber[name.upper()]),
    )
    assert path.grounded


# --- the append-only rule ------------------------------------------------


def test_a_layer_cannot_assert_without_citing_anything():
    context = LayerContext()
    with pytest.raises(LayerError, match="without citing anything"):
        context.add(Enrichment(
            subject="pkg:npm/lodash", attribute="safe", value=True, layer="invented",
        ))


def test_a_layer_cannot_overwrite_what_an_earlier_one_concluded():
    context = LayerContext()
    first = context.add(Enrichment(
        subject="x", attribute="a", value=1, layer="l1", derived_from=("e1",),
    ))
    again = context.add(Enrichment(
        subject="x", attribute="a", value=99, layer="l1", derived_from=("e1",),
    ))

    assert again is first
    assert context.attribute("x", "a") == 1, "the original survived"


def test_the_trace_is_bounded_against_a_corrupted_chain():
    context = LayerContext()
    forged = Enrichment(
        subject="x", attribute="loop", value=1, layer="l1", derived_from=("self",),
    )
    context.enrichments["self"] = forged

    assert len(context.trace("self")) < 64


def test_roots_returns_the_evidence_ids_at_the_end_of_the_chain():
    context = LayerContext()
    leaf = context.add(Enrichment(
        subject="x", attribute="a", value=1, layer="discovery",
        derived_from=("evidence-1",),
    ))
    derived = context.add(Enrichment(
        subject="x", attribute="b", value=2, layer="normalization",
        derived_from=(leaf.id,),
    ))

    assert context.roots(derived.id) == ("evidence-1",)


# --- abstention ----------------------------------------------------------


def test_a_raising_layer_abstains_without_costing_the_others():
    class Broken(BaseLayer):
        name = "broken"
        number = LayerNumber.ARCHITECTURE_VALIDATION

        def execute(self, context, emit, step):
            raise RuntimeError("the world is not as I expected")

    pipeline = Pipeline()
    pipeline.add(Broken())
    result = pipeline.run(repository())

    assert len(result.abstained) == 1
    assert result.abstained[0].layer == "broken"
    assert not result.clean
    assert result.of_layer("semantic_linking").enrichments, "L4 still ran"


def test_an_abstention_becomes_a_gap_that_travels_with_the_answer():
    class Broken(BaseLayer):
        name = "broken"
        number = LayerNumber.IMPACT

        def execute(self, context, emit, step):
            raise ValueError("no impact model is loaded")

    pipeline = Pipeline()
    pipeline.add(Broken())
    result = pipeline.run(repository())

    gaps = [gap for gap in result.gaps if "broken" in gap.subject]
    assert len(gaps) == 1
    assert "no impact model is loaded" in gaps[0].detail
    assert gaps[0].confidence_impact > 0


def test_layers_run_in_number_order_regardless_of_registration_order():
    pipeline = Pipeline(layers=[
        SemanticLinkingLayer(), DiscoveryLayer(),
        GraphConstructionLayer(), NormalizationLayer(),
    ])

    assert [layer.name for layer in pipeline.layers] == [
        "discovery", "normalization", "graph_construction", "semantic_linking",
    ]
    result = pipeline.run(repository())
    assert result.clean, "ordering by number, not registration, kept it valid"


def test_semantic_linking_refuses_to_guess_when_the_graph_never_resolved():
    context = LayerContext(observations=tuple(repository()))
    result = SemanticLinkingLayer().run(context)

    assert result.abstained
    assert "needs a resolved graph" in result.errors[0]


# --- what the layers actually conclude -----------------------------------


def test_discovery_indexes_evidence_so_the_far_end_of_a_chain_resolves():
    context = LayerContext(observations=tuple(repository()))
    DiscoveryLayer().run(context)

    assert len(context.evidence) == 4
    assert all(item.location.uri for item in context.evidence.values())


def test_an_observation_without_evidence_is_counted_not_promoted():
    context = LayerContext(observations=(
        Observation(kind="declares", subject="pkg:npm/lodash", evidence=None),
    ))
    DiscoveryLayer().run(context)

    assert context.facts["ungrounded_observations"] == 1
    assert not context.enrichments


def test_normalization_reports_an_unrecognised_licence_rather_than_guessing():
    observations = [Observation(
        kind="declares", subject="pkg:pypi/mystery@1.0.0",
        evidence=evidence("file:///r/setup.py", 4),
        properties={"license": "BSD"},
    )]

    result = Pipeline().run(observations)
    refusals = result.context.facts["normalisation_refusals"]

    assert any("ambiguous" in refusal for refusal in refusals)
    assert not [
        item for item in result.enrichments if item.attribute == "license"
    ], "an ambiguous licence produced no licence enrichment"
    assert any(gap.kind is GapKind.PARSE_FAILURE for gap in result.gaps)


def test_normalization_canonicalises_two_spellings_onto_one_coordinate():
    observations = [
        Observation(
            kind="declares", subject="pkg:pypi/Typing_Extensions@4.9.0",
            evidence=evidence("file:///r/setup.py", 4),
        ),
        Observation(
            kind="declares", subject="pkg:pypi/typing-extensions@4.9.0",
            evidence=evidence("file:///r/requirements.txt", 2),
        ),
    ]

    result = Pipeline().run(observations)
    coordinates = {
        item.value for item in result.enrichments if item.attribute == "coordinate"
    }

    assert coordinates == {"pkg:pypi/typing-extensions"}
    assert result.context.facts["nodes"] == 1


def test_graph_construction_records_corroboration_with_its_sources_named():
    observations = [
        Observation(
            kind="declares", subject="pkg:pypi/flask@3.0.0",
            evidence=evidence("file:///r/requirements.txt", 1),
        ),
        Observation(
            kind="declares", subject="pkg:pypi/flask@3.0.0",
            evidence=evidence("file:///r/pyproject.toml", 9),
        ),
    ]

    result = Pipeline().run(observations)
    corroborated = next(
        item for item in result.enrichments if item.attribute == "corroborated_by"
    )

    assert len(corroborated.value) == 2
    assert "per file" in corroborated.rationale


def test_a_contradiction_between_two_lockfiles_becomes_a_named_conclusion():
    observations = [
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.21",
            evidence=evidence(
                "file:///r/a/package-lock.json", 3, EvidenceKind.LOCKFILE_PIN,
            ),
        ),
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.15",
            evidence=evidence(
                "file:///r/b/package-lock.json", 3, EvidenceKind.LOCKFILE_PIN,
            ),
        ),
    ]

    result = Pipeline().run(observations)
    contradiction = next(
        item for item in result.enrichments if item.attribute == "contradiction"
    )

    assert set(contradiction.value) == {"4.17.21", "4.17.15"}
    assert "not what they think it is" in contradiction.rationale


def test_an_unplaceable_observation_becomes_an_unresolved_dependency_gap():
    observations = [Observation(
        kind="declares", subject="lodash",
        evidence=evidence("file:///r/package.json", 3),
    )]

    result = Pipeline().run(observations)

    assert any(
        gap.kind is GapKind.UNRESOLVED_DEPENDENCY for gap in result.gaps
    )


def test_a_clean_run_reports_only_gaps_that_are_declared_limits():
    """Clean means no layer failed. It does not mean nothing was out of reach.

    This fixture declares an npm dependency and reads no npm code, so L5 declines
    to judge whether that declaration is unused — and says so as a gap. A run
    that reported no gap there would be claiming coverage it does not have,
    which is exactly what the layer exists to refuse.
    """
    result = Pipeline().run(repository(), element="repo")

    assert result.clean, "no layer abstained"
    assert not result.abstained
    assert all(
        "cannot be judged unused" in gap.detail for gap in result.gaps
    ), "every gap here is a declared limit, not a failure"
    assert result.context.facts["nodes"] == 3
    assert result.context.facts["links"] == 2


def test_the_result_serialises_for_the_ui_without_losing_the_gaps():
    class Broken(BaseLayer):
        name = "broken"
        number = LayerNumber.GOVERNANCE

        def execute(self, context, emit, step):
            raise RuntimeError("nope")

    pipeline = Pipeline()
    pipeline.add(Broken())
    body = pipeline.run(repository()).to_dict()

    assert body["clean"] is False
    assert any(
        "the broken layer abstained" in entry["detail"] for entry in body["gaps"]
    ), "an abstention that does not reach the UI is an answer that looks complete"
    assert [entry["number"] for entry in body["layers"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
