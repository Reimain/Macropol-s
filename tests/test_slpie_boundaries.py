"""The architectural boundaries, asserted rather than documented.

SLPIE owns everything except code generation, which it delegates to Gratimos.
That boundary is only worth anything if it is checked: a convention held by
review erodes the first time somebody needs `gratimos.meta` in a hurry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SLPIE = Path(__file__).resolve().parent.parent / "slpie"

#: The single module permitted to import Gratimos — the codegen bridge that
#: turns graph shapes into architecture-as-code (plan §15).
CODEGEN_BRIDGE = "artifacts/codegen.py"


def slpie_modules():
    return sorted(SLPIE.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module root outside the package.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_exactly_one_slpie_module_may_import_gratimos():
    importers = [
        module.relative_to(SLPIE).as_posix()
        for module in slpie_modules()
        if "gratimos" in imported_roots(module)
    ]
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
    offenders = []
    for module in slpie_modules():
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
