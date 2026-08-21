"""Python source analysis, using the real `ast` module.

Not regular expressions. An import inside a docstring, a string containing the
word `import`, a conditional import under `TYPE_CHECKING` — a regex gets all of
those wrong, and the errors are silent. The parser gets them right and gives
exact line numbers for free.

The distinction this file cares most about is **static versus dynamic**. A
plain `import requests` is a static import at 0.90. An
`importlib.import_module(name)` where `name` is a variable is a dynamic load at
0.40, and one where it is a literal is somewhere in between — a real import that
static analysis of the *call site* found rather than of the import graph. Those
are three different degrees of knowledge and they must not collapse into one.
"""

from __future__ import annotations

import ast
from typing import Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, imports, module_urn, result

EXTRACTOR = "slpie.python.ast"

#: Modules that ship with Python. An import of one of these is not a dependency
#: on anything installable, and reporting it as one would bury every real
#: third-party edge under a hundred imports of `os` and `typing`.
import sys

STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


def discover_python_source(source: Source) -> DiscoveryResult:
    """Parse one module and report what it imports."""
    try:
        tree = ast.parse(source.text, filename=source.uri)
    except SyntaxError as error:
        # Real repositories contain files that do not parse: templates, Python 2
        # leftovers, generated stubs. That is a fact to report, not a crash.
        return result((), errors=[
            f"{source.uri}: could not parse ({error.msg} at line {error.lineno})"
        ])

    subject = module_urn(source.element, _module_path(source)).to_string()
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.STATIC_IMPORT, extractor=EXTRACTOR,
                line=1, excerpt=source.excerpt(1), digest=source.digest,
            ),
            node_kind="module", language="python",
        )
    ]

    for node in ast.walk(tree):
        observations.extend(_from_node(source, subject, node))

    return result(observations)


def _from_node(source: Source, subject: str, node: ast.AST) -> Iterator[Observation]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield from _static(source, subject, alias.name, node.lineno)

    elif isinstance(node, ast.ImportFrom):
        # A relative import points inside this element, not at a package.
        if node.level:
            return
        if node.module:
            yield from _static(source, subject, node.module, node.lineno)

    elif isinstance(node, ast.Call):
        yield from _dynamic(source, subject, node)


def _static(source: Source, subject: str, dotted: str, line: int) -> Iterator[Observation]:
    root = dotted.split(".")[0]
    if not root or root in STDLIB:
        return
    yield imports(
        subject,
        Purl.create("pypi", root).to_string(),
        evidence_at(
            source.uri, kind=EvidenceKind.STATIC_IMPORT, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        module=dotted, language="python",
    )


def _dynamic(source: Source, subject: str, node: ast.Call) -> Iterator[Observation]:
    """`importlib.import_module(...)` and `__import__(...)`.

    A literal argument is a real name the platform can act on, found by
    inspecting a call rather than an import statement — reported as
    `dynamic_load` because that is what it is, and because certainty about it
    would be unearned. A computed argument yields no target at all: naming a
    package we cannot identify would be a guess with a file:line attached.
    """
    name = _called_name(node)
    if name not in ("importlib.import_module", "__import__", "import_module"):
        return
    if not node.args:
        return

    argument = node.args[0]
    line = getattr(node, "lineno", 0)
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        root = argument.value.split(".")[0]
        if root and root not in STDLIB:
            yield imports(
                subject,
                Purl.create("pypi", root).to_string(),
                evidence_at(
                    source.uri, kind=EvidenceKind.DYNAMIC_LOAD, extractor=EXTRACTOR,
                    line=line, excerpt=source.excerpt(line), digest=source.digest,
                ),
                module=argument.value, language="python", dynamic=True,
            )
        return

    # A computed name. The *fact of dynamic loading* is worth recording, because
    # it tells the platform its import graph is incomplete here — but there is
    # no target to name.
    yield declares(
        subject,
        evidence_at(
            source.uri, kind=EvidenceKind.DYNAMIC_LOAD, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        node_kind="module", language="python",
        dynamic_import_site=True,
    )


def _called_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        parts: list[str] = [target.attr]
        current: ast.AST = target.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _module_path(source: Source) -> str:
    """The path within its element, so a module urn does not repeat the name."""
    path = source.uri
    for prefix in ("file://", "./"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    if source.element and f"/{source.element}/" in f"/{path}":
        _, _, tail = path.partition(f"{source.element}/")
        path = tail or path
    return path.removesuffix(".py")


DISCOVERERS = (
    (
        "slpie.python.ast", discover_python_source,
        ("*.py",), ("imports", "declares"),
        (EvidenceKind.STATIC_IMPORT, EvidenceKind.DYNAMIC_LOAD),
    ),
)
