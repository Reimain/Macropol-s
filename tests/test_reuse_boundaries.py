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

import pytest

from _walk import GRATIMOS, bridges, crossings, imported_roots, modules

#: The single Gratimos module permitted to import SLPIE. Read from
#: `slpie/audit/engine.py`, the same declaration the judge runs from.
LICENCE_BRIDGE = bridges()["gratimos"]

#: The two subpackages this file asserts about. Named once, so the globs below
#: and the docstring parametrisation at the foot cannot drift apart.
CRAWL = "crawl/**/*.py"
REUSE = "reuse/**/*.py"


def gratimos_modules():
    return modules(GRATIMOS)


def test_exactly_one_gratimos_module_may_import_slpie():
    importers = crossings(GRATIMOS, "slpie")

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
    for module in modules(GRATIMOS, CRAWL):
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
        for module in modules(GRATIMOS, CRAWL)
        if any(
            isinstance(node, ast.ImportFrom) and node.module and "reuse" in node.module
            for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        )
    ]
    assert not offenders, f"crawl must not depend on reuse; found {offenders}"


def test_importing_gratimos_does_not_drag_in_the_crawler():
    """The reuse path is lazy, so `import gratimos` stays cheap.

    Asserted in a subprocess because by the time this test file is collected the
    modules are already imported by other tests, and an in-process check would
    pass for the wrong reason.
    """
    import subprocess
    import sys

    probe = (
        "import gratimos, sys;"
        "assert 'gratimos.crawl' not in sys.modules;"
        "assert 'slpie.domain.license' not in sys.modules;"
        "gratimos.ReuseAssessor;"
        "assert 'gratimos.crawl' in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(GRATIMOS.parent),
    )
    assert result.returncode == 0, result.stderr


def test_an_unknown_attribute_still_raises_attribute_error():
    import gratimos

    with pytest.raises(AttributeError, match="no attribute 'nonsense'"):
        gratimos.nonsense


@pytest.mark.parametrize(
    "module",
    modules(GRATIMOS, CRAWL) + modules(GRATIMOS, REUSE),
    ids=lambda path: path.stem,
)
def test_every_new_module_parses_and_carries_a_docstring(module):
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    assert ast.get_docstring(tree), f"{module.name} has no module docstring"
