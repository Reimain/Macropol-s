"""The architectural boundaries, asserted rather than documented.

SLPIE owns everything except code generation, which it delegates to Gratimos.
That boundary is only worth anything if it is checked: a convention held by
review erodes the first time somebody needs `gratimos.meta` in a hurry.
"""

from __future__ import annotations

import ast

import pytest

from _walk import SLPIE, bridges, crossings, imported_roots, modules

#: The single module permitted to import Gratimos — the codegen bridge that
#: turns graph shapes into architecture-as-code (plan §15). Read from
#: `slpie/audit/engine.py` rather than restated, so the boundary is written down
#: once: the judge customers run and the test we run cannot disagree about it.
CODEGEN_BRIDGE = bridges()["slpie"]


def slpie_modules():
    return modules(SLPIE)


def test_exactly_one_slpie_module_may_import_gratimos():
    importers = crossings(SLPIE, "gratimos")

    assert importers in ([], [CODEGEN_BRIDGE]), (
        f"only {CODEGEN_BRIDGE} may import Gratimos; found {importers}"
    )


def test_the_kernel_has_no_third_party_dependencies():
    """Including the UI. Anything importable must ship with Python itself."""
    import sys

    allowed = set(sys.stdlib_module_names) | {"slpie", "gratimos", "__future__"}
    offenders: dict[str, set[str]] = {}
    for module in slpie_modules():
        third_party = imported_roots(module) - allowed
        if third_party:
            offenders[module.relative_to(SLPIE).as_posix()] = third_party

    assert not offenders, f"third-party imports in the kernel: {offenders}"


def test_no_module_above_the_binding_layer_branches_on_the_target():
    """Simulated and live differ only in which connector is bound.

    A `if target == LIVE` anywhere else would mean the simulator stopped being
    evidence about the real code path, which is the whole reason it exists.
    """
    allowed_prefixes = ("binding/", "simulator/", "cli.py", "ui/")
    everything = slpie_modules()
    exempt = [
        module for module in everything
        if module.relative_to(SLPIE).as_posix().startswith(allowed_prefixes)
    ]
    # Without this the test survives a restructure by exempting nothing and
    # checking everything, or — worse, if `cli.py` becomes `cli/` — by silently
    # dropping a prefix nobody notices is no longer matching.
    assert len(exempt) < len(everything), (
        f"every module matched {allowed_prefixes}, so this test checked nothing"
    )
    assert exempt, (
        f"none of {allowed_prefixes} matched a module — the binding layer moved, "
        f"and this test is now asserting against the wrong set"
    )

    offenders = []
    for module in everything:
        relative = module.relative_to(SLPIE).as_posix()
        if relative.startswith(allowed_prefixes):
            continue
        source = module.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ("Target.LIVE" in stripped or "Target.SIMULATED" in stripped) and (
                stripped.startswith(("if ", "elif ")) or " if " in stripped
            ):
                offenders.append(f"{relative}: {stripped}")

    assert not offenders, f"target branching outside the binding layer: {offenders}"


@pytest.mark.parametrize("module", slpie_modules(), ids=lambda p: p.stem)
def test_every_module_parses_and_carries_a_docstring(module):
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    assert ast.get_docstring(tree), f"{module.name} has no module docstring"
