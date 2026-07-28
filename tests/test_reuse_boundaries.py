"""The mirror boundary: Gratimos reaches into SLPIE in exactly one file.

`tests/test_slpie_boundaries.py` asserts that exactly one SLPIE module imports
Gratimos — the codegen bridge. Reuse assessment needs the traffic to run the
other way too, for SPDX obligation analysis and version-range algebra, and an
unasserted second direction is how two packages quietly become one.

So the same rule is applied in reverse, with the same `ast` walk: one named
file, `gratimos/reuse/bridge.py`, and nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

GRATIMOS = Path(__file__).resolve().parent.parent / "gratimos"

#: The single Gratimos module permitted to import SLPIE.
LICENCE_BRIDGE = "reuse/bridge.py"


def gratimos_modules():
    return sorted(GRATIMOS.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_exactly_one_gratimos_module_may_import_slpie():
    importers = [
        module.relative_to(GRATIMOS).as_posix()
        for module in gratimos_modules()
        if "slpie" in imported_roots(module)
    ]
    assert importers in ([], [LICENCE_BRIDGE]), (
        f"only {LICENCE_BRIDGE} may import SLPIE; found {importers}"
    )


def test_the_crawler_has_no_third_party_dependencies():
    """The crawler is stdlib-only, exactly as the SLPIE kernel is.

    A crawler is the obvious place to reach for `requests`, and this is the
    assertion that stops it: politeness, retries and conditional requests are
    implemented here on `urllib` precisely so the package installs and runs with
    nothing but Python.
    """
    import sys

    allowed = set(sys.stdlib_module_names) | {"gratimos", "slpie", "__future__"}
    offenders: dict[str, set[str]] = {}
    for module in GRATIMOS.rglob("crawl/**/*.py"):
        third_party = imported_roots(module) - allowed
        if third_party:
            offenders[module.relative_to(GRATIMOS).as_posix()] = third_party
    assert not offenders, f"the crawler must stay stdlib-only; found {offenders}"


def test_the_crawler_never_imports_the_reuse_layer():
    """Direction: reuse depends on crawl, never the reverse.

    Both directions would make "what does a crawl produce?" and "what does an
    assessment consume?" the same question, and the pair could then only be
    understood together.
    """
    offenders = [
        module.relative_to(GRATIMOS).as_posix()
        for module in GRATIMOS.rglob("crawl/**/*.py")
        if any(
            isinstance(node, ast.ImportFrom) and node.module and "reuse" in node.module
            for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        )
    ]
    assert not offenders, f"crawl must not depend on reuse; found {offenders}"


@pytest.mark.parametrize(
    "module",
    [m for m in GRATIMOS.rglob("crawl/**/*.py")] + [m for m in GRATIMOS.rglob("reuse/**/*.py")],
    ids=lambda path: path.stem,
)
def test_every_new_module_parses_and_carries_a_docstring(module):
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    assert ast.get_docstring(tree), f"{module.name} has no module docstring"
