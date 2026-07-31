"""Phase 10 — the intelligence layers, and guidance that is never a bare value.

Four themes, and each one is a property the platform would be worse without:

**L5 distinguishes three things a naive diff conflates.** Imported-but-undeclared
is a defect; declared-but-unimported is waste; and *declared in an ecosystem whose
code was never read* is neither — it is the layer declining to rule. That third
case is the one that matters, because reporting a manifest-only tree's every
dependency as unused is a confident conclusion drawn from having looked at
nothing.

**L7 propagates confidence as a minimum and guards cycles by path.** A chain is as
strong as its weakest link, and a visited set would make the answer depend on
traversal order.

**L8 enumerates and does not recommend.** Every option carries what it changes and
who it breaks; which to take is a judgement about the codebase.

**`Guidance` is a type, not a convention.** Answer, reasoning, gaps, ranked next
questions and actions — and every question is a composition that type-checks
against the registry, so the console is never a dead end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slpie.compose import Composition, Context, Kind, registry
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.plugins.protocol import Observation
from slpie.reasoning import (
    ArchitectureValidationLayer,
    ImpactLayer,
    OptimizationLayer,
    Pipeline,
)
from slpie.reasoning.guidance import guidance_for, render


@pytest.fixture(scope="module")
def verbs():
    return registry()


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    """A real tree with something to say: a duplicate, and a range that is not one."""
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "version": "1.0.0",'
        ' "dependencies": {"lodash": "*", "left-pad": "^1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{"name": "demo", "lockfileVersion": 3, "packages": {'
        '"node_modules/lodash": {"version": "4.17.21"},'
        '"node_modules/left-pad": {"version": "1.3.0"}}}',
        encoding="utf-8",
    )
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "package-lock.json").write_text(
        '{"name": "sub", "lockfileVersion": 3, "packages": {'
        '"node_modules/lodash": {"version": "4.17.15"}}}',
        encoding="utf-8",
    )
    return tmp_path


def evidence(uri: str, line: int, kind: EvidenceKind = EvidenceKind.MANIFEST_DECLARED,
             excerpt: str = "") -> Evidence:
    return Evidence(
        kind=kind, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=excerpt or f"{uri}:{line}",
    )


def manifests_only() -> list[Observation]:
    """Two npm declarations and nothing that could import them."""
    return [
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.21",
            evidence=evidence("file:///r/package.json", 4, excerpt='"lodash"'),
        ),
        Observation(
            kind="declares", subject="pkg:npm/left-pad@1.3.0",
            evidence=evidence("file:///r/package.json", 5, excerpt='"left-pad"'),
        ),
    ]


def declared_and_used() -> list[Observation]:
    """One dependency declared and imported, one imported and never declared."""
    return [
        Observation(
            kind="declares", subject="pkg:pypi/pyyaml@6.0.1",
            evidence=evidence("file:///r/requirements.txt", 1, excerpt="PyYAML==6.0.1"),
        ),
        Observation(
            kind="imports", subject="urn:slpie:module:app/main",
            object="pkg:pypi/pyyaml@6.0.1",
            evidence=evidence(
                "file:///r/app/main.py", 3, EvidenceKind.STATIC_IMPORT, "import yaml",
            ),
        ),
        Observation(
            kind="imports", subject="urn:slpie:module:app/main",
            object="pkg:pypi/requests@2.31.0",
            evidence=evidence(
                "file:///r/app/main.py", 4, EvidenceKind.STATIC_IMPORT,
                "import requests",
            ),
        ),
    ]


# --- L5: the three deltas, and the one it refuses to call ------------------


def test_an_import_that_nothing_declares_is_reported_as_a_phantom():
    result = Pipeline().run(declared_and_used(), element="repo")

    phantoms = [
        item for item in result.enrichments
        if item.attribute == "phantom_dependency"
    ]
    assert phantoms, "requests is imported and never declared"
    assert any("requests" in item.subject for item in phantoms)
    assert result.context.facts["validation"]["phantom"] == 1


def test_a_phantom_traces_back_to_the_line_that_imported_it():
    pipeline = Pipeline()
    result = pipeline.run(declared_and_used(), element="repo")

    phantom = next(
        item for item in result.enrichments
        if item.attribute == "phantom_dependency"
    )
    found = pipeline.evidence_for(result, phantom.id)

    assert found, "a phantom that cites nothing is an accusation, not a finding"
    assert any("main.py:4" in item.location.reference for item in found)


def test_a_tree_of_manifests_declines_to_call_anything_unused():
    """The headline honesty case.

    Nothing here imports anything, because no code was read. Concluding that
    every declared dependency is unused would be a confident answer drawn from
    having examined nothing — the same over-reach the audit judge refuses with
    INDETERMINATE.
    """
    result = Pipeline().run(manifests_only(), element="repo")
    validation = result.context.facts["validation"]

    assert validation["declared"] == 2
    assert validation["unused"] == 0, "nothing was read that could have imported them"
    assert validation["undecidable"] == 2


def test_declining_to_judge_travels_as_a_gap_not_as_zero_confidence():
    """A layer certain it cannot decide is not a layer that believes nothing.

    Expressing the abstention as a step at confidence 0.0 drove the *whole*
    answer to zero, because a path's confidence is the minimum across its steps.
    One unreachable ecosystem would then have made every answer worthless.
    """
    result = Pipeline().run(manifests_only(), element="repo")

    assert any(
        "cannot be judged unused" in gap.detail for gap in result.gaps
    ), "the limit is reported"

    guidance = guidance_for(result, root=".")
    assert guidance.confidence > 0.5, (
        "an abstention about one ecosystem must not zero the whole answer"
    )
    assert not guidance.complete, "and it must still say the answer is limited"


def test_an_ecosystem_whose_code_was_read_is_still_judged():
    """Declining everywhere would be as useless as claiming everywhere."""
    observations = declared_and_used() + manifests_only()
    result = Pipeline().run(observations, element="repo")
    validation = result.context.facts["validation"]

    assert validation["undecidable"] == 2, "npm was declared and never scanned"
    assert validation["phantom"] == 1, "pypi was scanned, so pypi is judged"


def test_validation_refuses_to_run_without_a_resolved_graph():
    from slpie.reasoning.layer import LayerContext

    outcome = ArchitectureValidationLayer().run(LayerContext())

    assert outcome.abstained
    assert "resolved graph" in outcome.errors[0]


# --- L7: minimum confidence, and cycles ------------------------------------


def weak_chain() -> list[Observation]:
    """a → b by a certain edge, b → c by a dynamic load."""
    return [
        Observation(
            kind="depends_on", subject="pkg:npm/a@1.0.0", object="pkg:npm/b@1.0.0",
            evidence=evidence(
                "file:///r/package-lock.json", 3, EvidenceKind.LOCKFILE_PIN, '"b"',
            ),
        ),
        Observation(
            kind="depends_on", subject="pkg:npm/b@1.0.0", object="pkg:npm/c@1.0.0",
            evidence=evidence(
                "file:///r/b/loader.js", 9, EvidenceKind.DYNAMIC_LOAD, "require(name)",
            ),
        ),
    ]


def test_a_radius_reached_only_through_a_weak_edge_is_reported_as_weak():
    result = Pipeline().run(weak_chain(), element="repo")
    reaches = {item.node: item for item in result.context.facts["reaches"]}

    deepest = max(reaches.values(), key=lambda item: item.size)
    assert deepest.size == 2, "c is reached by both b and a"
    assert deepest.weakest == pytest.approx(EvidenceKind.DYNAMIC_LOAD.base_confidence)


def test_confidence_propagates_as_a_minimum_rather_than_a_product():
    """Four certain hops must not decay towards zero.

    Multiplying would report well-evidenced structure as doubt, which is the
    failure mode that makes people stop reading confidence numbers at all.
    """
    chain = [
        Observation(
            kind="depends_on",
            subject=f"pkg:npm/p{index}@1.0.0", object=f"pkg:npm/p{index + 1}@1.0.0",
            evidence=evidence(
                "file:///r/package-lock.json", index, EvidenceKind.LOCKFILE_PIN,
                f'"p{index}"',
            ),
        )
        for index in range(4)
    ]
    result = Pipeline().run(chain, element="repo")
    reaches = result.context.facts["reaches"]

    furthest = max(reaches, key=lambda item: item.size)
    assert furthest.size == 4
    assert furthest.weakest == pytest.approx(1.0)


def test_a_cycle_terminates_and_still_reports_every_member():
    cycle = [
        Observation(
            kind="depends_on", subject="pkg:npm/x@1.0.0", object="pkg:npm/y@1.0.0",
            evidence=evidence("file:///r/lock.json", 1, EvidenceKind.LOCKFILE_PIN, "y"),
        ),
        Observation(
            kind="depends_on", subject="pkg:npm/y@1.0.0", object="pkg:npm/x@1.0.0",
            evidence=evidence("file:///r/lock.json", 2, EvidenceKind.LOCKFILE_PIN, "x"),
        ),
    ]
    result = Pipeline().run(cycle, element="repo")

    assert result.context.facts["impact"]["nodes"] == 2
    assert not result.abstained, "a cycle must terminate, not hang or raise"


def test_impact_refuses_to_run_without_a_resolved_graph():
    from slpie.reasoning.layer import LayerContext

    outcome = ImpactLayer().run(LayerContext())

    assert outcome.abstained
    assert "resolved graph" in outcome.errors[0]


# --- the resolver's link endpoints -----------------------------------------


def test_a_link_points_at_the_node_the_pin_produced_not_the_range():
    """The endpoint must survive the identity becoming more specific.

    A bucket's node id changes when a lockfile pin arrives after a manifest
    range, and a link built before that moment pointed at an id no resolved node
    carried. The graph was then silently disconnected exactly where impact and
    reconciliation need it joined.
    """
    from slpie.linking.resolver import Resolver

    resolution = Resolver().resolve([
        Observation(
            kind="depends_on", subject="pkg:npm/app@1.0.0", object="pkg:npm/lodash",
            evidence=evidence("file:///r/package.json", 4, excerpt='"^4.17.0"'),
            properties={"range": "^4.17.0"},
        ),
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.21",
            evidence=evidence(
                "file:///r/package-lock.json", 12, EvidenceKind.LOCKFILE_PIN,
                '"4.17.21"',
            ),
        ),
    ])

    known = {entry.node_id for entry in resolution.resolved}
    endpoints = {link.source for link in resolution.links} | {
        link.target for link in resolution.links
    }
    assert endpoints <= known, "a link pointing at no node disconnects the graph"

    target = next(
        entry for entry in resolution.resolved
        if entry.node_id == resolution.links[0].target
    )
    assert target.identity == "pkg:npm/lodash@4.17.21"


# --- L8: enumerates, never recommends --------------------------------------


def duplicated() -> list[Observation]:
    return [
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.21",
            evidence=evidence(
                "file:///r/package-lock.json", 3, EvidenceKind.LOCKFILE_PIN, "4.17.21",
            ),
        ),
        Observation(
            kind="declares", subject="pkg:npm/lodash@4.17.15",
            evidence=evidence(
                "file:///r/sub/package-lock.json", 3, EvidenceKind.LOCKFILE_PIN,
                "4.17.15",
            ),
        ),
        Observation(
            kind="depends_on", subject="pkg:npm/app@1.0.0", object="pkg:npm/lodash",
            evidence=evidence("file:///r/package.json", 4, excerpt='"lodash": "*"'),
            properties={"range": "*"},
        ),
    ]


def test_one_coordinate_at_two_versions_is_reported_as_a_duplicate():
    result = Pipeline().run(duplicated(), element="repo")

    duplicates = [
        item for item in result.enrichments
        if item.attribute == "duplicate_versions"
    ]
    assert duplicates
    assert set(duplicates[0].value) == {"4.17.15", "4.17.21"}


def test_a_range_that_admits_anything_is_reported_as_no_constraint():
    result = Pipeline().run(duplicated(), element="repo")

    unconstrained = [
        item for item in result.enrichments
        if item.attribute == "unconstrained_range"
    ]
    assert unconstrained, "`*` is a hope, not a constraint"
    assert result.context.facts["optimization"]["unconstrained"] >= 1


def test_no_enrichment_recommends_an_upgrade_it_calls_safe_when_it_is_not():
    """A breaking option may be offered; it may never be labelled safe."""
    result = Pipeline().run(duplicated(), element="repo")

    for item in result.enrichments:
        if item.attribute != "safe_upgrade":
            continue
        assert "still admits it" in item.rationale, (
            "a safe upgrade states why it is safe, or it is not one"
        )


def test_optimization_refuses_to_run_without_a_resolved_graph():
    from slpie.reasoning.layer import LayerContext

    outcome = OptimizationLayer().run(LayerContext())

    assert outcome.abstained
    assert "resolved graph" in outcome.errors[0]


# --- Guidance: never a bare value ------------------------------------------


def test_guidance_carries_all_four_parts():
    result = Pipeline().run(duplicated(), element="repo")
    guidance = guidance_for(result, question="is this tree consistent?", root=".")

    assert guidance.answer, "the answer"
    assert guidance.reasoning.steps, "how it was reached"
    assert guidance.summary, "a sentence somebody can read"
    assert guidance.next_questions, "what to ask next"


def test_every_offered_question_is_a_composition_that_type_checks(verbs):
    """A question the platform cannot act on is a prompt, not guidance."""
    for observations in (duplicated(), declared_and_used(), manifests_only()):
        result = Pipeline().run(observations, element="repo")
        guidance = guidance_for(result, root=".")

        for question in guidance.next_questions:
            pipeline = question.parameters.get("pipeline")
            assert pipeline, f"{question.text!r} offers no way to answer it"
            validation = Composition.read(pipeline, verbs=verbs).validate()
            assert validation.ok, f"{pipeline!r}: {validation.explain()}"


def test_questions_are_ranked_by_what_answering_them_would_buy():
    result = Pipeline().run(duplicated(), element="repo")
    guidance = guidance_for(result, root=".")

    gains = [item.information_gain for item in guidance.next_questions]
    assert gains == sorted(gains, reverse=True)


def test_a_clean_tree_still_offers_the_move_that_verifies_it():
    """Nothing wrong is not nothing to do — the next move is checking."""
    result = Pipeline().run(declared_and_used(), element="repo")
    guidance = guidance_for(result, root=".")

    assert guidance.next_questions, "a dead end is never the right answer"


def test_the_summary_never_says_nothing_contradictory_while_reporting_one():
    result = Pipeline().run(duplicated(), element="repo")
    guidance = guidance_for(result, root=".")

    if guidance.answer["contradictions"]:
        assert "nothing contradictory" not in guidance.summary


def test_an_action_names_the_package_rather_than_its_digest():
    result = Pipeline().run(duplicated(), element="repo")
    guidance = guidance_for(result, root=".")

    for action in guidance.actions:
        assert not (len(action.target) == 40 and action.target.isalnum()), (
            f"{action.summary!r} names a node digest nobody can act on"
        )


def test_free_actions_are_offered_before_the_ones_that_cost_something():
    result = Pipeline().run(duplicated(), element="repo")
    guidance = guidance_for(result, root=".")

    breaking = [action.breaking for action in guidance.actions]
    assert breaking == sorted(breaking)


def test_the_rendering_states_the_limits_alongside_the_answer():
    result = Pipeline().run(manifests_only(), element="repo")
    text = render(guidance_for(result, root="."))

    assert "limiting this answer" in text
    assert "worth asking next" in text
    assert "confidence" in text


# --- the verbs --------------------------------------------------------------


def test_ask_produces_guidance_and_nothing_else_does(verbs):
    producers = [verb.name for verb in verbs if verb.produces is Kind.GUIDANCE]

    assert producers == ["ask"]
    assert verbs.require("ask").consumes is Kind.ENRICHMENTS, (
        "the only route to an answer is through the layers that derived it"
    )


def test_ask_over_a_real_tree_answers_with_its_limits(repository, verbs):
    result = Composition.read(
        f"discover {repository} | reason | ask", verbs=verbs,
    ).run(Context(root=str(repository)))

    assert result.ok
    assert result.flow.kind is Kind.GUIDANCE
    assert "answer" in result.flow.facts
    assert result.flow.facts["questions"], "a console that offers nothing is a wall"


def test_appending_ask_does_not_launder_away_an_upstream_gap(repository, verbs):
    without = Composition.read(
        f"discover {repository} | reason", verbs=verbs,
    ).run(Context(root=str(repository))).flow
    with_ask = Composition.read(
        f"discover {repository} | reason | ask", verbs=verbs,
    ).run(Context(root=str(repository))).flow

    assert {gap.id for gap in without.gaps} <= {gap.id for gap in with_ask.gaps}


def test_radius_answers_without_a_database(repository, verbs):
    result = Composition.read(
        f"discover {repository} | reason | radius", verbs=verbs,
    ).run(Context(root=str(repository)))

    assert result.ok
    assert result.flow.kind is Kind.IMPACT


def test_a_radius_for_a_name_that_matches_nothing_says_so(repository, verbs):
    """An empty radius must not read as `nothing depends on it`."""
    result = Composition.read(
        f"discover {repository} | reason | radius --package nosuchpackage",
        verbs=verbs,
    ).run(Context(root=str(repository)))

    assert result.ok
    assert result.flow.empty
    assert any("was not found" in gap.detail for gap in result.flow.gaps)


def test_options_enumerates_with_the_cost_of_each(repository, verbs):
    result = Composition.read(
        f"discover {repository} | reason | options", verbs=verbs,
    ).run(Context(root=str(repository)))

    assert result.ok
    for option in result.flow.items:
        assert "breaking" in option, "an option without its consequence is a trap"
        assert option["why"], "and one without a reason is an instruction"


def test_options_safe_keeps_only_what_breaks_nothing(repository, verbs):
    result = Composition.read(
        f"discover {repository} | reason | options --safe", verbs=verbs,
    ).run(Context(root=str(repository)))

    assert all(not option["breaking"] for option in result.flow.items)


# --- the console -----------------------------------------------------------


MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
codebase:
  - root: ./services/payments
"""


@pytest.fixture()
def engine(tmp_path):
    from slpie.engine import Engine

    built = Engine.from_text(MANIFEST)
    built.declare()
    built.simulate(root=tmp_path / "world")
    built.attach(wanted=("file-read", "lockfile-read", "static-analysis"))
    yield built
    built.close()


def test_a_question_asked_before_any_scan_says_so_rather_than_guessing(engine):
    guidance = engine.ask("what is wrong here?")

    assert guidance.answer is None
    assert "nothing has been scanned" in guidance.summary
    assert guidance.next_questions, "and it says what would make the answer possible"


def test_the_console_answers_from_what_the_layers_concluded(engine):
    engine.scan()
    guidance = engine.ask("what is wrong here?")

    assert guidance.answer is not None, "the console is no longer a placeholder"
    assert guidance.reasoning.steps
    assert guidance.next_questions


def test_the_console_merges_the_environment_gaps_with_the_pipeline_gaps(engine):
    engine.scan()
    guidance = engine.ask("what is wrong here?")

    environment = {gap.id for gap in engine.gaps()}
    assert environment <= {gap.id for gap in guidance.gaps}, (
        "a refused capability limits this answer as much as an unread ecosystem"
    )


def test_an_intelligence_verb_refuses_what_reason_did_not_produce(verbs):
    from slpie.compose.flow import Flow
    from slpie.compose.verb import Context as VerbContext, VerbError

    flow = Flow(kind=Kind.ENRICHMENTS, value=("not a pipeline result",))
    with pytest.raises(VerbError, match="reason"):
        verbs.require("ask").run(flow, {}, VerbContext())
