"""The notebooks are a projection, and this is what keeps them one.

Executing every notebook is CI's job (`make notebooks-run`) — it needs a kernel
and about a minute, which is too much for the default suite. What is asserted
here is cheaper and catches the failures that matter most:

* the committed `.ipynb` files match the spec, so an edit made in the JSON is
  caught rather than silently overwritten on the next rebuild;
* the build is deterministic, so a rebuild produces no diff;
* every code cell parses, so a syntax error is caught in a second rather than in
  a kernel a minute later.

The distinction worth keeping: this file proves the notebooks are *well formed*.
Only running them proves they are *right*, and nothing here pretends otherwise.
"""

from __future__ import annotations

import ast
import json

import pytest

from _walk import REPOSITORY

pytest.importorskip("nbformat", reason="the notebook layer is an optional extra")

NOTEBOOKS = REPOSITORY / "notebooks"


def specs():
    from tools.notebooks.spec import NOTEBOOKS as SPECS

    return SPECS


def test_every_specified_notebook_is_committed():
    for notebook in specs():
        path = NOTEBOOKS / notebook.filename
        assert path.exists(), (
            f"{notebook.filename} is in the spec but not on disk — run `make notebooks`"
        )


def test_no_notebook_exists_without_a_spec():
    """A hand-written notebook would be the drift this whole design prevents."""
    generated = {notebook.filename for notebook in specs()}
    found = {path.name for path in NOTEBOOKS.glob("*.ipynb")}

    assert found - generated == set(), (
        "these notebooks have no spec and would vanish on the next rebuild"
    )


def test_the_committed_notebooks_match_their_spec():
    """The check CI runs. An edit in the JSON is caught, not overwritten."""
    from tools.notebooks.build import serialise

    stale = [
        notebook.filename
        for notebook in specs()
        if (NOTEBOOKS / notebook.filename).read_text(encoding="utf-8")
        != serialise(notebook)
    ]
    assert not stale, f"stale, run `make notebooks`: {stale}"


def test_building_twice_produces_identical_bytes():
    """Deterministic, so a rebuild is not a diff.

    Cell ids are derived from the notebook and the cell's position rather than
    randomly, which is the only reason this holds — `nbformat`'s own default
    would put a fresh uuid on every cell on every build.
    """
    from tools.notebooks.build import serialise

    for notebook in specs():
        assert serialise(notebook) == serialise(notebook)


@pytest.mark.parametrize(
    "notebook", specs(), ids=lambda notebook: notebook.slug,
)
def test_every_code_cell_parses(notebook):
    """A syntax error caught in a second rather than in a kernel a minute later."""
    for position, cell in enumerate(notebook.cells):
        if cell.kind != "code":
            continue
        try:
            ast.parse(cell.source)
        except SyntaxError as error:
            raise AssertionError(
                f"{notebook.filename} cell {position}: {error.msg} "
                f"at line {error.lineno}\n\n{cell.source[:300]}"
            ) from error


@pytest.mark.parametrize(
    "notebook", specs(), ids=lambda notebook: notebook.slug,
)
def test_every_notebook_is_valid_and_carries_no_outputs(notebook):
    import nbformat

    body = json.loads((NOTEBOOKS / notebook.filename).read_text(encoding="utf-8"))
    nbformat.validate(nbformat.from_dict(body))

    for cell in body["cells"]:
        if cell["cell_type"] != "code":
            continue
        # Committed outputs embed one run's temp paths and timings, which puts
        # noise in the diff of every rebuild.
        assert cell["outputs"] == [], f"{notebook.filename} has a committed output"
        assert cell["execution_count"] is None


@pytest.mark.parametrize(
    "notebook", specs(), ids=lambda notebook: notebook.slug,
)
def test_every_notebook_installs_the_package_before_using_it(notebook):
    """The first code cell stands on its own, so Colab works with no setup."""
    first = next(cell for cell in notebook.cells if cell.kind == "code")

    assert "_ensure_installed" in first.source, (
        f"{notebook.filename} uses the package before making sure it is installed"
    )


def test_the_notebooks_cover_both_packages():
    """A reader should not have to guess that half the repository exists."""
    slugs = {notebook.slug for notebook in specs()}

    assert any(slug.startswith("gratimos") for slug in slugs)
    assert len(slugs) >= 10, "the set has shrunk; was a notebook dropped by mistake?"


def test_the_index_lists_every_notebook():
    """The README table is generated, and this is what makes that worth doing.

    It was hand-written until it listed fourteen of sixteen pages: the two most
    recent were simply never added. Nobody noticed, because a table that is
    merely incomplete still looks like a table. Asserting the generated block is
    current is the same discipline `--check` applies to the notebooks themselves.
    """
    from tools.notebooks.build import INDEX, INDEX_END, INDEX_START, index

    body = INDEX.read_text(encoding="utf-8")
    assert INDEX_START in body and INDEX_END in body, (
        "the generated-index markers are gone from notebooks/README.md, so the "
        "table has quietly become hand-maintained again"
    )

    generated = index()
    assert generated in body, "notebooks/README.md is stale — run `make notebooks`"
    for notebook in specs():
        assert notebook.filename in generated, (
            f"{notebook.filename} is missing from the index"
        )


def test_the_stated_page_count_matches_the_set():
    """Three documents quote the count in prose; none of them may be wrong."""
    from tools.notebooks.build import ROOT

    total = len(specs())
    for relative in ("README.md", "docs/VALUE.md"):
        body = (ROOT / relative).read_text(encoding="utf-8").lower()
        spelled = _WORDS[total].lower()
        assert spelled in body or str(total) in body, (
            f"{relative} does not state that there are {total} notebooks"
        )


#: Only the counts this repository has plausibly reached. A missing key is a
#: louder failure than a silently-skipped assertion, which is the point.
_WORDS = {
    14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen",
    18: "Eighteen", 19: "Nineteen", 20: "Twenty",
}
