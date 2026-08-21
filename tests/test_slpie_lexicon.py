"""§31 — terminology adapted per context, and the words that may never move.

The lexicon exists so one console reads as a platform-engineering tool to one
tenant and a compliance tool to another without forking a screen. The tests
below are mostly about the *limit* on that, because the limit is where the
design could quietly fail: a tenant who can rename `refused` has a control
nobody can see, including us.
"""

from __future__ import annotations

import json

import pytest

from slpie.context.lexicon import Lexicon, LexiconError, Term, default
from slpie.context.profile import (
    ContextProfile,
    load_profile_file,
    load_profiles,
    resolve,
)
from slpie.ui.api import Api, Request


@pytest.fixture(scope="module")
def base() -> Lexicon:
    return default()


@pytest.fixture()
def profiled(tmp_path):
    """A tree with one good profile and one that will not parse."""
    directory = tmp_path / ".slpie" / "lexicon"
    directory.mkdir(parents=True)
    (directory / "platform-engineering.yaml").write_text(
        "name: platform-engineering\n"
        "terms:\n"
        "  node: service\n"
        "  finding: risk\n"
        "  station:\n"
        "    word: fleet\n"
        "    gloss: The estate this console is attached to.\n",
        encoding="utf-8",
    )
    return tmp_path


# -- the derived default -------------------------------------------------


def test_the_default_is_derived_from_the_code_not_authored(base: Lexicon) -> None:
    """Every word traces to a module, so the platform cannot ship a word it
    does not use — the same rule the manual and the contract already follow."""
    assert len(base) > 40
    for term in base:
        assert term.source, f"{term.key} came from nowhere"
    assert base.get("node").source == "slpie/domain/node.py"
    assert base.get("node").gloss                       # from its own docstring


def test_an_already_plural_package_name_is_not_pluralised_twice(
    base: Lexicon,
) -> None:
    """`artifacts` is the plural. It rendered as "artifactses"."""
    assert base.get("artifacts").plural == "artifacts"
    assert base.get("node").plural == "nodes"


def test_a_missing_term_renders_as_its_key_rather_than_raising(base: Lexicon) -> None:
    """A render is not the place to fail.

    A missing term should show a slightly ugly label, never take a screen down.
    The profile is where a bad key fails loudly, because that is authored.
    """
    assert base.word("no-such-term") == "no-such-term"
    assert base.word("node", title=True) == "Node"
    assert base.word("node", plural=True, title=True) == "Nodes"


# -- what a profile may do -----------------------------------------------


def test_a_profile_renames_the_product(base: Lexicon, profiled) -> None:
    words = resolve({"profile": "platform-engineering"}, root=profiled, base=base)
    assert words.word("node") == "service"
    assert words.word("node", plural=True) == "services"
    assert words.word("finding", plural=True) == "risks"
    assert words.word("station") == "fleet"
    # Everything it did not name keeps the platform's word.
    assert words.word("edge") == base.word("edge")


def test_a_rename_is_attributed_to_the_profile_that_made_it(
    base: Lexicon, profiled,
) -> None:
    """Where a word came from is part of the answer, as it is everywhere else."""
    words = resolve({"profile": "platform-engineering"}, root=profiled, base=base)
    assert words.get("node").source == "profile:platform-engineering"
    assert words.get("edge").source == "slpie/domain/edge.py"


def test_the_digest_moves_when_the_words_do(base: Lexicon, profiled) -> None:
    words = resolve({"profile": "platform-engineering"}, root=profiled, base=base)
    assert words.digest != base.digest
    assert words.digest == resolve(
        {"profile": "platform-engineering"}, root=profiled, base=base,
    ).digest


# -- what a profile may never do -----------------------------------------


@pytest.mark.parametrize("key", [
    "severity.critical", "gap.capability_refused",
    "verdict.violated", "target.live", "refusal.refused",
])
def test_a_profile_cannot_rename_a_control(base: Lexicon, key: str) -> None:
    """The load-bearing limit.

    A tenant renaming `refused` to `pending` is how a control becomes
    invisible — to the operator reading it, and to us reading their ledger.
    """
    with pytest.raises(LexiconError) as raised:
        base.overlay({key: {"word": "something-friendlier"}}, name="sneaky")
    assert key in str(raised.value)


def test_the_protected_set_is_derived_from_the_enums(base: Lexicon) -> None:
    """So a severity added next year is protected the day it is added.

    A hand-written list of protected words is one rename away from a hole, and
    the hole is invisible in the list.
    """
    from slpie.audit.judge import Verdict
    from slpie.binding.target import Target
    from slpie.domain.finding import GapKind, Severity

    protected = {term.key for term in base.protected}
    for member in Severity:
        assert f"severity.{member.value}" in protected
    for member in GapKind:
        assert f"gap.{member.value}" in protected
    for member in Verdict:
        assert f"verdict.{member.value}" in protected
    for member in Target:
        assert f"target.{member.value}" in protected


def test_a_profile_naming_a_term_nobody_defines_is_refused(base: Lexicon) -> None:
    """A silent no-op here is a rename somebody believes happened."""
    with pytest.raises(LexiconError) as raised:
        base.overlay({"nodes": {"word": "service"}}, name="typo")
    assert "nodes" in str(raised.value)
    assert "node" in str(raised.value)          # it suggests the real key


def test_an_empty_word_is_refused(base: Lexicon) -> None:
    with pytest.raises(LexiconError):
        base.overlay({"node": {"word": "   "}}, name="blank")


# -- loading -------------------------------------------------------------


def test_a_malformed_profile_costs_that_profile_and_not_the_console(
    tmp_path,
) -> None:
    """One fat-fingered file must not blank the interface.

    The same property `slpie/governance/policies.py` has, and the reason to
    reuse its shape rather than invent one.
    """
    directory = tmp_path / ".slpie" / "lexicon"
    directory.mkdir(parents=True)
    (directory / "good.yaml").write_text(
        "name: good\nterms:\n  node: service\n", encoding="utf-8")
    (directory / "broken.yaml").write_text("terms: [not, a, mapping]\n",
                                           encoding="utf-8")

    loaded = load_profiles(tmp_path)
    assert [item.name for item in loaded.profiles] == ["good"]
    assert len(loaded.errors) == 1
    assert "broken" in loaded.errors[0]


def test_the_short_form_and_the_long_form_mean_the_same_thing(tmp_path) -> None:
    """`node: service` and `node: {word: service}` — most renames are one word."""
    directory = tmp_path / ".slpie" / "lexicon"
    directory.mkdir(parents=True)
    (directory / "short.yaml").write_text(
        "terms:\n  node: service\n", encoding="utf-8")
    (directory / "long.json").write_text(
        json.dumps({"terms": {"node": {"word": "service"}}}), encoding="utf-8")

    short = load_profile_file(directory / "short.yaml")
    long = load_profile_file(directory / "long.json")
    assert short.terms["node"]["word"] == long.terms["node"]["word"] == "service"


def test_an_unknown_profile_yields_the_platforms_own_words(base: Lexicon) -> None:
    """A reader with no profile sees a real console, not an error about words."""
    assert resolve({"profile": "nobody"}, root=".", base=base).digest == base.digest
    assert resolve({}, root=".", base=base).digest == base.digest


# -- the route -----------------------------------------------------------


def test_the_route_answers_with_the_platforms_words_by_default() -> None:
    api = Api(engine=None)
    response = api.handle(Request("GET", "/api/lexicon", {}, {}))
    assert response.status == 200
    assert response.body["name"] == "default"
    assert response.body["terms"]["node"]["word"] == "node"
    assert response.body["terms"]["severity.critical"]["protected"] is True


def test_the_route_reads_the_context_the_gateway_wrote() -> None:
    """No second identity path.

    `Request.context` is populated before any route runs, so asking for a
    lexicon is asking about a caller the platform has already identified.
    """
    api = Api(engine=None)
    request = Request("GET", "/api/lexicon", {}, {}, context={"profile": "nobody"})
    response = api.handle(request)
    assert response.status == 200
    assert response.body["requested"] == "nobody"


def test_a_broken_profile_does_not_take_the_route_down(monkeypatch) -> None:
    """It falls back to the platform's words and reports what it ignored."""
    from slpie.context import profile as profile_module

    def explode(*_args, **_kwargs):
        raise LexiconError("severity.high carries a decision, not a name")

    monkeypatch.setattr(profile_module, "resolve", explode)
    api = Api(engine=None)
    response = api.handle(
        Request("GET", "/api/lexicon", {"profile": "hostile"}, {}),
    )
    assert response.status == 200
    assert response.body["name"] == "default"
    assert "carries a decision" in response.body["error"]


# -- the verb ------------------------------------------------------------


def test_the_lexicon_composes_like_everything_else() -> None:
    from slpie.compose.pipeline import Composition

    result = Composition.read("lexicon | count").run()
    assert result.ok, result.explanation
    assert result.flow.facts["profile"] == "default"
    assert result.flow.facts["protected"]


def test_a_term_needs_both_a_key_and_a_word() -> None:
    with pytest.raises(LexiconError):
        Term(key="", word="x")
    with pytest.raises(LexiconError):
        Term(key="x", word="")
