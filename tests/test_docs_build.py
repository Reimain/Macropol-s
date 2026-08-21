"""The documentation configuration, and the bridge it depends on.

The Sphinx build itself takes minutes and runs in its own CI job. What is worth
asserting in the suite is the part that can break silently: `docs/conf.py`
converts two Markdown constructs into reStructuredText on the way into autodoc,
and if that conversion regressed the pages would still build — they would just
render a table as a row of pipes and a code block as three literal backticks.
A broken renderer that produces output is exactly the kind of thing a
build-succeeded check does not catch.

The last test is the one that matters most over time: it walks every docstring
in the repository and asserts the bridge has a rule for every Markdown construct
actually in use. Somebody writing a new docstring with a construct nobody
anticipated finds out here, rather than by looking at a mangled page later.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("slpie", "slpie_enterprise", "gratimos", "tools")


@pytest.fixture(scope="module")
def conf():
    """Load `docs/conf.py` as a module without executing a Sphinx build."""
    path = ROOT / "docs" / "conf.py"
    assert path.is_file(), "docs/conf.py is gone; the documentation has no config"

    spec = importlib.util.spec_from_file_location("_docs_conf", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_docs_conf"] = module
    spec.loader.exec_module(module)
    return module


# --- the configuration ------------------------------------------------------


def test_single_backticks_render_as_code(conf):
    """1,872 of them. The reST default would render every one as a book title."""
    assert conf.default_role == "code"


def test_the_ring_one_imports_are_mocked(conf):
    """So the documentation builds from the same kernel-only install as CI.

    If this list ever shrank to nothing, the docs job would need the enterprise
    extras — making the documentation the one artifact that cannot be produced
    from the install invariant 4 exists to protect.
    """
    mocked = set(conf.autodoc_mock_imports)

    assert {"kubernetes", "boto3"} <= mocked
    assert "slpie" not in mocked and "gratimos" not in mocked, (
        "mocking a first-party package would document a stub instead of the code"
    )


def test_napoleon_stays_out(conf):
    """It has nothing to convert here, and it actively mangles what is here.

    Not one Google or NumPy section exists across the whole repository — no
    `Args:`, no `Returns:`, no `Attributes:` — so Napoleon had no work to do.
    What it did instead was read an attribute or property docstring as
    `type: description` and split it on the **first colon**, which turned
    `SourceLocation.reference`'s "`file.py:42` — the form a human wants" into a
    type of "`file.py" and a description of "42` — ...", leaving an unclosed
    backtick and a mangled page.

    Any docstring whose first line contains a colon inside inline markup would
    hit the same thing, and this codebase writes `file.py:42` constantly. So the
    extension is not merely unnecessary, it is a hazard, and re-adding it should
    be a deliberate decision rather than a tidy-up.
    """
    assert "sphinx.ext.napoleon" not in conf.extensions


def test_no_docstring_has_grown_a_google_section():
    """The premise of leaving Napoleon out. If somebody writes one, it will
    render as body text — so this fails first and the choice is explicit."""
    sections = re.compile(
        r"^[ \t]*(Args|Arguments|Returns|Yields|Raises|Attributes|Parameters)"
        r"[ \t]*:[ \t]*$", re.M,
    )
    offenders = sorted({
        str(path) for path, text in _docstrings() if sections.search(text)
    })

    assert not offenders, (
        "these docstrings use a Google/NumPy section, which nothing renders "
        "now that `sphinx.ext.napoleon` is out:\n  " + "\n  ".join(offenders)
    )


def test_undocumented_members_are_still_listed(conf):
    """Omitting them answers 'does this exist?' with 'no' for things that do."""
    assert conf.autodoc_default_options["undoc-members"] is True
    assert conf.autodoc_default_options["member-order"] == "bysource"


def test_the_build_does_not_need_the_network(conf):
    """A docs build that reached out would fail whenever the network did, which
    no other build in this repository does."""
    assert conf.intersphinx_mapping == {}


def test_the_ambiguity_suppression_does_not_depend_on_the_environment():
    """The regression test for a deploy that failed on 181 warnings.

    `suppress_warnings` used to be `["ref.python"] if OFFLINE else []`. Every
    local build ran with the suppression on; CI set `SPHINX_INTERSPHINX=1` and
    ran with it off. The branch CI took had therefore never been executed, and
    the first thing to exercise it was a Pages deploy, which died under `-W`.

    The theory behind the branch was that loading CPython's inventory would make
    `bytes` resolve to the builtin. It does not: Sphinx's Python domain searches
    every *documented* object for an unqualified name, and this repository
    documents `BlockRef.bytes`, `Dataset.bytes`, `Fact.object` and more, so the
    ambiguity is a property of the code and no inventory removes it.

    So the rule is the one that was violated: loading the configuration under
    any environment must produce the same configuration.
    """
    import importlib.util
    import os

    def load(**environment):
        previous = {k: os.environ.get(k) for k in environment}
        os.environ.update({k: v for k, v in environment.items() if v is not None})
        for key, value in environment.items():
            if value is None:
                os.environ.pop(key, None)
        try:
            spec = importlib.util.spec_from_file_location(
                "_docs_conf_probe", ROOT / "docs" / "conf.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.suppress_warnings
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    off = load(SPHINX_INTERSPHINX=None)
    on = load(SPHINX_INTERSPHINX="1")

    assert off == on, (
        f"the docs configuration changes with the environment: {off} vs {on}. "
        f"Whatever CI runs must be what a contributor runs, or the branch CI "
        f"takes is one nobody has ever built."
    )
    assert "ref.python" in off, (
        "the cross-reference ambiguity suppression is gone; `-W` will fail on "
        "181 warnings about attributes named `bytes` and `object`"
    )


# --- the bridge -------------------------------------------------------------


def test_a_fenced_block_becomes_a_code_block(conf):
    converted = conf._bridge(
        "Before.\n\n```python\nx = 1\nif x:\n    pass\n```\n\nAfter.\n"
    )

    assert ".. code-block:: python" in converted
    assert "   x = 1" in converted
    assert "       pass" in converted, "the block's own indentation was flattened"
    assert "```" not in converted
    assert converted.strip().endswith("After.")


def test_an_unlabelled_fence_gets_a_language_that_always_lexes(conf):
    """`text`, not `default`: `default` guesses and warns once per block when it
    cannot, and several of these blocks are ASCII diagrams that it cannot."""
    converted = conf._bridge("```\ncredential -> Provider -> Principal\n```")

    assert ".. code-block:: text" in converted


def test_a_pipe_table_becomes_a_list_table(conf):
    converted = conf._bridge(
        "| Verdict | Meaning |\n"
        "|---|---|\n"
        "| ACCEPT | routed to its store |\n"
        "| DROP | refused, silently |\n"
    )

    assert ".. list-table::" in converted
    assert ":header-rows: 1" in converted
    assert "* - Verdict" in converted
    assert "  - Meaning" in converted
    assert "* - DROP" in converted
    assert "|---|" not in converted, "the divider row leaked into the output"


def test_a_ragged_table_is_padded_rather_than_refused(conf):
    """A row with fewer cells than the header is a typo, not a reason to fail a
    docs build. reST needs the columns to line up, so the gap is filled."""
    converted = conf._bridge("| a | b | c |\n|---|---|---|\n| 1 | 2 |\n")

    rows = [line for line in converted.split("\n") if line.strip().startswith(("* -", "- "))]
    assert converted.count("* - ") == 2, converted


def test_a_stray_pipe_is_not_mistaken_for_a_table(conf):
    """`a | b` is how this codebase writes a shell pipe, and there are hundreds."""
    original = "Run `discover . | link | findings` to see it.\n"
    converted = conf._bridge(original)

    assert ".. list-table::" not in converted, "a shell pipe was read as a table"
    assert converted.strip() == original.strip()


def test_an_existing_rest_table_is_left_alone(conf):
    """36 docstrings already carry reST simple tables. Touching them would break
    what was already correct."""
    original = (
        "============  ==========================================\n"
        "`Tier.WORK`   per-user working data.\n"
        "`Tier.SHARED` corpora many users read.\n"
        "============  ==========================================\n"
    )

    assert conf._bridge(original).strip() == original.strip()


def test_converting_twice_changes_nothing_the_second_time(conf):
    """Sphinx may hand a docstring through more than one processing pass."""
    once = conf._bridge("| a | b |\n|---|---|\n| 1 | 2 |\n\n```sh\nls\n```\n")

    assert conf._bridge(once) == once


# --- the constructs actually in use -----------------------------------------


def _docstrings():
    for package in PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:                      # pragma: no cover - none today
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    text = ast.get_docstring(node)
                    if text:
                        yield path.relative_to(ROOT), text


def test_the_walk_finds_docstrings_at_all():
    """A guard on the two tests below. Asserting 'no unhandled Markdown' over an
    empty set passes without checking anything — the vacuous-pass defect
    docs/AUDIT.md §1.1 was written about."""
    found = list(_docstrings())

    assert len(found) > 500, f"only {len(found)} docstrings found; did a package move?"


def test_no_docstring_uses_markdown_the_bridge_cannot_convert():
    """Markdown headings and links have no rule, because nothing uses them.

    If that changes, this fails and the choice is explicit: add a rule to the
    bridge, or write the docstring in reST. What must not happen is a page
    quietly rendering `## Context` as body text.
    """
    heading = re.compile(r"^[ \t]*#{2,}[ \t]+\S", re.M)
    link = re.compile(r"\[[^\]\n]+\]\([^)\n]+\)")
    offenders: list[str] = []

    for path, text in _docstrings():
        if heading.search(text):
            offenders.append(f"{path}: a Markdown heading")
        if link.search(text):
            offenders.append(f"{path}: a Markdown link")

    assert not offenders, (
        "these docstrings use Markdown the Sphinx bridge has no rule for:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_every_fence_in_the_repository_survives_the_bridge(conf):
    """Run the real docstrings through it, not a fixture that agrees with it."""
    checked = 0
    for path, text in _docstrings():
        if "```" not in text:
            continue
        checked += 1
        converted = conf._bridge(text)
        assert "```" not in converted, f"{path}: a fence was left unconverted"
        assert ".. code-block::" in converted, f"{path}: the fence produced no block"

    assert checked >= 15, f"only {checked} fenced docstrings found; did they move?"


# --- the hand-written pages -------------------------------------------------


def test_every_module_the_index_points_at_exists():
    """The two hand-written pages name modules. A stale one fails the docs build
    under `-W`, so it is worth catching in three seconds here rather than in a
    twenty-minute Sphinx run — and one of them was already stale when this was
    written (`slpie.graph.sqlite`, a rename the plan proposed and nobody made).
    """
    import importlib

    referenced: set[str] = set()
    for page in ("index.md", "reference/index.md"):
        body = (ROOT / "docs" / page).read_text(encoding="utf-8")
        referenced |= set(re.findall(r"\{py:mod\}`([^`]+)`", body))

    assert len(referenced) > 15, "the index stopped pointing at modules"

    missing = []
    for name in sorted(referenced):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    assert not missing, f"docs/ points at modules that do not exist: {missing}"


def test_the_reference_page_reaches_every_package():
    """The recursive autosummary root. Dropping a package here would silently
    document three-quarters of the repository and look complete."""
    body = (ROOT / "docs" / "reference" / "index.md").read_text(encoding="utf-8")
    root = body.split(".. autosummary::")[-1]

    for package in PACKAGES:
        assert re.search(rf"^\s+{re.escape(package)}\s*$", root, re.M), (
            f"{package} is not in the recursive autosummary root"
        )
