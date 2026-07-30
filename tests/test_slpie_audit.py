"""The judge — deterministic, reproducible, and honest about its blind spots.

Four properties carry §25:

1. **Reproducible.** The same tree yields the same digest; touching one import
   changes it, and the changed verdict names the file and line. A verdict you
   cannot reproduce is an opinion, and an opinion cannot gate a release.
2. **Deterministic.** No network, no model, no ordering dependence. Filesystem
   order must never reach a verdict.
3. **It agrees with the test it does not replace.** `test_slpie_boundaries.py`
   stays exactly as it is, and the judge is cross-checked against it — so a judge
   that went blind fails the suite rather than quietly passing.
4. **It declines to rule where it cannot see.** An unparseable file and a runtime
   import produce `INDETERMINATE`, count against coverage, and never pass as
   `UPHELD`. The rule that broke this once — ruling on unused dependencies in a
   tree that loads modules by name — is regression-tested below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slpie.audit import (
    Audit,
    Auditor,
    Check,
    Judgement,
    Verdict,
    audit,
    audit_self,
    by_name,
    project,
    rules,
    slpie_checks,
)

REPOSITORY = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def ours() -> Audit:
    return audit_self(REPOSITORY)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A small two-ring tree, written as real files."""
    (tmp_path / "inner").mkdir()
    (tmp_path / "outer").mkdir()
    (tmp_path / "inner" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "inner" / "core.py").write_text(
        "import json\nimport os\n\n\ndef go() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "inner" / "bridge.py").write_text(
        "import outer.thing\n", encoding="utf-8",
    )
    (tmp_path / "outer" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "outer" / "thing.py").write_text(
        "from inner import core\n", encoding="utf-8",
    )
    return tmp_path


# --- reproducible --------------------------------------------------------


def test_the_same_tree_produces_the_same_digest(tree: Path):
    """One comparable value CI can pin: unchanged tree, unchanged digest."""
    assert audit(tree).digest == audit(tree).digest


def test_touching_one_import_changes_the_digest(tree: Path):
    checks = (Check("purity", {"rule": "inner-purity", "ring": "inner"}),)
    before = audit(tree, checks=checks).digest

    (tree / "inner" / "core.py").write_text(
        "import json\nimport requests\n", encoding="utf-8",
    )
    after = audit(tree, checks=checks)

    assert after.digest != before
    assert after.violations[0].evidence[0].location.line == 2, (
        "the changed verdict names the line"
    )


def test_a_judgements_digest_ignores_everything_but_its_claim():
    """A digest that shifted with a timestamp would be useless for its one job."""
    from slpie.audit.judge import upheld

    first = upheld("rule", "subject", "detail")
    second = upheld("rule", "subject", "detail")
    assert first.digest == second.digest
    assert upheld("rule", "other", "detail").digest != first.digest


def test_the_audit_digest_does_not_depend_on_judgement_order():
    """Filesystem order differs between machines; the digest must not."""
    from slpie.audit.judge import upheld

    one = upheld("a", "x")
    two = upheld("b", "y")
    assert Audit((one, two)).digest == Audit((two, one)).digest


def test_the_projection_is_sorted_so_walk_order_cannot_reach_a_verdict(tree: Path):
    names = [module.name for module in project(tree).modules]
    assert names == sorted(names)


# --- deterministic, and it runs on somebody else's tree -----------------


def test_the_judge_runs_against_a_tree_it_was_not_written_for(tree: Path):
    result = audit(tree)

    assert result.scanned == 5
    assert result.verdict_for("readable") is Verdict.UPHELD


def test_a_generic_tree_is_not_failed_against_boundaries_nobody_declared(tree: Path):
    """Asserting somebody else's layer rules would be inventing their architecture
    and then failing them against it."""
    result = audit(tree)

    assert not result.violations
    assert "single-import" not in result.by_rule()


def test_a_named_boundary_is_judged_when_it_is_declared(tree: Path):
    result = audit(tree, checks=(
        Check("single-import", {
            "rule": "inner→outer", "ring": "inner", "target": "outer",
            "allowed": "inner.bridge",
        }),
    ))

    assert result.verdict_for("inner→outer") is Verdict.UPHELD


def test_a_second_bridge_across_a_boundary_is_a_violation_naming_the_line(
    tree: Path,
):
    (tree / "inner" / "sneaky.py").write_text(
        "import outer.thing\n", encoding="utf-8",
    )
    result = audit(tree, checks=(
        Check("single-import", {
            "rule": "inner→outer", "ring": "inner", "target": "outer",
            "allowed": "inner.bridge",
        }),
    ))

    assert result.verdict_for("inner→outer") is Verdict.VIOLATED
    violation = result.violations[0]
    assert violation.subject == "inner.sneaky"
    assert violation.evidence
    assert violation.evidence[0].location.line == 1
    assert violation.remediation


def test_a_relative_import_cannot_sneak_past_a_boundary_check(tmp_path: Path):
    """Leaving `from ..x import y` unresolved is how a relative import evades the
    rule that was meant to catch it."""
    (tmp_path / "inner").mkdir()
    (tmp_path / "inner" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "inner" / "deep").mkdir()
    (tmp_path / "inner" / "deep" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "inner" / "deep" / "sneaky.py").write_text(
        "from ..core import thing\n", encoding="utf-8",
    )
    (tmp_path / "inner" / "core.py").write_text("thing = 1\n", encoding="utf-8")

    imports = project(tmp_path).get("inner.deep.sneaky").imports
    assert imports[0].target == "inner.core", "the relative import was resolved"


def test_a_violation_always_carries_evidence(ours: Audit):
    """A violation with no evidence is an accusation, not a finding."""
    for judgement in ours.judgements:
        assert judgement.grounded, judgement


# --- it declines to rule where it cannot see ----------------------------


def test_an_unparseable_file_is_present_and_undecided_not_absent(tmp_path: Path):
    """Omitting it would shrink the tree the judge examined, and every rule would
    then report a clean pass over a smaller codebase than the one that exists."""
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("import json\n", encoding="utf-8")

    projection = project(tmp_path)
    assert len(projection) == 2
    assert [item.name for item in projection.unparsed] == ["broken"]

    result = audit(tmp_path)
    assert result.verdict_for("readable") is Verdict.INDETERMINATE
    assert "syntax error" in result.indeterminate[0].detail


def test_a_runtime_import_is_reported_as_a_blind_spot(tmp_path: Path):
    (tmp_path / "dyn.py").write_text(
        "import importlib\n\n\ndef load(name):\n"
        "    return importlib.import_module(name)\n",
        encoding="utf-8",
    )
    result = audit(tmp_path)

    assert result.verdict_for("statically-visible") is Verdict.INDETERMINATE
    assert result.indeterminate[0].evidence


def test_ordinary_attribute_access_is_not_reported_as_a_blind_spot(tmp_path: Path):
    """It once flagged `getattr` and fired sixty times on idiomatic Python. A rule
    that noisy gets switched off, taking the real findings with it."""
    (tmp_path / "normal.py").write_text(
        "def go(thing):\n    return getattr(thing, 'name', None)\n",
        encoding="utf-8",
    )
    assert audit(tmp_path).verdict_for("statically-visible") is Verdict.UPHELD


def test_unused_dependencies_are_undecided_when_the_tree_imports_by_name(
    tmp_path: Path,
):
    """The regression that matters. This rule reported nine tree-sitter grammars
    as unused against a repository that loads every one of them through
    `importlib.import_module(config.ts_module)` — ruling confidently on a tree
    where the judge had already declared itself blind."""
    (tmp_path / "loader.py").write_text(
        "import importlib\n\n\n"
        "def grammar(name):\n    return importlib.import_module(name)\n",
        encoding="utf-8",
    )
    result = audit(tmp_path, checks=(
        Check("statically-visible"),
        Check("declared-dependencies", {"declared": ["tree-sitter-ruby"]}),
    ))

    assert result.verdict_for("declared-dependencies") is Verdict.INDETERMINATE
    assert "cannot be decided" in result.indeterminate[-1].detail


def test_unused_dependencies_are_a_violation_when_nothing_is_dynamic(
    tmp_path: Path,
):
    (tmp_path / "plain.py").write_text("import json\n", encoding="utf-8")
    result = audit(tmp_path, checks=(
        Check("declared-dependencies", {"declared": ["requests"]}),
    ))

    assert result.verdict_for("declared-dependencies") is Verdict.VIOLATED
    assert "CVEs against" in result.violations[0].detail


def test_an_undeclared_import_is_a_phantom_dependency(tmp_path: Path):
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")
    result = audit(tmp_path, checks=(
        Check("declared-dependencies", {"declared": ["flask"]}),
    ))

    subjects = {item.subject for item in result.violations}
    assert "requests" in subjects
    assert any("clean install" in item.detail for item in result.violations)


def test_an_import_guarded_by_try_except_is_a_deliberate_optional(tmp_path: Path):
    """Reporting correct defensive code as a phantom dependency is how a useful
    rule gets muted."""
    (tmp_path / "app.py").write_text(
        "try:\n    import boto3\nexcept ImportError:\n    boto3 = None\n",
        encoding="utf-8",
    )
    result = audit(tmp_path, checks=(
        Check("declared-dependencies", {"declared": ["flask"]}),
    ))

    assert "boto3" not in {item.subject for item in result.violations}


def test_a_rule_that_raises_is_undecided_rather_than_a_pass(tmp_path: Path):
    (tmp_path / "a.py").write_text("import json\n", encoding="utf-8")

    from slpie.audit.rules import Rule

    auditor = Auditor((Check("explodes"),))
    auditor._rules["explodes"] = Rule(
        "explodes", "raises",
        lambda projection, options: (_ for _ in ()).throw(RuntimeError("no")),
    )
    result = auditor.run(tmp_path)

    assert result.verdict_for("explodes") is Verdict.INDETERMINATE
    assert result.errors


def test_coverage_counts_the_undecided_against_it(tmp_path: Path):
    """A judge reporting 100% while a tenth of the tree failed to parse is the
    exact dishonesty INDETERMINATE exists to prevent."""
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    result = audit(tmp_path)

    assert result.coverage < 1.0


def test_a_rule_is_not_passing_if_it_could_not_decide_one_subject():
    """Nineteen upheld and one undecided is not a passing rule; reporting it as
    one is how a blind spot becomes a green tick."""
    from slpie.audit.judge import indeterminate, upheld

    mixed = Audit((
        upheld("r", "a"), upheld("r", "b"), indeterminate("r", "c", "unknown"),
    ))
    assert mixed.verdict_for("r") is Verdict.INDETERMINATE


def test_a_violation_outranks_an_undecided():
    from slpie.audit.judge import indeterminate, violated

    mixed = Audit((
        indeterminate("r", "a", "unknown"), violated("r", "b", "wrong"),
    ))
    assert mixed.verdict_for("r") is Verdict.VIOLATED


# --- it agrees with the test it does not replace ------------------------


def test_the_judge_agrees_with_the_boundary_test_on_this_repository(ours: Audit):
    """`test_slpie_boundaries.py` is not weakened or exempted. The judge is
    cross-checked against it, so a judge that went blind fails the suite rather
    than quietly passing."""
    import ast

    offenders: list[str] = []
    for path in sorted((REPOSITORY / "slpie").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "gratimos" for name in names):
                offenders.append(str(path.relative_to(REPOSITORY)))
    offenders = sorted(set(offenders))

    #: The test's own answer, computed independently of the projection.
    assert offenders == ["slpie/artifacts/codegen.py"]

    #: And the judge's.
    assert ours.verdict_for("slpie→gratimos") is Verdict.UPHELD


def test_the_judge_catches_a_deliberately_violated_boundary(tmp_path: Path):
    """If the judge could not fail, its passing would mean nothing."""
    (tmp_path / "slpie").mkdir()
    (tmp_path / "slpie" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "slpie" / "rogue.py").write_text(
        "from gratimos.meta import DataShape\n", encoding="utf-8",
    )
    result = audit(tmp_path, checks=slpie_checks())

    assert result.verdict_for("slpie→gratimos") is Verdict.VIOLATED
    assert result.violations[0].subject == "slpie.rogue"


def test_this_repository_currently_upholds_every_boundary_it_declares(ours: Audit):
    for rule in ("slpie→gratimos", "gratimos→slpie", "kernel-purity"):
        assert ours.verdict_for(rule) is Verdict.UPHELD, ours.render()


def test_the_kernel_purity_rule_would_catch_a_third_party_import(tmp_path: Path):
    (tmp_path / "slpie").mkdir()
    (tmp_path / "slpie" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "slpie" / "web.py").write_text("import requests\n", encoding="utf-8")

    result = audit(tmp_path, checks=(
        Check("purity", {"rule": "kernel-purity", "ring": "slpie"}),
    ))
    assert result.verdict_for("kernel-purity") is Verdict.VIOLATED
    assert "requests" in result.violations[0].detail


def test_the_stdlib_list_comes_from_the_interpreter_not_from_a_hand_written_one():
    """A hand-maintained list fails open: a new stdlib module would be reported as
    a third-party dependency in a kernel that forbids them."""
    import sys

    from slpie.audit.rules import STDLIB

    assert "tomllib" in STDLIB
    assert STDLIB >= set(sys.stdlib_module_names)


# --- the registry judges itself -----------------------------------------


def test_a_reference_entry_with_no_source_is_a_violation(tmp_path: Path):
    class Entry:
        def __init__(self, identifier, source=""):
            self.identifier = identifier
            self.source = source

    result = audit(tmp_path, checks=(Check("citations"),), entries=[
        Entry("a2a", "https://example.invalid/a2a"),
        Entry("mystery"),
    ])

    assert result.verdict_for("citations") is Verdict.VIOLATED
    assert result.violations[0].subject == "mystery"


def test_the_real_reference_registry_cites_every_entry():
    from gratimos.reference import protocol_registry

    result = audit(REPOSITORY, checks=(Check("citations"),), entries=list(
        protocol_registry()
    ))
    assert result.verdict_for("citations") is Verdict.UPHELD, result.render()


def test_a_claim_pointing_at_a_deleted_module_is_a_violation(tmp_path: Path):
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")

    class Claim:
        def __init__(self, term, answered_by):
            self.term = term
            self.answered_by = answered_by

    result = audit(tmp_path, checks=(Check("honesty"),), claims=[
        Claim("validation", "real"),
        Claim("in-silico", "gone"),
    ])

    assert result.verdict_for("honesty") is Verdict.VIOLATED
    assert result.violations[0].subject == "in-silico"
    assert "outlived the code" in result.violations[0].detail


def test_a_gap_contradicted_by_the_graph_is_also_a_violation(tmp_path: Path):
    """A gaps list that only ever grows is marketing; one audited both ways is a
    fact."""
    (tmp_path / "solver.py").write_text("x = 1\n", encoding="utf-8")

    class Gap:
        def __init__(self, term, answered_by):
            self.term = term
            self.answered_by = answered_by

    result = audit(tmp_path, checks=(Check("honesty"),), gaps=[
        Gap("constraint solving", "solver"),
    ])

    assert result.verdict_for("honesty") is Verdict.VIOLATED
    assert "no longer one" in result.violations[0].remediation


# --- as verbs ------------------------------------------------------------


def test_a_verdict_composes_with_everything_else():
    """An architecture finding pipes into filter and explain like any other
    conclusion — one triage list, not two views to reconcile."""
    from slpie.compose import Composition, Context, Kind

    result = Composition.read(
        f"audit {REPOSITORY} | verdicts --only upheld"
    ).run(Context(root=str(REPOSITORY)))

    assert result.ok
    assert result.flow.kind is Kind.JUDGEMENTS
    assert all(item.verdict is Verdict.UPHELD for item in result.flow.items)


def test_the_audit_verb_reports_its_digest_for_a_release_gate():
    from slpie.compose import Composition, Context

    result = Composition.read(f"audit {REPOSITORY}").run(
        Context(root=str(REPOSITORY)),
    )
    assert result.flow.facts["digest"]
    assert "coverage" in result.flow.facts


def test_the_audit_verbs_are_documented_like_every_other_verb():
    from slpie.compose import registry
    from slpie.manual import page_for

    verbs = registry()
    for name in ("audit", "verdicts"):
        page = page_for(name, verbs=verbs)
        assert page.verb.summary
        assert page.verb.examples


def test_every_rule_the_judge_offers_is_named_and_summarised():
    for rule in rules():
        assert rule.name and rule.summary
    assert set(by_name()) == {rule.name for rule in rules()}
