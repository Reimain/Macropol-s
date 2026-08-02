"""Dispatch — the kernel does not reimplement the device.

Three properties matter more than the rest:

1. **No shell, ever.** Asserted by walking the package with `ast`, because a code
   review will eventually miss one and the consequence is arbitrary execution.
2. **A missing binary is a gap, not a crash.** The fallback runs and is
   *announced* — an approximation that looks identical to the real answer is worse
   than a slower correct one.
3. **Dispatch does not silently break reproducibility.** A flow touched by an
   environment-dependent tool says so, because "same composition, same digest" is
   a promise and system tools vary by version and locale.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from slpie.compose import Composition, Context, Kind, registry
from slpie.dispatch import (
    Determinism,
    DispatchError,
    LocalDispatcher,
    Outcome,
    Tool,
    ToolRegistry,
    available,
    probe,
    reset_probes,
)
from slpie.dispatch.local import _environment
from slpie.dispatch.tool import NORMALISED_ENV

from _walk import SLPIE, modules

DISPATCH = SLPIE / "dispatch"


def dispatch_modules():
    """Every module under `dispatch/`, subpackages included.

    `rglob`, not `glob`: the first version of this walk was non-recursive, so a
    module moved into a subpackage would have escaped both audits below while
    they carried on reporting a clean tree. `modules()` refuses an empty match
    for the same reason.
    """
    return modules(DISPATCH)


@pytest.fixture()
def tools() -> ToolRegistry:
    return ToolRegistry()


# --- no shell ------------------------------------------------------------


def test_no_dispatch_path_ever_uses_a_shell():
    """A code review will eventually miss one; an ast walk will not."""
    offenders: list[str] = []

    for path in dispatch_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "shell":
                    continue
                if not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                ):
                    offenders.append(f"{path.name}:{node.lineno} shell=")

    assert offenders == [], f"a shell reached the dispatch path: {offenders}"


def test_dispatch_never_calls_os_system_or_popen_with_a_string():
    banned = {"system", "popen", "getoutput", "getstatusoutput"}
    offenders: list[str] = []

    for path in dispatch_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in banned:
                    offenders.append(f"{path.name}:{node.lineno} {node.func.attr}")

    assert offenders == []


def test_an_argument_carrying_shell_syntax_is_refused(tools: ToolRegistry):
    """Nothing here interprets `;`, so its presence means the caller thought
    something would — and that belief is how injection arrives later."""
    dispatcher = LocalDispatcher()
    with pytest.raises(DispatchError, match="shell syntax"):
        dispatcher.dispatch(tools.require("git"), ["log", "x; rm -rf /"])


def test_a_flag_is_never_mistaken_for_shell_syntax(tools: ToolRegistry):
    """`--pretty=format:%h;%s` is a legitimate git flag."""
    outcome = LocalDispatcher().dispatch(
        tools.require("git"), ["log", "-1", "--pretty=format:%h"],
    )
    assert outcome.available


# --- probing and fallback -----------------------------------------------


def test_probing_reports_what_is_installed(tools: ToolRegistry):
    found = tools.available()

    assert set(found) == set(tools.names)
    assert found["git"] is True, "this repository is a git checkout"


def test_a_tool_records_its_version_so_a_differing_answer_is_attributable(
    tools: ToolRegistry,
):
    outcome = tools.run("git", ["--version"])

    assert outcome.ok
    assert "git version" in outcome.version


def test_a_missing_binary_is_an_outcome_rather_than_an_exception(tools: ToolRegistry):
    absent = Tool(
        name="definitely-not-installed", argv=("slpie-no-such-binary",),
        summary="a tool that does not exist",
        fallback="the stdlib path runs instead",
    )
    tools.add(absent)

    outcome = tools.run("definitely-not-installed")

    assert not outcome.available
    assert not outcome.ok
    assert "the stdlib path runs instead" in outcome.error


def test_a_missing_binary_becomes_a_named_capability_gap(tools: ToolRegistry):
    tools.add(Tool(name="ghost", argv=("slpie-no-such-binary",), summary="absent"))
    tools.run("ghost")

    gaps = tools.gaps()
    assert len(gaps) == 1
    assert gaps[0].subject == "tool://ghost"
    assert gaps[0].remediation


def test_an_alternative_binary_is_tried_before_giving_up():
    """`rg` is preferred and `grep` is the fallback; nothing should say so twice."""
    reset_probes()
    tool = Tool(
        name="search", argv=("slpie-no-such-binary",),
        alternatives=(("git",),), summary="prefers one, falls back to another",
    )
    found, _version, argv = probe(tool)

    assert found
    assert argv[0] == "git", "the alternative was used"
    reset_probes()


def test_an_unregistered_tool_cannot_be_dispatched(tools: ToolRegistry):
    with pytest.raises(DispatchError, match="no tool is registered"):
        tools.run("wget")


def test_an_allow_list_constrains_what_a_dispatcher_will_run(tools: ToolRegistry):
    tools.use(LocalDispatcher(allow=("jq",)))
    outcome = tools.run("git", ["--version"])

    assert not outcome.available
    assert "allow-list" in outcome.error


# --- determinism ---------------------------------------------------------


def test_the_environment_is_normalised_rather_than_inherited():
    """`sort` collates differently under another locale, and a pipeline whose
    answer follows the operator's shell settings cannot be reproduced."""
    environment = _environment()

    for name, value in NORMALISED_ENV.items():
        assert environment[name] == value
    assert "PATH" in environment, "nothing is findable without it"


def test_a_deterministic_tool_leaves_the_flow_reproducible(tools: ToolRegistry):
    tools.run("git", ["--version"])

    assert tools.determinism() is Determinism.DETERMINISTIC
    assert tools.reproducible()


def test_one_environment_bound_tool_makes_the_whole_flow_environment_bound(
    tools: ToolRegistry,
):
    """A chain is as reproducible as its least reproducible input; averaging would
    let one volatile tool hide behind four deterministic ones."""
    tools.run("git", ["--version"])
    tools.add(Tool(
        name="wobbly", argv=("git",), summary="stands in for a volatile tool",
        determinism=Determinism.VOLATILE,
    ))
    tools.run("wobbly", ["--version"])

    assert tools.determinism() is Determinism.VOLATILE
    assert not tools.reproducible()
    assert "snapshot rather than a fact" in tools.determinism().note


def test_a_dispatched_result_is_citable_as_runtime_evidence(tools: ToolRegistry):
    from slpie.domain.evidence import EvidenceKind

    outcome = tools.run("git", ["--version"])
    evidence = tools.evidence_for(outcome)

    assert evidence.kind is EvidenceKind.RUNTIME_TRACE
    assert "git" in evidence.excerpt
    assert outcome.version in evidence.extractor_version


# --- as verbs ------------------------------------------------------------


def test_the_tools_verb_reports_this_machines_capabilities():
    result = Composition.read("tools").run(Context())

    assert result.ok
    assert result.flow.kind is Kind.REPORT
    assert result.flow.facts["available"]["git"] is True


def test_history_comes_from_git_rather_than_a_reimplementation():
    result = Composition.read("history --count 3").run(Context(root="."))

    assert result.ok
    commits = result.flow.value["commits"]
    assert commits, "this repository has history"
    assert all(entry["sha"] and entry["subject"] for entry in commits)
    assert "git version" in result.flow.facts["git"]


def test_piping_to_a_tool_changes_the_kind_so_the_fidelity_loss_is_visible():
    """A binary reads bytes and returns bytes; the typed objects do not come back,
    and downstream verbs needing objects must therefore refuse to follow."""
    verbs = registry()
    composition = Composition.read("history | json | tool --name jq", verbs=verbs)

    assert composition.validate().ok
    assert composition.produces is Kind.TEXT

    onward = Composition.read(
        "history | json | tool --name jq | link", verbs=verbs,
    )
    assert not onward.validate().ok, "TEXT must not satisfy link's OBSERVATIONS"


def test_a_shell_string_cannot_be_smuggled_through_the_tool_verb():
    verbs = registry()
    result = Composition.read(
        "history | tool --name sh --args '-c rm'", verbs=verbs,
    ).run(Context())

    assert not result.ok
    assert "not a dispatchable tool" in result.error
    assert "nothing here runs a shell" in result.error.lower()


def test_a_missing_tool_passes_the_flow_through_and_says_it_did_nothing():
    """Pretending the filter ran would produce a filtered-looking answer that was
    never filtered."""
    tools = ToolRegistry()
    tools.add(Tool(name="pdftotext", argv=("slpie-no-such-binary",), summary="absent"))

    import slpie.dispatch.registry as module
    from slpie.dispatch import registry as dispatch_registry, reset as reset_dispatch

    module._REGISTRY = tools
    try:
        result = Composition.read("history --count 1 | tool --name pdftotext").run(
            Context(root="."),
        )
        assert result.ok
        assert result.flow.facts["applied"] is False
        assert result.flow.gaps
    finally:
        reset_dispatch()


def test_the_dispatch_verbs_are_documented_like_every_other_verb():
    from slpie.manual import page_for

    verbs = registry()
    for name in ("tools", "tool", "history"):
        page = page_for(name, verbs=verbs)
        assert page.verb.summary
        assert page.verb.examples
        assert page.render()
