"""The manual, the planner and the CLI — the three surfaces a person touches.

The load-bearing test is `test_every_cookbook_recipe_runs`. A documented
composition that does not run teaches the wrong thing and costs the reader their
trust in the rest of the document, so every recipe is executed here rather than
proofread.

The planner's headline property is that it **cannot emit an invalid pipeline**,
because it selects routes from the type graph rather than composing freely. That is
what makes the no-key offline path honest instead of a fiction, and it is asserted
across every question in the fixture rather than spot-checked.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from slpie.cli import FAILED, FINDINGS, OK, USAGE, Cli
from slpie.compose import Composition, Context, Kind, registry
from slpie.compose.flow import Flow
from slpie.compose.wire import WireError, decode, encode
from slpie.manual import (
    as_dict,
    compose_help,
    markdown,
    offline,
    overview,
    page_for,
    recipes,
    verb_help,
)
from slpie.planner import plan_for, read


def cli(stdin: str = "", *, isatty: bool = True) -> tuple[Cli, io.StringIO, io.StringIO]:
    out, err = io.StringIO(), io.StringIO()
    return Cli(
        stdout=out, stderr=err, stdin=io.StringIO(stdin), isatty=isatty,
    ), out, err


# --- the cookbook is executable -----------------------------------------


def test_every_cookbook_recipe_runs(repository: Path, verbs):
    """A documented composition that does not run is worse than an undocumented one."""
    for recipe in offline():
        text = recipe.pipeline.replace("discover .", f"discover {repository}")
        composition = Composition.read(text, verbs=verbs)

        validation = composition.validate()
        assert validation.ok, f"{recipe.pipeline!r}: {validation.explain()}"

        result = composition.run(Context(root=str(repository)))
        assert result.ok, f"{recipe.pipeline!r}: {result.error}"
        assert result.flow.kind is recipe.produces, (
            f"{recipe.pipeline!r} claims {recipe.produces.label} "
            f"but produced {result.flow.kind.label}"
        )


def test_every_recipe_that_claims_to_need_no_environment_really_does_not(
    repository: Path, verbs,
):
    for recipe in offline():
        text = recipe.pipeline.replace("discover .", f"discover {repository}")
        for stage in Composition.read(text, verbs=verbs):
            assert verbs.require(stage.verb).group != "environment", (
                f"{recipe.pipeline!r} is marked offline but uses {stage.verb}"
            )


def test_every_environment_recipe_is_marked_as_needing_one(verbs):
    for recipe in recipes():
        uses = any(
            verbs.require(stage.verb).group == "environment"
            for stage in Composition.read(recipe.pipeline, verbs=verbs)
        )
        assert uses == recipe.needs_environment, (
            f"{recipe.pipeline!r} is mismarked"
        )


# --- the manual is generated, and total ---------------------------------


def test_the_overview_lists_every_verb(verbs):
    text = overview(verbs=verbs)
    for verb in verbs:
        assert verb.name in text, f"{verb.name} is missing from `slpie help`"


def test_the_markdown_manual_documents_every_verb(verbs):
    text = markdown(verbs=verbs)
    for verb in verbs:
        assert f"`{verb.name}`" in text


def test_a_verb_page_states_what_pipes_into_it_and_what_it_pipes_into(verbs):
    """Generated from the type graph, which is what makes the manual teach
    composition rather than merely list commands."""
    page = page_for("link", verbs=verbs)

    assert "discover" in page.follows
    assert "constraints" in page.precedes
    assert "OBSERVATIONS -> RESOLUTION" == page.signature

    rendered = page.render()
    assert "pipe into it from" in rendered
    assert "and then pipe it into" in rendered


def test_a_source_verb_says_it_starts_a_pipeline(verbs):
    page = page_for("discover", verbs=verbs)

    assert page.follows == ()
    assert "starts a pipeline" in page.render()


def test_a_mutating_verb_is_marked_in_its_page_and_in_the_overview(verbs):
    assert "CHANGES THE ENVIRONMENT" in page_for("target", verbs=verbs).render()
    assert "changes the environment" in overview(verbs=verbs)


def test_a_parameters_help_text_reaches_the_page(verbs):
    rendered = page_for("findings", verbs=verbs).render()

    assert "--severity" in rendered
    assert "critical" in rendered, "the choices are listed, so nobody guesses"


def test_the_compose_help_teaches_before_it_lists(verbs):
    text = compose_help(verbs=verbs)

    assert "provenance, not bytes" in text
    assert "typed" in text
    for recipe in recipes()[:4]:
        assert recipe.pipeline in text


def test_the_machine_readable_manual_carries_the_same_verbs(verbs):
    body = as_dict(verbs=verbs)

    assert {entry["name"] for entry in body["verbs"]} == set(verbs.names)
    assert len(body["recipes"]) == len(recipes())
    assert body["primer"]


def test_an_unknown_help_topic_suggests_rather_than_shrugs(verbs):
    with pytest.raises(Exception) as caught:
        verb_help("linkk", verbs=verbs)
    assert "link" in str(caught.value)


# --- the planner ---------------------------------------------------------

QUESTIONS = (
    "what breaks if lodash 5 lands?",
    "what is wrong here before release?",
    "do my dependency versions actually resolve?",
    "what packages are in ./services?",
    "who depends on redis?",
    "where does what I declared drift from reality?",
    "what can you not see?",
    "why do you believe lodash is vulnerable?",
    "what is the architecture of this system?",
)


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_plan_the_planner_writes_type_checks(question: str, verbs):
    """It cannot emit an invalid pipeline, because it only selects routes the
    type graph already contains. That is what makes the offline path honest."""
    plan = plan_for(question, verbs=verbs)

    assert plan.understood, f"{question!r} was not understood"
    composition = plan.composition(verbs=verbs)
    assert composition is not None
    assert composition.validate().ok, (
        f"{question!r} planned {plan.pipeline!r}: "
        f"{composition.validate().explain()}"
    )


def test_the_planner_never_puts_a_mutating_verb_in_a_plan_it_wrote(verbs):
    for question in QUESTIONS:
        plan = plan_for(question, verbs=verbs)
        for step in plan.steps:
            assert not verbs.require(step.verb).mutates, (
                f"{question!r} planned the mutating verb {step.verb}"
            )


def test_a_question_naming_a_package_produces_a_plan_that_uses_it(verbs):
    plan = plan_for("what breaks if lodash 5 lands?", verbs=verbs)

    assert plan.intent.subject == "lodash"
    assert "lodash" in plan.pipeline, (
        "a plan that discards the word the question was about answers a "
        "different question"
    )


def test_a_question_that_can_be_answered_offline_gets_an_offline_plan(verbs):
    """Shortest-first chose the one-stage `reconcile`, which needs a manifest, a
    ledger and a graph — so the shortest plan was the one most readers could not
    run."""
    plan = plan_for("what is wrong here before release?", verbs=verbs)

    assert not plan.needs_environment
    assert plan.steps[0].verb == "discover"
    assert "high" in plan.pipeline, "the severity in the question is carried through"


def test_the_planner_never_injects_a_filter_it_was_not_asked_for(verbs):
    """A loosely-extracted noun became `filter --contains actually`."""
    plan = plan_for("do my dependency versions actually resolve?", verbs=verbs)

    assert "filter" not in plan.pipeline
    assert plan.pipeline == "discover | link | constraints"


def test_an_unrecognised_question_asks_back_with_options_that_work(verbs):
    plan = plan_for("how is the weather today", verbs=verbs)

    assert not plan.understood
    assert "guessing" in plan.clarification
    assert plan.options
    for option in plan.options:
        assert Composition.read(option, verbs=verbs).validate().ok, (
            "a clarification must not offer an option that does not run"
        )


def test_the_plan_states_why_each_stage_is_there(verbs):
    plan = plan_for("what is wrong here before release?", verbs=verbs)
    rendered = plan.render()

    for step in plan.steps:
        assert step.because
        assert step.because in rendered


def test_intent_reads_a_severity_stated_without_the_word(verbs):
    assert read("what must I fix before release?").severity == "high"
    assert read("show me critical problems").severity == "critical"


def test_an_alternative_route_is_never_the_same_route_with_a_filter_wedged_in(verbs):
    plan = plan_for("what packages are in ./services?", verbs=verbs)

    for alternative in plan.alternatives:
        stages = [item.strip() for item in alternative.split("|")]
        interior = stages[1:-1]
        assert not any(
            verbs.require(name).group == "shaping" for name in interior
        ), f"{alternative!r} is the chosen route with noise added"


# --- the CLI -------------------------------------------------------------


def test_bare_slpie_prints_the_generated_overview():
    runner, out, _ = cli()
    assert runner.main([]) == OK
    assert "usage:" in out.getvalue()
    assert "discover" in out.getvalue()


def test_help_for_one_verb_prints_its_generated_page():
    runner, out, _ = cli()
    assert runner.main(["help", "link"]) == OK
    assert "OBSERVATIONS -> RESOLUTION" in out.getvalue()


def test_help_for_an_unknown_verb_exits_as_a_usage_error():
    runner, _, err = cli()
    assert runner.main(["help", "linkk"]) == USAGE
    assert "link" in err.getvalue()


def test_help_compose_prints_the_cookbook():
    runner, out, _ = cli()
    assert runner.main(["help", "compose"]) == OK
    assert "COMPOSING" in out.getvalue()
    assert "discover . | link | findings" in out.getvalue()


def test_the_verb_registry_is_available_as_json_for_the_clients():
    runner, out, _ = cli()
    assert runner.main(["verbs"]) == OK
    body = json.loads(out.getvalue())
    assert body["verbs"] and body["graph"] and body["sources"]


def test_an_invalid_composition_is_a_usage_error_and_runs_nothing():
    runner, out, err = cli()
    assert runner.main(["findings | attach"]) == USAGE
    assert "FINDINGS" in err.getvalue()
    assert out.getvalue().strip() == ""


def test_dry_run_shows_the_composition_without_executing_it(repository: Path):
    runner, out, _ = cli()
    assert runner.main([f"discover {repository} | link", "--dry-run"]) == OK

    text = out.getvalue()
    assert "OBSERVATIONS \u2192 RESOLUTION" in text
    assert "produces RESOLUTION" in text


def test_a_real_pipeline_runs_from_the_cli(repository: Path):
    runner, out, _ = cli()
    code = runner.main([f"discover {repository} | link | findings"])

    assert code == OK
    assert "FINDINGS" in out.getvalue()


def test_fail_on_turns_findings_into_a_nonzero_exit_for_ci(repository: Path):
    runner, _, err = cli()
    code = runner.main([
        f"discover {repository} | link | findings", "--fail-on", "high", "--quiet",
    ])

    assert code == FINDINGS
    assert "at or above high" in err.getvalue()


def test_fail_on_stays_zero_when_nothing_breaches(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    runner, _, _ = cli()
    code = runner.main([
        f"discover {tmp_path} | link | findings", "--fail-on", "critical", "--quiet",
    ])

    assert code == OK


def test_an_unknown_severity_for_fail_on_is_a_usage_error(repository: Path):
    runner, _, err = cli()
    code = runner.main([
        f"discover {repository} | link | findings", "--fail-on", "urgent", "--quiet",
    ])

    assert code == USAGE
    assert "not a severity" in err.getvalue()


def test_an_environment_verb_without_an_environment_fails_rather_than_answering_emptily():
    runner, _, err = cli()
    assert runner.main(["status"]) == FAILED
    assert "needs an environment" in err.getvalue()


def test_plan_from_the_cli_prints_the_composition():
    runner, out, _ = cli()
    assert runner.main(["plan", "what is wrong before release?"]) == OK

    text = out.getvalue()
    assert "discover | findings --severity high" in text
    assert "Run it with --run" in text


def test_plan_with_no_question_is_a_usage_error():
    runner, _, err = cli()
    assert runner.main(["plan"]) == USAGE
    assert "needs a question" in err.getvalue()


def test_json_output_carries_the_provenance_a_client_needs(repository: Path):
    runner, out, _ = cli()
    assert runner.main([f"discover {repository} | link", "--json"]) == OK

    body = json.loads(out.getvalue())
    assert body["ok"] is True
    assert body["flow"]["digest"]
    assert body["flow"]["reasoning"]["steps"]


# --- crossing a process boundary ----------------------------------------


def test_a_flow_of_observations_round_trips_through_a_pipe(repository: Path):
    runner, out, _ = cli()
    assert runner.main([f"discover {repository}", "--wire"]) == OK

    revived = decode(out.getvalue())
    assert revived.kind is Kind.OBSERVATIONS
    assert revived.size > 0
    assert all(item.evidence is not None for item in revived.items)


def test_the_gaps_survive_the_process_boundary():
    """Provenance that stopped at the `|` would be provenance whose honesty
    depended on how the tool was invoked."""
    from slpie.domain.finding import Gap, GapKind

    gap = Gap(
        kind=GapKind.CAPABILITY_REFUSED, subject="vendored/",
        detail="static analysis was refused", confidence_impact=0.3,
    )
    original = Flow.start().then(
        Kind.OBSERVATIONS, (), stage="source", gaps=[gap],
    )

    revived = decode(encode(original))
    assert [item.detail for item in revived.gaps] == ["static analysis was refused"]
    assert revived.stages == ("source",)


def test_a_downstream_slpie_accepts_a_piped_flow(repository: Path):
    producer, produced, _ = cli()
    assert producer.main([f"discover {repository}", "--wire"]) == OK

    consumer, out, err = cli(stdin=produced.getvalue(), isatty=False)
    code = consumer.main(["link"])

    assert code == OK, err.getvalue()
    assert "RESOLUTION" in out.getvalue()


def test_a_kind_that_cannot_cross_is_refused_at_emission_not_downstream(
    repository: Path,
):
    runner, _, err = cli()
    code = runner.main([f"discover {repository} | link", "--wire"])

    assert code == USAGE
    assert "cannot be written to a pipe" in err.getvalue()
    assert "--json" in err.getvalue(), "it names the thing to use instead"


def test_arbitrary_text_on_stdin_is_refused_with_the_reason(repository: Path):
    runner, _, err = cli(stdin="just some text\n", isatty=False)
    code = runner.main(["link"])

    assert code == USAGE
    assert "envelope" in err.getvalue() or "not a slpie flow" in err.getvalue()


def test_a_source_verb_never_blocks_reading_stdin_that_will_not_arrive(
    repository: Path,
):
    """Gated on the verb rather than on isatty: a source consumes nothing, so
    reading stdin for it is a deadlock waiting for a writer that does not exist."""
    class Blocking:
        def read(self, *_args):
            raise AssertionError("a source verb must not read stdin")

        def isatty(self):
            return False

    runner = Cli(
        stdout=io.StringIO(), stderr=io.StringIO(), stdin=Blocking(), isatty=False,
    )
    assert runner.main([f"discover {repository}", "--quiet"]) == OK
