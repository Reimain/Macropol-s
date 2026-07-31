"""Phase 13 — agent tools and the incremental engine.

**Tools are a projection, not a second implementation.** Every tool is a named
composition over the verb registry, so a tool cannot reach a capability that does
not exist, and adding a verb widens the tool set with no change to the agent.
The alternative — ten hand-written functions — makes the tool set an eleventh
place a capability is declared, and it drifts the way every parallel restatement
does.

**An agent is told what it could not see.** A tool returns the flow, so the
reasoning and the gaps reach the model exactly as they reach a terminal. A model
that is not told about a refused capability will answer as though nothing was
missing.

**Incremental means content, never mtime.** A `git checkout` rewrites mtimes on
identical files and a restored build cache writes older ones than recorded — the
first wastes a full rescan, the second silently skips a file that really changed.

**A node cited by three files does not die because one changed.** It is weakened
and recomputed. Retiring it would churn it out of the graph and straight back in
on every scan that touched anything nearby.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slpie.agent import (
    Tool,
    ToolError,
    ToolParam,
    ToolResult,
    ToolRunner,
    ToolSet,
    builtin_tools,
)
from slpie.compose import Composition, registry
from slpie.compose.pipeline import CompositionError
from slpie.compose.verb import Context
from slpie.domain.edge import Edge, EdgeKind
from slpie.domain.evidence import Evidence, EvidenceKind, SourceLocation
from slpie.domain.identity import Purl
from slpie.domain.node import Node, NodeKind
from slpie.domain.reasoning import Enrichment
from slpie.graph.sqlite_graph import SqliteGraph
from slpie.incremental import (
    Delta,
    Fingerprint,
    IncompleteFingerprint,
    Invalidation,
    Plan,
    Skip,
    SkipReason,
    TruncatedWalk,
    UnreadableFile,
    Watcher,
    audit,
    default_strict,
    digest_file,
    evidence_for_uris,
    excluded,
    explain,
    invalidate,
)


@pytest.fixture(scope="module")
def verbs():
    return registry()


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        '{"name": "shop", "version": "1.0.0", "license": "MIT",'
        ' "dependencies": {"lodahs": "^4.0.0", "loose": "*"}}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps({
        "name": "shop", "lockfileVersion": 3, "packages": {
            "node_modules/lodahs": {"version": "4.17.21", "license": "AGPL-3.0"},
            "node_modules/loose": {"version": "0.1.0"},
        }}), encoding="utf-8")
    application = tmp_path / "app"
    application.mkdir()
    (application / "settings.py").write_text(
        'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    return tmp_path


# --- tools are a projection of the registry ---------------------------------


def test_every_tool_is_a_composition_that_type_checks(verbs):
    """A tool that could not run is worse than a tool that does not exist."""
    for tool in ToolSet(root="."):
        arguments = {
            param.name: param.sample for param in tool.params if param.required
        }
        pipeline = tool.pipeline(arguments)
        validation = Composition.read(pipeline, verbs=verbs).validate()

        assert validation.ok, f"{tool.name}: {pipeline!r} — {validation.explain()}"


def test_every_tool_names_only_verbs_this_build_has(verbs):
    for tool in ToolSet(root="."):
        arguments = {
            param.name: param.sample for param in tool.params if param.required
        }
        for stage in Composition.read(tool.pipeline(arguments), verbs=verbs):
            assert stage.verb in verbs, f"{tool.name} names {stage.verb!r}"


def test_a_tool_describes_itself_as_a_json_schema():
    schema = ToolSet(root=".").require("impact_analysis").to_dict()

    assert schema["name"] == "impact_analysis"
    assert "package" in schema["input_schema"]["properties"]
    assert schema["description"]


def test_a_choice_parameter_offers_its_choices_to_the_model():
    schema = ToolSet(root=".").require("sbom").to_dict()

    assert schema["input_schema"]["properties"]["format"]["enum"] == [
        "cyclonedx", "spdx",
    ]


def test_a_required_parameter_is_declared_required():
    schema = ToolSet(root=".").require("run_composition").to_dict()

    assert schema["input_schema"]["required"] == ["pipeline"]


# --- parameters are values, never syntax ------------------------------------


def test_a_tool_owns_its_flag_and_the_model_supplies_only_the_value():
    """The first design asked a model for `"--severity critical"`.

    Quoted, that became one argument and produced `govern '--severity critical'`
    — a flag the verb had never heard of. A model cannot mis-spell syntax it
    never writes.
    """
    tools = ToolSet(root="/tmp/x")

    assert tools.require("governance_scan").pipeline({"severity": "critical"}) == (
        "discover /tmp/x | govern --severity critical"
    )


def test_an_omitted_optional_value_leaves_no_dangling_flag():
    tools = ToolSet(root="/tmp/x")

    assert tools.require("governance_scan").pipeline({}) == "discover /tmp/x | govern"


def test_a_boolean_flag_renders_bare_or_not_at_all():
    """`--safe true` would be read as the next stage's argument."""
    tool = ToolSet(root="/tmp/x").require("safe_upgrade")

    assert tool.pipeline({"safe": "true"}).endswith("options --safe")
    assert tool.pipeline({"safe": "false"}).endswith("options")
    assert tool.pipeline({}).endswith("options")


def test_a_value_outside_the_declared_choices_is_refused():
    with pytest.raises(ToolError, match="not one of"):
        ToolSet(root=".").require("sbom").pipeline({"format": "xml"})


def test_a_missing_required_value_is_refused_with_its_help():
    with pytest.raises(ToolError, match="pipeline"):
        ToolSet(root=".").require("run_composition").pipeline({})


def test_an_argument_the_tool_does_not_take_is_refused():
    with pytest.raises(ToolError, match="does not take"):
        ToolSet(root=".").require("dependency_lookup").pipeline({"nonsense": "x"})


def test_a_hostile_value_becomes_one_quoted_argument(verbs):
    """There is no shell here, and the quoting is belt to that brace."""
    tool = ToolSet(root=".").require("impact_analysis")
    pipeline = tool.pipeline({"package": "lodash; rm -rf /"})

    stages = list(Composition.read(pipeline, verbs=verbs))
    assert [stage.verb for stage in stages] == ["discover", "reason", "radius"]
    assert "rm" not in [stage.verb for stage in stages]


def test_the_composition_escape_hatch_is_not_quoted_into_one_token(verbs):
    """Quoting a pipeline would make every call fail as an unknown verb."""
    pipeline = ToolSet(root=".").require("run_composition").pipeline(
        {"pipeline": "discover . | link | findings"},
    )

    assert [s.verb for s in Composition.read(pipeline, verbs=verbs)] == [
        "discover", "link", "findings",
    ]


def test_the_root_is_bound_by_the_caller_not_chosen_by_the_model():
    """A model that could pick the directory could read one it was never given."""
    tools = ToolSet(root="/srv/allowed")

    for tool in tools:
        assert "package" not in tool.template or "{package}" in tool.template
        if "discover" in tool.template:
            assert "/srv/allowed" in tool.template


def test_a_duplicate_tool_name_is_refused():
    tools = ToolSet(root=".")
    with pytest.raises(ToolError, match="already registered"):
        tools.add(tools.require("findings"))


def test_an_unknown_tool_lists_what_this_build_offers():
    with pytest.raises(ToolError, match="architecture_audit"):
        ToolSet(root=".").require("nonexistent")


def test_a_tool_set_behaves_like_a_collection():
    tools = ToolSet(root=".")

    assert len(tools) == len(builtin_tools("."))
    assert "findings" in tools and "nope" not in tools
    assert tools.get("nope") is None
    assert "findings" in tools.names
    assert "findings" in str(tools.require("findings"))


# --- running a tool ---------------------------------------------------------


def test_a_tool_call_answers_with_its_reasoning_and_its_gaps(repository):
    """The reason an agent over this platform is worth building."""
    result = ToolRunner(root=str(repository)).call(
        "governance_scan", {"severity": "critical"},
    )

    assert result.ok
    assert result.size >= 1
    assert result.reasoning, "how it was reached"
    assert result.gaps, "and what it could not check"
    assert 0 < result.confidence < 1.0, "discounted by those gaps"
    assert "settings.py" in result.render()


def test_a_failed_call_is_a_result_a_model_can_correct_from(repository):
    """A stack trace produces an apology; a named type mismatch produces a fix."""
    result = ToolRunner(root=str(repository)).call(
        "run_composition", {"pipeline": "findings | attach"},
    )

    assert not result.ok
    assert "FINDINGS" in result.error and "NOTHING" in result.error
    assert result.render().startswith("FAILED")


def test_an_unknown_tool_comes_back_as_a_result_not_an_exception(repository):
    result = ToolRunner(root=str(repository)).call("teleport", {})

    assert not result.ok
    assert "no tool called" in result.error


def test_a_mutating_verb_is_unreachable_from_an_agent(repository):
    """An agent cannot confirm a change to somebody's environment for them."""
    result = ToolRunner(root=str(repository)).call(
        "run_composition", {"pipeline": "target --to live"},
    )

    assert not result.ok
    assert "cannot confirm that on somebody's behalf" in result.error


def test_a_stage_that_fails_still_reports_what_was_already_missing(repository):
    result = ToolRunner(root=str(repository)).call(
        "run_composition", {"pipeline": "discover /does/not/exist | link"},
    )

    assert not result.ok
    assert "stopped at" in result.error


def test_a_long_answer_is_truncated_and_says_so(repository, monkeypatch):
    """A model that thinks it has seen everything will say so."""
    import slpie.agent.runner as runner

    monkeypatch.setattr(runner, "MAX_ITEMS", 2)
    result = ToolRunner(root=str(repository)).call("dependency_lookup", {})
    rendered = ToolRunner(root=str(repository)).call("findings", {})

    assert result.ok
    if rendered.size > 2:
        assert rendered.truncated > 0
        assert "not shown" in rendered.render()


def test_an_item_that_would_swallow_the_context_window_is_shortened():
    from slpie.agent.runner import MAX_ITEM_CHARS, _short

    assert len(_short("x" * 5000)) <= MAX_ITEM_CHARS + 1
    assert _short("a\n  b") == "a b"


def test_a_scalar_answer_prefers_the_rendering_a_verb_prepared(repository):
    """`ask` produces one object; its `answer` fact is the readable form of it."""
    result = ToolRunner(root=str(repository)).call(
        "graph_explanation", {"question": "is this consistent?"},
    )

    assert result.ok
    assert result.items, "the prepared rendering was used, not a repr"


def test_a_result_serialises_for_a_transcript(repository):
    body = ToolRunner(root=str(repository)).call("dependency_lookup", {}).to_dict()

    assert body["ok"] is True
    assert body["tool"] == "dependency_lookup"
    assert "pipeline" in body and "gaps" in body


def test_the_runner_describes_every_tool_it_offers():
    schemas = ToolRunner(root=".").describe()

    assert len(schemas) == len(ToolSet(root="."))
    assert all("input_schema" in schema for schema in schemas)


def test_each_call_gets_its_own_isolated_session(repository):
    """Concurrent agents share a memory ceiling and share nothing else."""
    runner = ToolRunner(root=str(repository))
    first = runner.call("dependency_lookup", {})
    second = runner.call("dependency_lookup", {})

    assert first.ok and second.ok
    assert first.size == second.size, "and neither disturbed the other"


# --- fingerprints -----------------------------------------------------------


def test_a_fingerprint_is_content_not_mtime(tmp_path):
    """A `git checkout` rewrites mtimes on files whose content is identical."""
    import os

    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")
    before = Fingerprint.of(tmp_path)

    os.utime(path, (0, 0))
    after = Fingerprint.of(tmp_path)

    assert after.compare(before).empty, "an mtime change is not a content change"


def test_a_changed_byte_is_noticed(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{}", encoding="utf-8")
    before = Fingerprint.of(tmp_path)

    path.write_text("{ }", encoding="utf-8")
    delta = Fingerprint.of(tmp_path).compare(before)

    assert delta.changed == (path.resolve().as_uri(),)
    assert not delta.added and not delta.removed


def test_added_changed_and_removed_are_three_different_instructions(tmp_path):
    """`added` needs discovering; `removed` needs only retirement."""
    (tmp_path / "keep.json").write_text("1", encoding="utf-8")
    (tmp_path / "edit.json").write_text("1", encoding="utf-8")
    (tmp_path / "gone.json").write_text("1", encoding="utf-8")
    before = Fingerprint.of(tmp_path)

    (tmp_path / "edit.json").write_text("2", encoding="utf-8")
    (tmp_path / "gone.json").unlink()
    (tmp_path / "new.json").write_text("1", encoding="utf-8")
    delta = Fingerprint.of(tmp_path).compare(before)

    assert len(delta.added) == 1 and len(delta.changed) == 1
    assert len(delta.removed) == 1 and delta.unchanged == 1
    assert delta.touched == 3
    assert len(delta.to_read) == 2, "removed files are not re-read"
    assert len(delta.stale) == 2, "but changed and removed both go stale"


def test_an_unchanged_tree_produces_an_empty_delta(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    before = Fingerprint.of(tmp_path)

    delta = Fingerprint.of(tmp_path).compare(before)

    assert delta.empty
    assert "nothing changed" in str(delta)


def test_a_tree_digest_is_one_comparable_value(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    (tmp_path / "b.json").write_text("2", encoding="utf-8")

    assert Fingerprint.of(tmp_path).digest == Fingerprint.of(tmp_path).digest

    (tmp_path / "b.json").write_text("3", encoding="utf-8")
    assert Fingerprint.of(tmp_path).digest != Fingerprint.of(tmp_path.parent).digest


def test_a_baseline_survives_the_process(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.json").write_text("1", encoding="utf-8")
    # Saved outside the tree it describes: a baseline written inside would be in
    # its own next fingerprint, and the tree would never compare clean.
    saved = tmp_path / "baseline.json"
    Fingerprint.of(tree).save(saved)

    restored = Fingerprint.load(saved)

    assert len(restored) == 1
    assert restored.digest == Fingerprint.of(tree).digest


def test_a_missing_baseline_reads_as_empty_so_the_first_run_is_a_full_one(tmp_path):
    """No baseline is the ordinary first run; a full scan is the right answer."""
    empty = Fingerprint.load(tmp_path / "absent.json")

    assert len(empty) == 0
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    assert Fingerprint.of(tmp_path).compare(empty).added


def test_an_unreadable_baseline_reads_as_empty_rather_than_crashing(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("not json at all", encoding="utf-8")

    assert len(Fingerprint.load(broken)) == 0


def test_a_file_that_vanishes_mid_walk_is_recorded_rather_than_hashed(
    tmp_path, monkeypatch,
):
    """Somebody saving a file while a scan runs must not fail a lenient scan."""
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    import slpie.incremental.fingerprint as module

    def vanish(_path):
        raise OSError("gone")

    monkeypatch.setattr(module, "digest_file", vanish)
    fingerprint = Fingerprint.of(tmp_path, strict=False)

    assert len(fingerprint) == 0
    assert [item.reason for item in fingerprint.skipped] == [SkipReason.UNREADABLE]
    assert "gone" in fingerprint.skipped[0].detail


def test_a_file_larger_than_the_limit_is_skipped(tmp_path):
    (tmp_path / "big.json").write_text("x" * 5000, encoding="utf-8")

    fingerprint = Fingerprint.of(tmp_path, max_bytes=100, strict=False)

    assert len(fingerprint) == 0
    assert [item.reason for item in fingerprint.skipped] == [SkipReason.TOO_LARGE]


def test_the_walk_is_bounded(tmp_path):
    for index in range(20):
        (tmp_path / f"f{index}.json").write_text("1", encoding="utf-8")

    fingerprint = Fingerprint.of(tmp_path, limit=5, strict=False)

    assert len(fingerprint) == 5
    assert fingerprint.truncated
    assert not fingerprint.complete


def test_a_fingerprint_behaves_like_a_mapping_of_uris(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("1", encoding="utf-8")
    fingerprint = Fingerprint.of(tmp_path)

    assert path.resolve().as_uri() in fingerprint
    assert list(fingerprint) == [path.resolve().as_uri()]
    assert "file(s)" in str(fingerprint)
    assert fingerprint.to_dict()["files"] == 1


def test_one_file_hashes_to_the_same_value_every_time(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 3_000_000)

    assert digest_file(path) == digest_file(path)


def test_a_fingerprint_compares_against_a_plain_mapping(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    current = Fingerprint.of(tmp_path)

    assert current.compare({}).added == tuple(current)


# --- invalidation -----------------------------------------------------------


def evidence(uri: str, line: int = 1) -> Evidence:
    return Evidence(
        kind=EvidenceKind.LOCKFILE_PIN, location=SourceLocation(uri, line=line),
        extractor="test", excerpt=f"{uri}:{line}",
    )


@pytest.fixture()
def graph():
    """One node cited by two files, one cited by one, and an edge between them."""
    built = SqliteGraph(None)
    shared = Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/shared@1.0.0"),
        evidence=(evidence("file:///r/a.json"), evidence("file:///r/b.json")),
    )
    lonely = Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/lonely@1.0.0"),
        evidence=(evidence("file:///r/a.json", 9),),
    )
    built.assert_node(shared)
    built.assert_node(lonely)
    built.assert_edge(Edge(
        kind=EdgeKind.DEPENDS_ON, src=lonely.id, dst=shared.id,
        evidence=(evidence("file:///r/a.json", 12),),
    ))
    built.ids = {"shared": shared.id, "lonely": lonely.id}
    yield built
    built.close()


def test_a_node_cited_only_by_the_changed_file_is_retired(graph):
    found = invalidate(graph, ["file:///r/a.json"])

    assert graph.ids["lonely"] in found.retire_nodes


def test_a_node_cited_by_a_file_that_did_not_change_is_weakened_not_retired(graph):
    """Retiring it would churn it out of the graph and straight back in."""
    found = invalidate(graph, ["file:///r/a.json"])

    assert graph.ids["shared"] in found.weakened
    assert graph.ids["shared"] not in found.retire_nodes


def test_an_edge_loses_its_justification_with_its_file(graph):
    found = invalidate(graph, ["file:///r/a.json"])

    assert found.retire_edges, "the edge was cited only by a.json"
    assert found.retired == len(found.retire_nodes) + len(found.retire_edges)


def test_changing_a_file_nothing_was_drawn_from_invalidates_nothing(graph):
    found = invalidate(graph, ["file:///r/untouched.json"])

    assert found.empty
    assert "nothing was invalidated" in str(found)


def test_a_removed_file_invalidates_without_being_rescanned(graph):
    found = invalidate(graph, [], removed=["file:///r/a.json"])

    assert found.stale_evidence
    assert found.rescan == (), "there is nothing left to read"


def test_enrichments_derived_from_retired_evidence_go_too(graph):
    """A conclusion whose chain terminates in nothing is the dangling chain."""
    stale = evidence("file:///r/a.json").id
    first = Enrichment(
        subject="x", attribute="a", value=1, layer="l", derived_from=(stale,),
    )
    second = Enrichment(
        subject="x", attribute="b", value=2, layer="l", derived_from=(first.id,),
    )
    third = Enrichment(
        subject="x", attribute="c", value=3, layer="l", derived_from=("unrelated",),
    )

    found = invalidate(
        graph, ["file:///r/a.json"],
        enrichments={first.id: first, second.id: second, third.id: third},
    )

    assert first.id in found.retire_enrichments
    assert second.id in found.retire_enrichments, "transitively"
    assert third.id not in found.retire_enrichments


def test_evidence_is_looked_up_by_the_index_the_schema_exists_for(graph):
    found = evidence_for_uris(graph, ["file:///r/a.json", "file:///r/nothing"])

    assert len(found) == 3, "two nodes and one edge cited a.json"


def test_an_invalidation_serialises_for_a_report(graph):
    body = invalidate(graph, ["file:///r/a.json"]).to_dict()

    assert body["stale_evidence"] > 0
    assert isinstance(body["retire_nodes"], list)


# --- the watcher ------------------------------------------------------------


def test_a_watcher_reports_what_a_rescan_would_do_before_doing_any_of_it(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")
    watcher.commit()

    (tmp_path / "b.json").write_text("2", encoding="utf-8")
    plan = watcher.plan()

    assert plan.delta.added
    assert plan.worth_it
    assert "moved" in plan.reason
    assert plan.to_dict()["proportion"] > 0


def test_an_unchanged_tree_is_no_work_at_all(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")
    watcher.commit()

    plan = watcher.plan()

    assert plan.delta.empty
    assert not plan.worth_it
    assert "no work to do" in plan.reason


def test_a_tree_that_mostly_moved_is_reported_as_not_worth_doing_piecemeal(tmp_path):
    """Retiring and re-deriving most of a graph costs more than one full scan."""
    for index in range(10):
        (tmp_path / f"f{index}.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")
    watcher.commit()

    for index in range(10):
        (tmp_path / f"f{index}.json").write_text("2", encoding="utf-8")
    plan = watcher.plan()

    assert plan.proportion == 1.0
    assert not plan.worth_it
    assert "costs more than one full rescan" in plan.reason


def test_a_plan_names_the_graph_subjects_that_stop_being_true(tmp_path):
    """The two halves joined: a changed file, and what it stops justifying."""
    changed = tmp_path / "a.json"
    other = tmp_path / "b.json"
    changed.write_text("1", encoding="utf-8")
    other.write_text("1", encoding="utf-8")

    built = SqliteGraph(None)
    lonely = Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/lonely@1.0.0"),
        evidence=(evidence(changed.resolve().as_uri()),),
    )
    built.assert_node(lonely)

    watcher = Watcher(tmp_path, baseline=tmp_path / ".slpie" / "base.json")
    watcher.commit()
    changed.write_text("2", encoding="utf-8")

    plan = watcher.plan(built)
    built.close()

    assert plan.delta.changed == (changed.resolve().as_uri(),)
    assert lonely.id in plan.invalidation.retire_nodes
    assert "re-read  a.json" in plan.render()
    assert plan.worth_it, "one file of two is worth doing incrementally"


def test_a_baseline_is_committed_after_a_rescan_never_before(tmp_path):
    """A scan that died halfway must not leave a baseline claiming it finished."""
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")

    assert watcher.plan().delta.added, "no baseline yet: everything is new"
    watcher.commit()
    assert watcher.plan().delta.empty, "committed: now nothing is new"


def test_forgetting_the_baseline_makes_the_next_scan_a_full_one(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")
    watcher.commit()

    assert watcher.forget() is True
    assert watcher.forget() is False, "forgetting twice is not an error"
    assert watcher.plan().delta.added


def test_the_current_fingerprint_is_cached_and_can_be_refreshed(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")
    first = watcher.current()

    (tmp_path / "b.json").write_text("2", encoding="utf-8")

    assert watcher.current() is first, "cached, so a plan does not walk twice"
    assert len(watcher.current(refresh=True)) == 2


def test_a_watcher_reports_its_delta_directly(tmp_path):
    (tmp_path / "a.json").write_text("1", encoding="utf-8")
    watcher = Watcher(tmp_path, baseline=tmp_path / "base.json")

    assert isinstance(watcher.delta(), Delta)
    assert isinstance(watcher.baseline(), Fingerprint)


def test_a_plan_over_an_empty_tree_has_nothing_to_say(tmp_path):
    plan = Watcher(tmp_path, baseline=tmp_path / "base.json").plan()

    assert plan.proportion == 0.0
    assert plan.reason in plan.render()
    assert str(plan) == plan.reason


# --- rendering the awkward cases --------------------------------------------


def test_a_truncated_result_says_how_much_was_left_out():
    result = ToolResult(
        tool="findings", pipeline="x", ok=True, kind="findings", size=100,
        items=("one",), truncated=99, confidence=0.9, grounded=True,
    )
    rendered = result.render()

    assert "99 more not shown" in rendered
    assert "narrow the question" in rendered
    assert str(result) == rendered


def test_a_result_with_no_gaps_does_not_invent_a_section():
    rendered = ToolResult(
        tool="t", pipeline="x", ok=True, kind="report", size=1, items=("a",),
        confidence=1.0, grounded=True,
    ).render()

    assert "what limits this answer" not in rendered
    assert "how this was reached" not in rendered
    assert "(not every claim traces to a file)" not in rendered


def test_an_ungrounded_result_says_so():
    rendered = ToolResult(
        tool="t", pipeline="x", ok=True, size=1, items=("a",), grounded=False,
    ).render()

    assert "not every claim traces to a file" in rendered


def test_a_subject_whose_row_is_already_gone_is_skipped(graph):
    """Another writer may have retired it between the lookup and the read."""
    class Racing(type(graph)):
        def node(self, node_id):
            return None

    racing = Racing(None)
    racing.subjects_of_evidence = lambda ids: (("node", "vanished"),)
    racing.evidence_by_uri = lambda uri: ("some-evidence",)

    found = invalidate(racing, ["file:///r/a.json"])
    racing.close()

    assert found.retire_nodes == ()


def test_nothing_gone_means_no_enrichment_is_doomed():
    from slpie.incremental.invalidation import _dependent_enrichments

    assert _dependent_enrichments({}, set()) == ()


def test_a_plan_lists_what_it_will_retire_as_well_as_what_it_will_re_read(tmp_path):
    gone = tmp_path / "gone.json"
    gone.write_text("1", encoding="utf-8")
    (tmp_path / "kept.json").write_text("1", encoding="utf-8")

    built = SqliteGraph(None)
    built.assert_node(Node(
        kind=NodeKind.PACKAGE, identity=Purl.parse("pkg:npm/x@1.0.0"),
        evidence=(evidence(gone.resolve().as_uri()),),
    ))
    watcher = Watcher(tmp_path, baseline=tmp_path / ".slpie" / "base.json")
    watcher.commit()
    gone.unlink()

    plan = watcher.plan(built)
    built.close()

    assert plan.delta.removed
    assert "retire   gone.json" in plan.render()


# --- the two halves meet ----------------------------------------------------


def test_an_agent_sees_a_verb_added_after_it_was_written(verbs):
    """The projection's payoff: `radius` needed no change here to be reachable."""
    tools = ToolSet(root=".")

    assert any("radius" in tool.template for tool in tools)
    assert any("govern" in tool.template for tool in tools)
    assert any("enterprise" in tool.template for tool in tools)


def test_the_incremental_plan_and_the_scan_agree_about_what_changed(repository):
    """A rescan reads what the fingerprint said moved, and nothing else."""
    watcher = Watcher(repository, baseline=repository / ".slpie" / "fp.json")
    watcher.commit()

    (repository / "app" / "settings.py").write_text(
        'AWS_ACCESS_KEY = "AKIAIOSFODNN7ROTATED"\n', encoding="utf-8",
    )
    plan = watcher.plan()

    assert len(plan.delta.changed) == 1
    assert plan.delta.changed[0].endswith("settings.py")
    assert plan.worth_it


# --- the two modes: never a wrong graph about a file nobody read ------------
#
# The defect these cover was measured, not imagined. On a ten-file tree a walk
# limit of four reported six live files as removed, and a size limit of zero
# reported all ten — and a rescan acting on either would have retired the graph
# nodes drawn from files that were still on disk and perfectly fine.


def _ten_files(root):
    for index in range(10):
        (root / f"f{index}.txt").write_text(f"content {index}\n", encoding="utf-8")
    return root


def test_a_file_the_walk_did_not_read_is_never_reported_as_removed(tmp_path):
    """The defect, directly: unread is not gone."""
    full = Fingerprint.of(_ten_files(tmp_path))
    (tmp_path / "f0.txt").write_text("x" * 5000, encoding="utf-8")
    partial = Fingerprint.of(tmp_path, max_bytes=100, strict=False)

    delta = partial.compare(full)

    assert delta.removed == ()
    assert delta.unknown == ((tmp_path / "f0.txt").resolve().as_uri(),)
    assert not delta.trustworthy


def test_a_size_limit_that_reads_nothing_retires_nothing(tmp_path):
    full = Fingerprint.of(_ten_files(tmp_path))
    partial = Fingerprint.of(tmp_path, max_bytes=0, strict=False)

    delta = partial.compare(full)

    assert len(partial) == 0
    assert delta.removed == ()
    assert len(delta.unknown) == 10


def test_a_file_genuinely_deleted_is_still_reported_as_removed(tmp_path):
    """The fix must not buy safety by never retiring anything."""
    full = Fingerprint.of(_ten_files(tmp_path))
    (tmp_path / "f3.txt").unlink()

    delta = Fingerprint.of(tmp_path).compare(full)

    assert len(delta.removed) == 1
    assert delta.removed[0].endswith("f3.txt")
    assert delta.unknown == ()
    assert delta.trustworthy


def test_strict_mode_refuses_to_fingerprint_a_tree_it_could_not_read(tmp_path):
    _ten_files(tmp_path)

    with pytest.raises(IncompleteFingerprint) as caught:
        Fingerprint.of(tmp_path, max_bytes=0, strict=True)

    error = caught.value
    assert error.reasons == ("too_large",)
    assert len(error.skipped) == 10
    assert error.root == str(tmp_path.resolve())
    assert "would report those files as *removed*" in str(error)


def test_strict_mode_refuses_a_walk_that_stopped_at_its_limit(tmp_path):
    """Separate exception, because a stopped walk cannot name what it missed."""
    _ten_files(tmp_path)

    with pytest.raises(TruncatedWalk) as caught:
        Fingerprint.of(tmp_path, limit=4, strict=True)

    assert caught.value.limit == 4
    assert caught.value.root == str(tmp_path.resolve())
    assert "would retire most of the graph" in str(caught.value)


def test_a_truncated_walk_does_not_record_one_object_per_unreached_file(tmp_path):
    """The list form is what runs a two-million-file monorepo out of memory."""
    for index in range(200):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")

    fingerprint = Fingerprint.of(tmp_path, limit=3, strict=False)

    assert fingerprint.skipped == ()      # nothing accumulated
    assert fingerprint.truncated          # but the fact is not lost
    assert "cannot be named" in fingerprint.explain_skips()
    assert str(fingerprint).endswith("truncated")


def test_a_truncated_walk_retires_nothing_at_all(tmp_path):
    """It cannot tell a deletion from a file it never reached, so it says so."""
    full = Fingerprint.of(_ten_files(tmp_path))
    (tmp_path / "f9.txt").unlink()

    delta = Fingerprint.of(tmp_path, limit=3, strict=False).compare(full)

    assert delta.removed == ()            # even f9, which really is gone
    assert len(delta.unknown) == 7
    assert not delta.trustworthy


def test_the_exception_carries_its_files_rather_than_only_a_message(tmp_path):
    """rope's shape: act on the fields, never parse prose out of `str(e)`."""
    _ten_files(tmp_path)

    with pytest.raises(IncompleteFingerprint) as caught:
        Fingerprint.of(tmp_path, max_bytes=0, strict=True)

    uris = {item.uri for item in caught.value.skipped}
    assert uris == {
        (tmp_path / f"f{index}.txt").resolve().as_uri() for index in range(10)
    }
    assert all(item.reason is SkipReason.TOO_LARGE for item in caught.value.skipped)


def test_a_long_skip_list_is_summarised_rather_than_dumped(tmp_path):
    for index in range(30):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")

    with pytest.raises(IncompleteFingerprint) as caught:
        Fingerprint.of(tmp_path, max_bytes=0, strict=True)

    message = str(caught.value)
    assert "… and 22 more" in message
    assert message.count("  - ") == 8


def test_an_excluded_directory_is_counted_and_does_not_raise(tmp_path):
    """A `node_modules` the operator configured away is not news."""
    (tmp_path / "keep.txt").write_text("1", encoding="utf-8")
    vendored = tmp_path / "node_modules" / "left-pad"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("1", encoding="utf-8")

    fingerprint = Fingerprint.of(tmp_path, strict=True)

    assert len(fingerprint) == 1
    assert fingerprint.excluded == 1      # counted, never a list
    assert fingerprint.skipped == ()
    assert fingerprint.complete
    assert fingerprint.unread == frozenset()


def test_an_excluded_file_never_becomes_unknown(tmp_path):
    """Safe skips must not leak into the delta as files nobody can account for."""
    (tmp_path / "keep.txt").write_text("1", encoding="utf-8")
    vendored = tmp_path / "node_modules"
    vendored.mkdir()
    (vendored / "index.js").write_text("1", encoding="utf-8")

    first = Fingerprint.of(tmp_path)
    (tmp_path / "keep.txt").write_text("2", encoding="utf-8")

    delta = Fingerprint.of(tmp_path).compare(first)

    assert delta.unknown == ()
    assert delta.trustworthy


def test_an_unread_file_the_baseline_never_knew_about_is_not_unknown(tmp_path):
    """Nothing was derived from it, so there is nothing to leave alone."""
    _ten_files(tmp_path)
    baseline = Fingerprint.of(tmp_path)
    for index in range(10, 20):
        (tmp_path / f"f{index}.txt").write_text("new\n", encoding="utf-8")

    delta = Fingerprint.of(tmp_path, max_bytes=0, strict=False).compare(baseline)

    assert len(delta.unknown) == 10  # the ten the baseline knew, not twenty


def test_the_mode_comes_from_the_environment_when_nobody_names_one(
    tmp_path, monkeypatch,
):
    _ten_files(tmp_path)
    monkeypatch.setenv("SLPIE_STRICT", "0")

    assert default_strict() is False
    assert len(Fingerprint.of(tmp_path, max_bytes=0)) == 0   # lenient: no raise

    monkeypatch.setenv("SLPIE_STRICT", "1")
    assert default_strict() is True
    with pytest.raises(IncompleteFingerprint):
        Fingerprint.of(tmp_path, max_bytes=0)


def test_strict_is_the_default_when_the_environment_says_nothing(monkeypatch):
    """The safe mode must be the one you get by not thinking about it."""
    monkeypatch.delenv("SLPIE_STRICT", raising=False)
    assert default_strict() is True

    for off in ("0", "false", "no", "off", "OFF", " False "):
        monkeypatch.setenv("SLPIE_STRICT", off)
        assert default_strict() is False


def test_a_broken_symlink_is_recorded_rather_than_crashing_the_walk(
    tmp_path, monkeypatch,
):
    """`is_file()` stats, and a stat can fail before there is a uri to name."""
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    from pathlib import Path as RealPath

    original = RealPath.is_file

    def refuse(self):
        if self.name == "a.txt":
            raise OSError("stale handle")
        return original(self)

    monkeypatch.setattr(RealPath, "is_file", refuse)
    fingerprint = Fingerprint.of(tmp_path, strict=False)

    assert len(fingerprint) == 0
    assert fingerprint.skipped[0].reason is SkipReason.UNREADABLE
    assert "stale handle" in fingerprint.skipped[0].detail


# --- explaining, which is the whole of the development mode -----------------


def test_a_skip_explains_itself_in_one_line():
    skip = Skip("file:///x/big.bin", SkipReason.TOO_LARGE, "900 MB")

    assert str(skip) == "big.bin: too_large — 900 MB"
    assert skip.to_dict() == {
        "uri": "file:///x/big.bin", "reason": "too_large", "detail": "900 MB",
    }


def test_a_skip_with_no_detail_and_no_slash_still_names_itself():
    assert Skip("bare", SkipReason.UNREADABLE).explain() == "bare: unreadable"


def test_the_explanation_groups_by_reason():
    skipped = [Skip(f"file:///x/f{i}.txt", SkipReason.TOO_LARGE) for i in range(7)]
    skipped.append(Skip("file:///x/gone.txt", SkipReason.UNREADABLE, "denied"))

    rendered = explain(skipped)

    assert "too_large: 7 file(s)" in rendered
    assert "… and 2 more" in rendered
    assert "unreadable: 1 file(s)" in rendered
    assert rendered.index("too_large") < rendered.index("unreadable")


def test_explaining_nothing_says_so_rather_than_rendering_empty():
    assert explain(()) == "  every file was read"


def test_a_fingerprint_explains_its_own_skips(tmp_path):
    _ten_files(tmp_path)

    fingerprint = Fingerprint.of(tmp_path, max_bytes=0, strict=False)

    assert "too_large: 10 file(s)" in fingerprint.explain_skips()
    assert "10 unread" in str(fingerprint)


def test_naming_one_unreadable_file_does_not_render_a_list():
    """The single-file form exists because a list of one is noise."""
    error = UnreadableFile("file:///x/a.txt", "permission denied")

    assert error.uri == "file:///x/a.txt"
    assert str(error) == "cannot read file:///x/a.txt: permission denied"
    assert str(UnreadableFile("file:///x/a.txt")) == "cannot read file:///x/a.txt"


def test_the_audit_is_the_one_place_the_rule_lives():
    """Two callers cannot apply the mode differently if there is only one gate."""
    skip = Skip("file:///x/b.txt", SkipReason.UNREADABLE)

    assert audit("/x", [], strict=True) == ()
    assert audit("/x", [skip], strict=False) == (skip,)
    with pytest.raises(IncompleteFingerprint):
        audit("/x", [skip], strict=True)


def test_the_exclusion_policy_is_asked_rather_than_recorded():
    assert excluded("file:///x/node_modules/a.js")
    assert excluded("file:///x/.git/HEAD")
    assert not excluded("file:///x/src/a.js")


# --- it survives the wire and the baseline file -----------------------------


def test_skips_survive_a_baseline_round_trip(tmp_path):
    _ten_files(tmp_path)
    fingerprint = Fingerprint.of(tmp_path, max_bytes=0, strict=False)

    written = fingerprint.save(tmp_path / "out" / "fp.json")
    restored = Fingerprint.load(written)

    assert restored.skipped == fingerprint.skipped
    assert restored.unread == fingerprint.unread
    assert restored.to_dict()["complete"] is False


def test_a_baseline_written_before_this_existed_still_loads(tmp_path):
    """An older baseline has no `skipped` key, and must not fail to load."""
    (tmp_path / "old.json").write_text(
        '{"root": "/x", "digests": {"file:///x/a": "ab"}}', encoding="utf-8",
    )

    restored = Fingerprint.load(tmp_path / "old.json")

    assert restored.skipped == ()
    assert restored.complete


def test_a_delta_states_what_it_could_not_account_for(tmp_path):
    _ten_files(tmp_path)
    full = Fingerprint.of(tmp_path)

    delta = Fingerprint.of(tmp_path, max_bytes=0, strict=False).compare(full)

    assert delta.to_dict()["unknown"] == list(delta.unknown)
    assert delta.to_dict()["trustworthy"] is False
    assert "10 unread and therefore left alone" in str(delta)


def test_an_empty_delta_still_says_what_it_could_not_see(tmp_path):
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    baseline = Fingerprint.of(tmp_path)

    delta = Fingerprint.of(tmp_path, max_bytes=0, strict=False).compare(baseline)

    assert delta.empty            # nothing added, changed or removed
    assert not delta.trustworthy  # and that is not the same as nothing to say
    assert "1 unread and therefore left alone" in str(delta)


# --- through the watcher and the verb ---------------------------------------


def test_the_watcher_carries_the_skips_through_its_own_rebuild(tmp_path):
    """It rebuilds the fingerprint to drop the baseline; the skips must survive."""
    _ten_files(tmp_path)
    watcher = Watcher(
        tmp_path, baseline=tmp_path / ".slpie" / "b.json",
        strict=False, max_bytes=0,
    )

    current = watcher.current(refresh=True)

    assert len(current.skipped) == 10
    assert not current.complete


def test_the_watcher_carries_truncation_through_its_own_rebuild(tmp_path):
    _ten_files(tmp_path)
    watcher = Watcher(tmp_path, strict=False, limit=4)

    assert watcher.current(refresh=True).truncated


def test_a_partial_plan_says_so_and_retires_nothing(tmp_path):
    _ten_files(tmp_path)
    baseline = tmp_path / ".slpie" / "b.json"
    Watcher(tmp_path, baseline=baseline).commit()

    partial = Watcher(tmp_path, baseline=baseline, strict=False, max_bytes=0)
    plan = partial.plan()

    assert plan.delta.removed == ()
    assert not plan.trustworthy
    assert "could not be read on this pass" in plan.caveat
    assert plan.to_dict()["trustworthy"] is False
    assert len(plan.to_dict()["skipped"]) == 10


def test_a_partial_plan_renders_the_files_it_left_alone(tmp_path):
    _ten_files(tmp_path)
    baseline = tmp_path / ".slpie" / "b.json"
    Watcher(tmp_path, baseline=baseline).commit()
    (tmp_path / "f0.txt").write_text("moved\n", encoding="utf-8")

    rendered = Watcher(
        tmp_path, baseline=baseline, strict=False, max_bytes=0,
    ).plan().render()

    assert "(left alone)" in rendered
    assert "files not read:" in rendered
    assert "too_large" in rendered


def test_a_truncated_plan_says_it_cannot_name_what_it_missed(tmp_path):
    _ten_files(tmp_path)
    baseline = tmp_path / ".slpie" / "b.json"
    Watcher(tmp_path, baseline=baseline).commit()

    plan = Watcher(tmp_path, baseline=baseline, strict=False, limit=4).plan()

    assert plan.truncated
    assert "the walk stopped at its file limit" in plan.caveat
    assert "cannot be named" in plan.render()
    assert plan.to_dict()["truncated"] is True


def test_a_complete_plan_carries_no_caveat_and_renders_none(tmp_path):
    _ten_files(tmp_path)
    baseline = tmp_path / ".slpie" / "b.json"
    watcher = Watcher(tmp_path, baseline=baseline)
    watcher.commit()
    (tmp_path / "f0.txt").write_text("moved\n", encoding="utf-8")

    plan = watcher.plan()

    assert plan.trustworthy
    assert plan.caveat == ""
    assert "files not read:" not in plan.render()


def test_a_strict_watcher_refuses_rather_than_planning_over_half_a_tree(tmp_path):
    _ten_files(tmp_path)

    with pytest.raises(IncompleteFingerprint):
        Watcher(tmp_path, strict=True, max_bytes=0).plan()

    with pytest.raises(TruncatedWalk):
        Watcher(tmp_path, strict=True, limit=4).plan()


def test_a_watcher_given_no_budgets_leaves_the_defaults_alone(tmp_path):
    """`None` must mean "not overridden", not "zero"."""
    _ten_files(tmp_path)

    assert Watcher(tmp_path)._bounds() == {}
    assert len(Watcher(tmp_path).current()) == 10


def test_the_changed_verb_reports_unread_files_as_a_gap(tmp_path, verbs):
    _ten_files(tmp_path)
    Watcher(tmp_path, baseline=tmp_path / ".slpie" / "fingerprint.json").commit()

    result = Composition.read("changed --lenient --max-bytes 0", verbs=verbs).run(
        Context(root=str(tmp_path)),
    )

    assert result.ok
    states = {row["state"] for row in result.flow.value}
    assert "unknown" in states
    assert "removed" not in states
    assert result.flow.gaps
    assert "unaccounted for" in result.flow.gaps[0].detail
    assert result.flow.facts["trustworthy"] is False


def test_the_changed_verb_defaults_to_refusing(tmp_path, verbs, monkeypatch):
    """Production mode: the crafted exception reaches the caller, named."""
    monkeypatch.delenv("SLPIE_STRICT", raising=False)
    _ten_files(tmp_path)

    result = Composition.read("changed --max-bytes 0", verbs=verbs).run(
        Context(root=str(tmp_path)),
    )

    assert not result.ok
    assert result.failed == "changed"
    assert result.error.startswith("IncompleteFingerprint:")
    assert "would report those files as *removed*" in result.error


def test_the_changed_verb_takes_strict_explicitly_over_the_environment(
    tmp_path, verbs, monkeypatch,
):
    monkeypatch.setenv("SLPIE_STRICT", "0")
    _ten_files(tmp_path)

    result = Composition.read("changed --strict --max-bytes 0", verbs=verbs).run(
        Context(root=str(tmp_path)),
    )

    assert result.error.startswith("IncompleteFingerprint:")


def test_a_truncated_walk_through_the_verb_names_the_limit(tmp_path, verbs):
    _ten_files(tmp_path)

    result = Composition.read("changed --limit 4", verbs=verbs).run(
        Context(root=str(tmp_path)),
    )

    assert result.error.startswith("TruncatedWalk:")
    assert "limit of 4" in result.error


def test_a_budget_that_is_not_a_number_is_refused_before_the_walk(verbs):
    """The registry's own typing catches it, so the verb needs no second parse."""
    with pytest.raises(CompositionError, match="whole number"):
        Composition.read("changed --limit nine", verbs=verbs).run()


def test_a_complete_scan_through_the_verb_carries_no_gap(tmp_path, verbs):
    _ten_files(tmp_path)
    Watcher(tmp_path, baseline=tmp_path / ".slpie" / "fingerprint.json").commit()
    (tmp_path / "f0.txt").write_text("moved\n", encoding="utf-8")

    result = Composition.read("changed", verbs=verbs).run(Context(root=str(tmp_path)))

    assert result.ok
    assert result.flow.gaps == ()
    assert result.flow.facts["trustworthy"] is True
    assert [row["state"] for row in result.flow.value] == ["changed"]
