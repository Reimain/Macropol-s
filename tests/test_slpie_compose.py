"""Composition — the spine every surface projects from.

Three headline properties, each asserted rather than demonstrated:

1. **The projection is total.** Every registered verb has a manual-ready summary,
   an executable example, and a place in the type graph. Registering a verb
   without wiring it is a test failure here, not a drift somebody finds later.
2. **Provenance survives composition.** A gap raised in stage one is still on the
   flow four stages later, and the reasoning path still terminates in file:line.
3. **Type errors are caught before execution.** An invalid composition runs
   *nothing* — asserted with a verb that would record a side effect if reached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slpie.compose import (
    Composition,
    CompositionError,
    Context,
    Flow,
    Kind,
    Param,
    ParseError,
    Verb,
    VerbError,
    VerbRegistry,
    parse,
    registry,
    render,
    run,
)
from slpie.domain.finding import Gap, GapKind
from slpie.domain.reasoning import ReasoningStep


@pytest.fixture()
def verbs() -> VerbRegistry:
    return registry()


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    """A tiny real tree — a manifest and its lockfile, which disagree."""
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "version": "1.0.0",'
        ' "dependencies": {"lodash": "^3.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{"name": "demo", "lockfileVersion": 3, "packages": {'
        '"node_modules/lodash": {"version": "4.17.21"}}}',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    return tmp_path


# --- the projection is total ---------------------------------------------


def test_every_verb_is_ready_for_every_surface(verbs: VerbRegistry):
    """The manual, the CLI help and the UI palette all read these fields."""
    for verb in verbs:
        assert verb.summary, f"{verb.name} has no summary; its manual page is blank"
        assert verb.examples, f"{verb.name} has no example; the cookbook cannot show it"
        assert verb.usage.startswith(verb.name)
        assert verb.produces is not Kind.NOTHING


def test_every_verb_appears_in_the_projection_the_clients_read(verbs: VerbRegistry):
    body = verbs.to_dict()

    assert {entry["name"] for entry in body["verbs"]} == set(verbs.names)
    assert set(body["graph"]) == set(verbs.names)
    assert body["sources"], "at least one verb can start a pipeline"


def test_every_example_parses_and_type_checks(verbs: VerbRegistry):
    """A documented composition that cannot run is worse than an undocumented one."""
    for verb in verbs:
        for example in verb.examples:
            composition = Composition.read(example, verbs=verbs)
            validation = composition.validate()
            assert validation.ok, f"{verb.name}: {example!r} — {validation.explain()}"


def test_a_duplicate_verb_name_is_refused_rather_than_shadowing(verbs: VerbRegistry):
    existing = verbs.verbs[0]
    with pytest.raises(VerbError, match="already registered"):
        VerbRegistry([existing]).add(existing)


def test_builtins_register_through_the_same_path_a_plugin_uses():
    class Plugin:
        def verbs(self):
            return (Verb(
                name="acme-audit", summary="a third-party verb",
                produces=Kind.REPORT, consumes=Kind.ANY,
                examples=("discover . | acme-audit",),
                run=lambda flow, args, ctx: flow.then(
                    Kind.REPORT, {"ok": True}, stage="acme-audit",
                ),
            ),)

    fresh = VerbRegistry()
    assert fresh.register_provider(Plugin()) == 1
    assert "acme-audit" in fresh
    assert fresh.origin_of("acme-audit") == "plugin"


# --- typed pipes ---------------------------------------------------------


def test_an_invalid_composition_names_both_kinds(verbs: VerbRegistry):
    validation = Composition.read("discover . | impact", verbs=verbs).validate()

    assert not validation.ok
    explanation = validation.explain()
    assert "OBSERVATIONS" in explanation
    assert "NODES" in explanation
    assert "impact" in explanation


def test_an_invalid_composition_executes_nothing(verbs: VerbRegistry):
    """Asserted with a verb that would leave a mark if it were ever reached."""
    reached: list[str] = []

    fresh = VerbRegistry()
    fresh.add(Verb(
        name="mark", summary="records that it ran", produces=Kind.OBSERVATIONS,
        examples=("mark",),
        run=lambda flow, args, ctx: (
            reached.append("yes"),
            flow.then(Kind.OBSERVATIONS, (), stage="mark"),
        )[1],
    ))
    fresh.add(Verb(
        name="needs-nodes", summary="consumes nodes", consumes=Kind.NODES,
        produces=Kind.IMPACT, examples=("mark | needs-nodes",),
        run=lambda flow, args, ctx: flow.then(Kind.IMPACT, (), stage="needs-nodes"),
    ))

    with pytest.raises(CompositionError):
        Composition.read("mark | needs-nodes", verbs=fresh).run()

    assert reached == [], "the first stage ran despite the composition being invalid"


def test_a_transform_verb_cannot_start_a_pipeline(verbs: VerbRegistry):
    validation = Composition.read("link", verbs=verbs).validate()

    assert not validation.ok
    assert "starts the pipeline" in validation.explain()


def test_a_polymorphic_filter_passes_the_kind_through(verbs: VerbRegistry):
    composition = Composition.read("discover . | head | link", verbs=verbs)

    assert composition.validate().ok, "head must not erase the kind flowing through"
    assert composition.produces is Kind.RESOLUTION


def test_an_unknown_verb_suggests_what_was_probably_meant(verbs: VerbRegistry):
    validation = Composition.read("discove .", verbs=verbs).validate()

    assert not validation.ok
    assert "discover" in validation.explain()


def test_nearest_never_suggests_the_opposite_of_what_was_typed(verbs: VerbRegistry):
    """Edit distance would offer `attach` for `detach`; a shared prefix will not."""
    assert "attach" not in verbs.nearest("zzzz")


def test_an_unknown_parameter_is_refused_with_the_known_ones_listed(verbs: VerbRegistry):
    validation = Composition.read("discover . --dpeth 3", verbs=verbs).validate()

    assert not validation.ok
    assert "--dpeth" in validation.explain()


def test_a_parameter_that_is_not_a_number_is_refused_not_coerced(verbs: VerbRegistry):
    validation = Composition.read("discover . --limit banana", verbs=verbs).validate()

    assert not validation.ok
    assert "not a whole number" in validation.explain()


# --- the type graph drives the manual and the planner --------------------


def test_successors_answer_what_may_follow_a_verb(verbs: VerbRegistry):
    names = {verb.name for verb in verbs.successors("discover")}

    assert "link" in names
    assert "reason" in names
    assert "impact" not in names, "impact consumes NODES, not OBSERVATIONS"


def test_predecessors_answer_what_may_precede_a_verb(verbs: VerbRegistry):
    names = {verb.name for verb in verbs.predecessors("constraints")}

    assert "link" in names
    assert verbs.predecessors("discover") == (), "a source verb has no predecessor"


def test_a_route_exists_from_a_source_verb_to_every_reachable_kind(verbs: VerbRegistry):
    routes = verbs.paths_to(Kind.FINDINGS, max_length=4)

    assert routes
    assert routes[0][0] in {verb.name for verb in verbs.sources()}
    assert all(len(route) <= 4 for route in routes)
    assert ("discover", "link", "findings") in routes


def test_shorter_routes_come_first_so_the_planner_prefers_them(verbs: VerbRegistry):
    routes = verbs.paths_to(Kind.RESOLUTION, max_length=4)
    lengths = [len(route) for route in routes]

    assert lengths == sorted(lengths)


# --- provenance survives composition ------------------------------------


def test_a_gap_raised_in_stage_one_is_still_present_four_stages_later():
    fresh = VerbRegistry()
    gap = Gap(
        kind=GapKind.CAPABILITY_REFUSED, subject="vendored/",
        detail="static analysis was refused", confidence_impact=0.3,
    )

    fresh.add(Verb(
        name="source", summary="raises a gap", produces=Kind.OBSERVATIONS,
        examples=("source",),
        run=lambda flow, args, ctx: flow.then(
            Kind.OBSERVATIONS, (1, 2, 3), stage="source", gaps=[gap],
            steps=[ReasoningStep(claim="read three things", operation="discover")],
        ),
    ))
    from slpie.compose.verbs import shaping

    for verb in shaping.verbs():
        fresh.add(verb)

    result = Composition.read(
        "source | head --count 2 | sort | count | explain", verbs=fresh,
    ).run()

    assert result.ok
    assert gap.id in {item.id for item in result.flow.gaps}
    assert "static analysis was refused" in result.flow.facts["explanation"]


def test_gaps_discount_the_confidence_rather_than_merely_annotating_it():
    plain = Flow(kind=Kind.OBSERVATIONS, value=(1,))
    limited = Flow(
        kind=Kind.OBSERVATIONS, value=(1,),
        gaps=(Gap(
            kind=GapKind.CAPABILITY_REFUSED, subject="x", detail="refused",
            confidence_impact=0.5,
        ),),
    )

    assert limited.confidence < plain.confidence


def test_a_stage_cannot_launder_away_a_gap_an_earlier_stage_raised():
    gap = Gap(kind=GapKind.PARSE_FAILURE, subject="x", detail="unreadable")
    first = Flow.start().then(Kind.OBSERVATIONS, (), stage="a", gaps=[gap])
    second = first.then(Kind.OBSERVATIONS, (), stage="b", gaps=[])

    assert gap.id in {item.id for item in second.gaps}


def test_a_gap_reported_by_two_stages_is_counted_once():
    gap = Gap(kind=GapKind.PARSE_FAILURE, subject="x", detail="unreadable")
    flow = (
        Flow.start()
        .then(Kind.OBSERVATIONS, (), stage="a", gaps=[gap])
        .then(Kind.OBSERVATIONS, (), stage="b", gaps=[gap])
    )

    assert len(flow.gaps) == 1


def test_shaping_steps_do_not_make_a_grounded_answer_look_unfounded():
    grounded = Flow.start().then(
        Kind.OBSERVATIONS, (1,), stage="discover",
        steps=[ReasoningStep(
            claim="read a file", operation="discover",
            evidence=(_evidence(),),
        )],
    )
    filtered = grounded.then(
        Kind.OBSERVATIONS, (1,), stage="head",
        steps=[ReasoningStep(claim="kept one", operation="shape")],
    )

    assert grounded.grounded
    assert filtered.grounded, "a filter derives no claim and has nothing to cite"


def test_an_empty_flow_is_never_reported_as_grounded():
    assert not Flow.start().grounded


def _evidence():
    from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation

    return Evidence(
        kind=EvidenceKind.MANIFEST_DECLARED,
        location=SourceLocation("file:///r/package.json", line=3),
        extractor="test", excerpt='"lodash": "^3.0.0"',
    )


# --- mutation is gated over the whole composition -----------------------


def test_a_composition_containing_a_mutating_verb_needs_confirmation(verbs: VerbRegistry):
    validation = Composition.read("target --to live", verbs=verbs).validate()

    assert validation.ok
    assert validation.needs_confirmation
    assert "target" in validation.mutating


def test_the_confirmation_is_demanded_before_the_first_stage_runs():
    reached: list[str] = []
    fresh = VerbRegistry()

    fresh.add(Verb(
        name="harmless", summary="runs first", produces=Kind.REPORT,
        examples=("harmless",),
        run=lambda flow, args, ctx: (
            reached.append("ran"),
            flow.then(Kind.REPORT, {}, stage="harmless"),
        )[1],
    ))
    fresh.add(Verb(
        name="dangerous", summary="changes the world", consumes=Kind.REPORT,
        produces=Kind.REPORT, mutates=True, examples=("harmless | dangerous",),
        run=lambda flow, args, ctx: flow.then(Kind.REPORT, {}, stage="dangerous"),
    ))

    composition = Composition.read("harmless | dangerous", verbs=fresh)
    with pytest.raises(CompositionError, match="change the environment"):
        composition.run(Context(confirmed=False))

    assert reached == [], "a stage ran before the operator had decided"
    assert composition.run(Context(confirmed=True)).ok


# --- failure ------------------------------------------------------------


def test_a_failing_stage_stops_the_composition_and_says_where():
    fresh = VerbRegistry()
    fresh.add(Verb(
        name="source", summary="fine", produces=Kind.OBSERVATIONS,
        examples=("source",),
        run=lambda flow, args, ctx: flow.then(Kind.OBSERVATIONS, (1,), stage="source"),
    ))
    fresh.add(Verb(
        name="broken", summary="raises", consumes=Kind.OBSERVATIONS,
        produces=Kind.RESOLUTION, examples=("source | broken",),
        run=lambda flow, args, ctx: (_ for _ in ()).throw(RuntimeError("nope")),
    ))

    result = Composition.read("source | broken", verbs=fresh).run()

    assert not result.ok
    assert result.partial, "the first stage's work is returned, not discarded"
    assert result.failed == "broken"
    assert result.completed == 1
    assert "nope" in result.error


def test_a_verb_needing_an_environment_says_so_rather_than_answering_emptily(
    verbs: VerbRegistry,
):
    result = Composition.read("status", verbs=verbs).run(Context(engine=None))

    assert not result.ok
    assert "needs an environment" in result.error


# --- parsing ------------------------------------------------------------


def test_a_pipeline_round_trips_through_parse_and_render():
    text = "discover . | filter --contains lodash | head --count 3"
    assert render(parse(text)) == text


def test_quoted_arguments_survive_the_pipe_split():
    stages = parse("search 'redis | cache' | impact")

    assert len(stages) == 2
    assert stages[0].operands == ("redis | cache",)


def test_an_unbalanced_quote_is_refused_rather_than_guessed():
    with pytest.raises(ParseError, match="unbalanced"):
        parse("search 'redis | impact")


def test_nothing_is_expanded_so_the_text_reviewed_is_the_text_that_runs():
    stages = parse("discover $HOME/repo")
    assert stages[0].operands == ("$HOME/repo",)


def test_a_trailing_flag_with_no_value_is_a_boolean():
    stages = parse("link --resolve_only")
    assert stages[0].arguments == {"resolve_only": True}


def test_an_inline_equals_form_is_accepted():
    stages = parse("discover --limit=50")
    assert stages[0].arguments == {"limit": "50"}


def test_short_flags_are_refused_with_the_long_form_named():
    with pytest.raises(ParseError, match="--c"):
        parse("head -c 3")


def test_an_empty_pipeline_is_refused():
    with pytest.raises(ParseError, match="empty"):
        parse("   ")


# --- end to end against real files --------------------------------------


def test_a_real_composition_answers_over_a_real_tree(repository: Path):
    result = run(f"discover {repository} | link | findings | explain")

    assert result.ok
    assert result.flow.kind is Kind.FINDINGS
    assert result.flow.facts["identities"] >= 2
    assert result.flow.grounded


def test_the_manifest_range_and_the_lockfile_pin_are_reported_as_contradicting(
    repository: Path,
):
    """The lockfile pins 4.17.21; the manifest asked for ^3.0.0."""
    result = run(f"discover {repository} | link | findings")

    assert result.ok
    details = " ".join(item.detail for item in result.flow.items)
    assert "4.17.21" in details or "3.0.0" in details


def test_the_same_composition_produces_the_same_digest_twice(repository: Path):
    """Different surfaces must not be different answers."""
    first = run(f"discover {repository} | link | count")
    second = run(f"discover {repository} | link | count")

    assert first.flow.digest == second.flow.digest


def test_a_composition_explains_itself_before_it_runs(repository: Path):
    text = f"discover {repository} | link | findings --severity high"
    rendered = Composition.read(text).explain()

    assert "OBSERVATIONS" in rendered
    assert "RESOLUTION" in rendered
    assert "produces FINDINGS" in rendered


def test_explain_reports_the_files_it_read(repository: Path):
    result = run(f"discover {repository} | link | explain")

    explanation = result.flow.facts["explanation"]
    assert "package.json" in explanation
    assert "confidence" in explanation


def test_the_digest_ignores_how_long_the_run_took(repository: Path):
    """A digest that moved with the clock would be useless for the one thing it
    exists for: proving two surfaces produced the same answer."""
    from slpie.compose.flow import VOLATILE, _stable

    rendered = _stable(
        {"duration": 0.12, "nodes": 3, "inner": {"observed_at": 99, "kept": 1}},
        comparable=True,
    )

    assert "duration" not in rendered
    assert "observed_at" not in rendered["inner"], "volatile keys hide at every level"
    assert rendered == {"nodes": 3, "inner": {"kept": 1}}
    assert "duration" in VOLATILE


def test_a_dependencys_version_is_never_recorded_against_the_depender(repository: Path):
    """A `depends_on`'s range describes the target. Reading it onto the source made
    every package absorb its dependencies' versions, and a package holding two
    versions reads as contradicted — so a healthy project raised a contradiction
    against its own root package."""
    result = run(f"discover {repository} | link")
    linked = result.flow.value

    root = next(
        entry for entry in linked.resolved if "demo" in entry.identity
    )
    assert root.versions == ("1.0.0",)
    assert root.ranges == (), "^3.0.0 belongs to lodash, not to demo"
    assert not root.contradicted


def test_the_pin_outside_its_declared_range_is_the_finding_that_gets_raised(
    repository: Path,
):
    result = run(f"discover {repository} | link | findings")

    titles = [item.title for item in result.flow.items]
    assert any("outside its declared range" in title for title in titles)
    assert all(item.evidence for item in result.flow.items), (
        "a finding with no evidence would sit at the top of a triage list unfounded"
    )


def test_the_joins_travel_in_the_pipe_rather_than_through_a_side_channel(
    repository: Path,
):
    """A stage-to-stage back door would let data move without the type check
    seeing it, which is exactly what the typed pipe is for."""
    result = run(f"discover {repository} | link")

    assert result.flow.kind is Kind.RESOLUTION
    assert result.flow.value.joins, "the linker output is on the flow"
    assert result.flow.value.contradicting_joins()
