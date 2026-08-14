"""AST into a queryable graph — the projection every judgement queries.

This is graphify's shape taken literally: **the graph is the judge.** Not a linter
with opinions and not a model asked to review, but an AST projected into a
queryable structure over which deterministic queries return verdicts.

Modules become nodes, imports become edges, and each carries evidence at a real
file and line — so the audit graph is inspectable by the same means as everything
else, and an audit finding composes with the rest of the platform rather than
sitting in a separate report.

Two properties are enforced here rather than hoped for:

**Determinism.** Modules are walked in sorted order, edges are sorted, and nothing
depends on `os.walk`'s ordering or on a dict's insertion sequence. A projection
that differed between machines for identical trees would make the whole digest
worthless, and filesystem order is exactly the sort of thing that differs.

**Honesty about what could not be read.** A file that fails to parse becomes a
module marked `parsed=False`, never an absent one. Omitting it would silently
shrink the tree the judge examined, and every rule would then report a clean pass
over a smaller codebase than the one that exists.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation

#: Directories never worth walking. Vendored and generated trees are not the
#: architecture under audit, and including them would drown every real finding.
SKIP = (
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".tox", "target", "vendor", ".mypy_cache", ".pytest_cache",
    "site-packages", ".ruff_cache", "htmlcov", ".eggs",
)

#: Calls that genuinely hide a dependency from a static reader.
#:
#: `getattr` was in this set and had to come out. It fires on ordinary attribute
#: access — sixty times across this repository — and a rule that flags idiomatic
#: Python as a blind spot is a rule people switch off, taking the two real
#: findings with it. Only these three resolve an *import* at runtime, which is the
#: thing the boundary rules cannot see past.
DYNAMIC_IMPORTS = frozenset({
    "__import__", "importlib.import_module", "importlib.__import__",
})


@dataclass(frozen=True, slots=True)
class Import:
    """One import edge, with the line it was written on."""

    module: str                 # the importing module, dotted
    target: str                 # what it imports, dotted
    line: int = 0
    names: tuple[str, ...] = ()
    relative: int = 0           # `from ..x import y` → 2
    inside_function: bool = False
    guarded: bool = False       # wrapped in try/except — a deliberate optional

    @property
    def top(self) -> str:
        return self.target.split(".", 1)[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module, "target": self.target, "line": self.line,
            "names": list(self.names), "relative": self.relative,
            "deferred": self.inside_function, "guarded": self.guarded,
        }

    def __str__(self) -> str:
        return f"{self.module}:{self.line} → {self.target}"


@dataclass(frozen=True, slots=True)
class Definition:
    """A top-level name a module exports."""

    module: str
    name: str
    kind: str                   # class · function · assignment
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module, "name": self.name,
            "kind": self.kind, "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class Module:
    """One source file, projected."""

    name: str                   # dotted, relative to the tree root
    path: str
    uri: str
    package: str = ""
    imports: tuple[Import, ...] = ()
    definitions: tuple[Definition, ...] = ()
    parsed: bool = True
    error: str = ""
    lines: int = 0
    dynamic: tuple[int, ...] = ()   # lines with importlib/__import__/getattr

    @property
    def ring(self) -> str:
        """Which ring this module belongs to. The top-level package name."""
        return self.name.split(".", 1)[0]

    def imports_of(self, prefix: str) -> tuple[Import, ...]:
        return tuple(
            item for item in self.imports
            if item.target == prefix or item.target.startswith(f"{prefix}.")
        )

    def evidence_at(self, line: int, excerpt: str = "") -> Evidence:
        return Evidence(
            kind=EvidenceKind.STATIC_IMPORT,
            location=SourceLocation(uri=self.uri, line=line),
            extractor="slpie.audit.project", extractor_version="1",
            excerpt=excerpt[:300],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "path": self.path, "ring": self.ring,
            "parsed": self.parsed, "error": self.error, "lines": self.lines,
            "imports": len(self.imports), "definitions": len(self.definitions),
            "dynamic": list(self.dynamic),
        }

    def __str__(self) -> str:
        return self.name if self.parsed else f"{self.name} (unparsed)"


class Projection:
    """The tree as a graph. Deterministic, and honest about what it could not read."""

    def __init__(self, root: Path | str, modules: Sequence[Module] = ()) -> None:
        self.root = Path(root)
        self._modules: dict[str, Module] = {
            module.name: module for module in modules
        }

    # -- access ----------------------------------------------------------

    @property
    def modules(self) -> tuple[Module, ...]:
        """Sorted, always. Filesystem order must never reach a verdict."""
        return tuple(self._modules[name] for name in sorted(self._modules))

    @property
    def parsed(self) -> tuple[Module, ...]:
        return tuple(item for item in self.modules if item.parsed)

    @property
    def unparsed(self) -> tuple[Module, ...]:
        """Present rather than absent, so no rule passes over a shrunken tree."""
        return tuple(item for item in self.modules if not item.parsed)

    def get(self, name: str) -> Module | None:
        return self._modules.get(name)

    def rings(self) -> tuple[str, ...]:
        return tuple(sorted({item.ring for item in self.modules}))

    def in_ring(self, ring: str) -> tuple[Module, ...]:
        return tuple(item for item in self.modules if item.ring == ring)

    # -- queries ---------------------------------------------------------

    def imports_into(self, prefix: str, *, ring: str = "") -> tuple[Import, ...]:
        """Every import of `prefix`, optionally only from one ring.

        This is the query the single-import invariants are expressed as, and the
        reason the projection exists: the rule is a question about the graph, not
        a bespoke walk written per rule.
        """
        found: list[Import] = []
        for module in self.modules:
            if ring and module.ring != ring:
                continue
            found.extend(module.imports_of(prefix))
        return tuple(sorted(found, key=lambda item: (item.module, item.line)))

    def importers_of(self, prefix: str, *, ring: str = "") -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.module for item in self.imports_into(prefix, ring=ring)
        ))

    def third_party(self, *, ring: str = "", stdlib: Iterable[str]) -> tuple[Import, ...]:
        """Imports that are neither stdlib nor local. Empty ring means the whole tree.

        `in_ring("")` matches nothing, so an empty ring once yielded no imports at
        all and every dependency rule reported a clean pass over what it thought
        was an empty tree — a silent pass being exactly the failure the judge is
        built to avoid.
        """
        known = set(stdlib)
        local = self.rings()
        found: list[Import] = []
        for module in (self.in_ring(ring) if ring else self.modules):
            for item in module.imports:
                if item.relative:
                    continue
                top = item.top
                if top in known or top in local:
                    continue
                found.append(item)
        return tuple(sorted(found, key=lambda item: (item.module, item.line)))

    def dynamic_sites(self) -> tuple[tuple[str, int], ...]:
        """Where the projection knows it cannot see. Drives INDETERMINATE."""
        return tuple(
            (module.name, line)
            for module in self.modules
            for line in module.dynamic
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "modules": len(self._modules),
            "parsed": len(self.parsed),
            "unparsed": [item.name for item in self.unparsed],
            "rings": list(self.rings()),
            "dynamic_sites": len(self.dynamic_sites()),
        }

    def __len__(self) -> int:
        return len(self._modules)

    def __iter__(self) -> Iterator[Module]:
        return iter(self.modules)


def project(root: Path | str, *, limit: int = 20_000) -> Projection:
    """Walk a tree and project it. Sorted throughout, so it is reproducible."""
    base = Path(root).resolve()
    modules: list[Module] = []

    for path in sorted(base.rglob("*.py")):
        if len(modules) >= limit:
            break
        if any(part in SKIP for part in path.parts):
            continue
        modules.append(_module(path, base))

    return Projection(base, modules)


def _module(path: Path, base: Path) -> Module:
    name = _dotted(path, base)
    uri = path.as_uri()

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return Module(
            name=name, path=str(path), uri=uri, parsed=False,
            error=f"could not read: {error}",
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        # Present, not absent. A rule passing over a tree it could not fully read
        # is a clean result about a smaller codebase than the one that exists.
        return Module(
            name=name, path=str(path), uri=uri, parsed=False,
            error=f"syntax error at line {error.lineno}: {error.msg}",
            lines=len(source.splitlines()),
        )

    imports: list[Import] = []
    definitions: list[Definition] = []
    dynamic: list[int] = []

    #: Which nodes sit inside a function, so a deferred import is distinguishable
    #: from a module-level one. They mean different things: a local import is often
    #: a deliberate cycle break, and treating it as a hard dependency mis-reports
    #: the architecture.
    deferred: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                deferred.add(id(inner))

    #: Imports inside a `try` are deliberate optionals — the author already handles
    #: their absence. Reporting those as undeclared dependencies would flag correct
    #: defensive code, which is how a useful rule becomes one people mute.
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for inner in ast.walk(node):
                guarded.add(id(inner))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(Import(
                    module=name, target=alias.name, line=node.lineno,
                    names=(alias.asname or alias.name,),
                    inside_function=id(node) in deferred,
                    guarded=id(node) in guarded,
                ))
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                target = _resolve_relative(name, target, node.level)
            imports.append(Import(
                module=name, target=target, line=node.lineno,
                names=tuple(alias.name for alias in node.names),
                relative=node.level or 0,
                inside_function=id(node) in deferred,
                guarded=id(node) in guarded,
            ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.append(Definition(
                module=name, name=node.name,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                line=node.lineno,
            ))
        elif isinstance(node, ast.Call):
            called = _call_name(node)
            if called in DYNAMIC_IMPORTS:
                dynamic.append(node.lineno)

    return Module(
        name=name, path=str(path), uri=uri,
        package=name.rsplit(".", 1)[0] if "." in name else "",
        imports=tuple(imports), definitions=tuple(definitions),
        lines=len(source.splitlines()), dynamic=tuple(sorted(set(dynamic))),
    )


def _dotted(path: Path, base: Path) -> str:
    relative = path.relative_to(base).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or base.name


def _resolve_relative(module: str, target: str, level: int) -> str:
    """`from ..x import y` inside `a.b.c` → `a.x`.

    Resolved rather than left relative, because a rule asking "does anything import
    gratimos" cannot answer that against `..codegen` — and leaving it unresolved is
    how a relative import sneaks past an import boundary check.
    """
    parts = module.split(".")
    # A module's own package is one level up; each further dot climbs another.
    trimmed = parts[:max(0, len(parts) - level)]
    return ".".join([*trimmed, target]) if target else ".".join(trimmed)


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        parts: list[str] = [function.attr]
        current: Any = function.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
