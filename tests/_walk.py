"""Walking the source tree, once, for every test that needs to.

Three tests used to walk `slpie/` and `gratimos/` with `ast` to assert the
architectural boundaries, and each carried its own copy of the walk —
`test_slpie_boundaries.py`, `test_reuse_boundaries.py` and, subtly differently,
`test_slpie_audit.py`. Three copies of one algorithm means a fix to import
resolution lands in one of them and is silently absent from the others, and
`test_slpie_audit.py` proves relative-import handling is exactly the kind of
thing that needs fixing.

Two things live here, and the second is the load-bearing one.

**`imported_roots`** — the walk itself.

**`modules(...)`** — a glob that refuses to match nothing. This exists because
of a measured defect: `GRATIMOS.rglob("crawl/**/*.py")` matched 12 files, and
renaming `gratimos/crawl` to anything made it match 0 — at which point
``assert not offenders`` over an empty set passed, and three boundary tests went
green having checked nothing at all. A restructure would have reported itself as
safe. Every walk in the suite goes through here so that a moved package fails
loudly instead of quietly.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: The repository root. `tests/` is a direct child of it, and this is the one
#: place that assumption is written down.
REPOSITORY = Path(__file__).resolve().parent.parent

SLPIE = REPOSITORY / "slpie"
GRATIMOS = REPOSITORY / "gratimos"


def bridges() -> dict[str, str]:
    """The modules permitted to cross a ring boundary, read from the product.

    Not restated here. `slpie/audit/engine.py` declares them as check
    configuration because the judge is what customers run against their own
    trees; the tests read the same declaration, so the boundary is written down
    once and a move updates one place rather than three.

    Returns `{"slpie": "artifacts/codegen.py", "gratimos": "reuse/bridge.py"}` —
    ring name to the path, relative to that ring, of its single bridge.
    """
    from slpie.audit.engine import slpie_checks

    found: dict[str, str] = {}
    for check in slpie_checks():
        if check.rule != "single-import":
            continue
        ring = str(check.options["ring"])
        allowed = str(check.options["allowed"])
        # "slpie.artifacts.codegen" -> "artifacts/codegen.py"
        relative = allowed.split(".", 1)[1].replace(".", "/")
        found[ring] = f"{relative}.py"
    return found


def modules(root: Path, pattern: str = "*.py") -> list[Path]:
    """Every module under `root` matching `pattern`, and never an empty list.

    The guard is the point. A glob that stops matching after a rename is worse
    than one that raises, because the assertions downstream of it all pass.
    """
    found = sorted(root.rglob(pattern))
    assert found, (
        f"{root.name}/{pattern} matched no files — did the package move? "
        f"A boundary test over an empty set passes without checking anything, "
        f"so this refuses rather than reporting a rename as safe."
    )
    return found


def imported_roots(path: Path) -> set[str]:
    """The top-level packages `path` imports.

    Relative imports are excluded deliberately: `from .foo import bar` has no
    root outside its own package, and counting it would make every module look
    like it imports itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def crossings(ring: Path, target: str) -> list[str]:
    """Modules under `ring` that import `target`, relative to the ring.

    The shape both boundary tests need: a sorted list of paths, ready to compare
    against the one bridge that is allowed to appear in it.
    """
    return [
        module.relative_to(ring).as_posix()
        for module in modules(ring)
        if target in imported_roots(module)
    ]
