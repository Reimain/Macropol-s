"""Suggestion intelligence — the learned half, held to the deterministic rules.

Four properties carry the design, and each is asserted rather than hoped for:

1. **Every suggestion runs.** Candidates come from the type graph, so a suggestion
   can never be something the platform cannot do. The short key is a promise.
2. **A stuck reviewer is never given nothing.** An error, an empty result and a gap
   each yield at least one path — those are the moments a conventional help system
   says nothing and a reviewer quietly stops reviewing.
3. **Learning changes order only.** With history injected the candidate *set* is
   unchanged; only the ranking moves. The worst a mis-learned weight can do is put
   a useful path second.
4. **Attention infers, but never invents.** Dwell without action is read as
   confusion; scattered signals decline to guess; and none of it can make the
   platform propose something it cannot do.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from slpie.cli import Cli
from slpie.compose import Composition, Context, registry
from slpie.suggest import (
    FOCUS_FLOOR,
    RETIRE_AFTER,
    STUCK_AT,
    Attention,
    History,
    Routines,
    RoutineError,
    SignalKind,
    SuggestionEngine,
    TouchKind,
    Touch,
    from_events,
    mint_key,
    on_empty,
    on_error,
    on_finding,
    on_gap,
    on_node,
    on_term,
)


@pytest.fixture()
def verbs():
    return registry()


@pytest.fixture()
def engine(verbs):
    return SuggestionEngine(verbs=verbs, root=".")


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
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
    return tmp_path


TOUCHES = (
    on_error("status needs an environment; declare one with a manifest first"),
    on_error("stage 2 `attach` consumes NOTHING, but `findings` produces FINDINGS"),
    on_error("there is no verb 'discove'; did you mean discover?"),
    on_empty("discover . | link | findings --severity critical"),
    on_empty("discover . | link | filter --contains nothing"),
    on_gap("vendored/"),
    on_finding("pkg:npm/lodash"),
    on_node("pkg:pypi/flask"),
    on_term("lockfile"),
    on_term("provenance"),
    on_term("wobblegonk"),
    Touch(kind=TouchKind.KIND, subject="resolution"),
    Touch(kind=TouchKind.VERB, subject="link"),
)


# --- every suggestion is runnable ---------------------------------------


@pytest.mark.parametrize("touch", TOUCHES, ids=lambda t: f"{t.kind.value}:{t.subject[:24]}")
def test_every_suggestion_offered_would_actually_run(touch, engine, verbs):
    """The short key is a promise. Offering a path that fails when typed costs more
    trust than offering nothing, because now every suggestion must be checked."""
    for suggestion in engine.suggest(touch):
        text = suggestion.pipeline
        assert text, "an empty pipeline is not a suggestion"
        if text.startswith("help"):
            continue
        validation = Composition.read(text, verbs=verbs).validate()
        assert validation.ok, (
            f"{touch} offered {text!r}, which does not run: {validation.explain()}"
        )


@pytest.mark.parametrize("touch", TOUCHES, ids=lambda t: t.kind.value)
def test_every_suggestion_says_what_it_will_reveal(touch, engine):
    """This is what stops it being a dictionary: not what a word means, but what
    you will know afterwards that you do not know now."""
    for suggestion in engine.suggest(touch):
        assert suggestion.reveals, f"{suggestion.pipeline} promises nothing"
        assert suggestion.because, f"{suggestion.pipeline} gives no reason"
        assert suggestion.key


def test_a_stuck_reviewer_is_never_given_an_empty_list(engine):
    """The moments a conventional help system says nothing are exactly the moments
    somebody gives up."""
    for touch in TOUCHES:
        suggestions = engine.suggest(touch)
        if touch.stuck:
            assert not suggestions.empty, f"{touch} left a stuck reviewer with nothing"


def test_a_blocked_reviewer_gets_fewer_options_than_a_browsing_one(engine):
    """A menu is not help when you cannot proceed; one obvious next step is."""
    from slpie.suggest import STUCK_WIDTH, WIDTH

    blocked = engine.suggest(on_error("something failed"))
    browsing = engine.suggest(on_finding("pkg:npm/lodash"))

    assert len(blocked) <= STUCK_WIDTH
    assert len(browsing) <= WIDTH


def test_an_unknown_term_still_offers_a_path_rather_than_a_shrug(engine):
    suggestions = engine.suggest(on_term("wobblegonk"))

    assert not suggestions.empty
    assert any("help" in item.pipeline for item in suggestions)


def test_a_term_that_is_a_verb_offers_both_its_page_and_running_it(engine):
    """Reading about a verb teaches less than running it once."""
    suggestions = engine.suggest(on_term("link"))
    pipelines = [item.pipeline for item in suggestions]

    assert any(text.startswith("help link") for text in pipelines)
    assert any("discover" in text for text in pipelines)


def test_asking_what_a_kind_is_answers_by_producing_one(engine):
    """"What is a RESOLUTION?" is best answered with a real one from your files."""
    suggestions = engine.suggest(Touch(kind=TouchKind.KIND, subject="resolution"))

    assert not suggestions.empty
    for item in suggestions:
        assert "link" in item.pipeline, "the shortest route to a RESOLUTION"


def test_an_empty_result_blames_the_filter_before_blaming_the_repository(engine):
    suggestions = engine.suggest(
        on_empty("discover . | link | findings --severity critical"),
    )

    assert not suggestions.empty
    assert "severity" not in suggestions.items[0].pipeline, (
        "the first move is to drop the filter that removed everything"
    )


# --- learning changes order, never content ------------------------------


def test_acceptance_reorders_without_changing_the_candidate_set(verbs):
    history = History()
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)
    touch = on_finding("pkg:npm/lodash")

    before = engine.suggest(touch)
    assert len(before) > 1, "this test needs something to reorder"

    second = before.items[1]
    for _ in range(3):
        history.accept(touch, second)
    after = engine.suggest(touch)

    assert {item.pipeline for item in before} == {item.pipeline for item in after}, (
        "learning must never add or remove a candidate, only reorder"
    )
    assert after.items[0].pipeline == second.pipeline


def test_dismissal_is_weighted_as_heavily_as_acceptance(verbs):
    """A system that only learns from acceptance keeps proposing what you have
    refused four times."""
    history = History()
    touch = on_finding("pkg:npm/lodash")
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)

    first = engine.suggest(touch).items[0]
    history.dismiss(touch, first)

    assert history.weight(touch, first) < 1.0


def test_three_dismissals_retire_a_suggestion_and_it_says_so(verbs):
    history = History()
    touch = on_finding("pkg:npm/lodash")
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)

    unwanted = engine.suggest(touch).items[0]
    for _ in range(RETIRE_AFTER):
        history.dismiss(touch, unwanted)

    after = engine.suggest(touch)
    assert unwanted.pipeline not in {item.pipeline for item in after}
    assert unwanted.pipeline in after.retired
    assert "no longer offered" in history.explain(touch, unwanted)


def test_retirement_is_scoped_to_the_situation_not_global(verbs):
    """A path dismissed while reviewing findings may be right for a gap."""
    history = History()
    findings = on_finding("pkg:npm/lodash")
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)

    unwanted = engine.suggest(findings).items[0]
    for _ in range(RETIRE_AFTER):
        history.dismiss(findings, unwanted)

    assert not history.retired(on_gap("vendored/"), unwanted)


def test_the_learned_weight_is_bounded_so_history_cannot_dominate(verbs):
    from slpie.suggest import MAX_WEIGHT, MIN_WEIGHT

    history = History()
    touch = on_finding("x")
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)
    item = engine.suggest(touch).items[0]

    for _ in range(50):
        history.accept(touch, item)
    assert history.weight(touch, item) <= MAX_WEIGHT

    other = History()
    for _ in range(50):
        other.dismiss(touch, item)
    assert other.weight(touch, item) >= MIN_WEIGHT


def test_the_ranking_can_be_interrogated(verbs):
    """A suggestion the reviewer cannot interrogate is one they stop trusting the
    first time it is wrong, and it will be wrong sometimes."""
    history = History()
    touch = on_finding("pkg:npm/lodash")
    engine = SuggestionEngine(verbs=verbs, root=".", history=history)
    item = engine.suggest(touch).items[0]

    assert "nothing has been accepted" in history.explain(touch, item)
    history.accept(touch, item)
    explained = history.explain(touch, item)
    assert "accepted 1" in explained
    assert "gain" in explained


def test_the_history_survives_a_restart(tmp_path: Path, verbs):
    path = tmp_path / "accepted.jsonl"
    touch = on_finding("pkg:npm/lodash")
    item = SuggestionEngine(verbs=verbs, root=".").suggest(touch).items[0]

    History(path).accept(touch, item)
    revived = History(path)

    assert len(revived) == 1
    assert revived.weight(touch, item) > 1.0


def test_a_corrupted_history_line_loses_that_row_and_not_the_feature(tmp_path: Path):
    path = tmp_path / "accepted.jsonl"
    path.write_text('{"pipeline": "a", "accepted": true}\nnot json\n', encoding="utf-8")

    assert len(History(path)) == 1, "one bad row must not disable guidance entirely"


# --- attention ----------------------------------------------------------


def test_dwelling_without_acting_is_read_as_confusion():
    """The headline case, and the one a click-only system cannot see."""
    attention = Attention()
    attention.hovered("pkg:npm/lodash", 4.2, touch_kind="finding")

    focus = attention.focus()
    assert focus.found, "a four second hover with no click must register"
    assert not focus.acted
    assert "without acting" in focus.explain()


def test_clicking_promptly_is_not_confusion():
    """Clicking is what a person does when things are going well."""
    attention = Attention()
    attention.hovered("x", 1.0)
    attention.clicked("x")

    assert not attention.focus().found


def test_clicking_the_same_thing_twice_is_confusion():
    """It did not answer the first time."""
    attention = Attention()
    attention.clicked("y")
    attention.clicked("y")

    assert attention.focus().found


def test_selecting_text_is_the_strongest_screen_signal():
    """People select a word to paste it into a search engine, which is a direct
    admission that the interface did not explain it."""
    attention = Attention()
    attention.selected("lockfile")

    focus = attention.focus()
    assert focus.found
    assert focus.stuck
    assert focus.kind is TouchKind.TERM


def test_split_attention_declines_to_name_a_subject():
    """Naming one of two equally-attended things is a coin toss presented as an
    inference."""
    attention = Attention()
    attention.hovered("a", 3.0)
    attention.hovered("b", 3.0)

    focus = attention.focus()
    assert not focus.found
    assert attention.touch() is None
    assert "guessing would be worse" in focus.explain()


def test_a_glancing_cursor_registers_nothing():
    """Moving the mouse across a list must not register interest in everything it
    crossed."""
    attention = Attention()
    for name in ("a", "b", "c", "d"):
        attention.hovered(name, 0.3)

    assert not attention.focus().found


def test_a_long_idle_does_not_become_the_strongest_signal():
    """Somebody who left the tab open is at lunch, not stuck."""
    brief = Attention()
    brief.hovered("x", 5.0)
    forever = Attention()
    forever.hovered("x", 600.0)

    assert forever.focus().score == pytest.approx(brief.focus().score, abs=0.2)


def test_a_stuck_prompt_looks_different_from_a_stuck_screen():
    attention = Attention()
    attention.failed("slpie status")
    attention.retyped("slpie status")

    focus = attention.focus()
    assert focus.found and focus.stuck
    assert focus.kind is TouchKind.ERROR


def test_attention_produces_a_touch_the_engine_can_act_on(verbs):
    attention = Attention()
    attention.selected("lockfile")

    touch = attention.touch()
    assert touch is not None

    suggestions = SuggestionEngine(verbs=verbs, root=".").suggest(touch)
    assert not suggestions.empty


def test_no_trail_is_ever_retained():
    """A reviewer's cursor is not evidence about the architecture, and keeping it
    would be collecting something we have no use for."""
    attention = Attention()
    attention.hovered("pkg:npm/lodash", 4.2)
    attention.clicked("pkg:npm/lodash")

    body = attention.focus().to_dict()

    assert isinstance(body["signals"], list)
    assert all(isinstance(item, str) for item in body["signals"]), (
        "signals must be summarised by kind, not replayed as events"
    )
    assert "at" not in body
    assert set(body["signals"]) == {"dwell", "click"}


def test_a_client_sends_aggregates_and_an_unknown_signal_is_dropped():
    """A newer client sending a signal this build does not know must not break
    guidance entirely."""
    attention = from_events([
        {"kind": "dwell", "subject": "x", "seconds": 4.0},
        {"kind": "telepathy", "subject": "x"},
        {"kind": "select", "subject": "lockfile", "touch_kind": "term"},
    ])

    assert len(attention) == 2
    assert attention.focus().found


def test_clearing_forgets_everything():
    attention = Attention()
    attention.selected("lockfile")
    attention.clear()

    assert not attention.focus().found
    assert len(attention) == 0


# --- routines -----------------------------------------------------------


def test_a_routine_is_claimed_validated_and_keyed(tmp_path: Path, verbs):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)
    claimed = routines.claim("release check", "discover . | link | findings")

    assert claimed.key == "rc"
    assert routines.resolve("rc") == "discover . | link | findings"


def test_a_routine_that_would_not_run_cannot_be_claimed(tmp_path: Path, verbs):
    """A short key is a promise, and a key that sometimes fails costs more than no
    key, because now every key has to be checked before it is trusted."""
    routines = Routines(tmp_path / "routines.json", verbs=verbs)

    with pytest.raises(RoutineError, match="would not run"):
        routines.claim("broken", "findings | attach")


def test_a_key_means_exactly_one_thing(tmp_path: Path, verbs):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)
    routines.claim("release check", "discover . | link | findings")

    with pytest.raises(RoutineError, match="already resolves"):
        routines.claim("something else", "discover . | link", key="rc")


def test_a_key_can_never_shadow_a_verb(tmp_path: Path, verbs):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)

    with pytest.raises(RoutineError, match="already a verb"):
        routines.claim("shadow", "discover .", key="link")


def test_a_minted_key_is_mnemonic_and_stable(tmp_path: Path, verbs):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)

    assert routines.mint("release check", "discover . | link") == "rc"
    assert routines.mint("release check", "discover . | link") == "rc", (
        "muscle memory is the whole value; a key that moved would be worse than none"
    )


def test_the_session_can_be_claimed_as_the_most_refined_thing_reached(
    tmp_path: Path, verbs,
):
    """Accepted paths are alternatives explored, not stages of one thing — joining
    them would produce a composition nobody ran."""
    routines = Routines(tmp_path / "routines.json", verbs=verbs)
    claimed = routines.claim_session("session", [
        "discover .",
        "discover . | link",
        "discover . | link | findings",
    ])

    assert claimed.pipeline == "discover . | link | findings"


def test_claiming_a_session_with_nothing_runnable_is_refused(tmp_path: Path, verbs):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)

    with pytest.raises(RoutineError, match="no accepted path"):
        routines.claim_session("nothing", ["findings | attach"])


def test_routines_survive_a_restart(tmp_path: Path, verbs):
    path = tmp_path / "routines.json"
    Routines(path, verbs=verbs).claim("release check", "discover . | link")

    assert Routines(path, verbs=verbs).resolve("rc") == "discover . | link"


def test_use_is_counted_so_the_list_shows_what_earned_its_key(
    tmp_path: Path, verbs,
):
    routines = Routines(tmp_path / "routines.json", verbs=verbs)
    routines.claim("release check", "discover . | link")
    routines.used("rc")
    routines.used("rc")

    assert routines.get("rc").uses == 2


def test_mint_key_prefers_initials_over_numbering():
    assert mint_key("discover . | link | findings") == "dlf"
    assert mint_key("discover . | link", taken=("dl",)) != "dl2", (
        "lengthening reads better than numbering, and stays mnemonic"
    )


# --- as verbs, end to end -----------------------------------------------


def test_suggest_never_changes_what_a_pipeline_produces(repository: Path, verbs):
    """Guidance that altered the answer would be guidance nobody could safely
    append to a working command."""
    plain = Composition.read(
        f"discover {repository} | link | findings", verbs=verbs,
    ).run(Context(root=str(repository)))
    guided = Composition.read(
        f"discover {repository} | link | findings | suggest", verbs=verbs,
    ).run(Context(root=str(repository)))

    assert plain.flow.kind is guided.flow.kind
    assert plain.flow.size == guided.flow.size
    assert guided.flow.facts["suggestion_keys"]


def test_the_cli_prints_guidance_after_the_answer_not_instead_of_it(
    repository: Path,
):
    out, err = io.StringIO(), io.StringIO()
    code = Cli(stdout=out, stderr=err, stdin=io.StringIO(), isatty=True).main([
        f"discover {repository} | link | findings | suggest",
        "--root", str(repository),
    ])
    text = out.getvalue()

    assert code == 0, err.getvalue()
    assert "FINDINGS" in text
    assert "suggestion(s)" in text
    assert text.index("FINDINGS") < text.index("suggestion(s)")


def test_a_claimed_key_runs_its_composition_from_the_cli(
    repository: Path, monkeypatch,
):
    monkeypatch.chdir(repository)

    def run(argv):
        out, err = io.StringIO(), io.StringIO()
        code = Cli(stdout=out, stderr=err, stdin=io.StringIO(), isatty=True).main(argv)
        return code, out.getvalue(), err.getvalue()

    code, _text, err = run(["routine", "claim", "release-check",
                            "discover . | link | findings"])
    assert code == 0, err

    code, text, err = run(["rc"])
    assert code == 0, err
    assert "FINDINGS" in text


def test_the_guidance_verbs_are_documented_like_every_other_verb(verbs):
    from slpie.manual import page_for

    for name in ("suggest", "accept", "dismiss", "routine"):
        page = page_for(name, verbs=verbs)
        assert page.verb.summary
        assert page.verb.examples
        assert page.render()
