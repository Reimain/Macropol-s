"""AST-level merge: regeneration that does not eat your edits.

Regenerating a module is a three-way merge, not a write. The three sides are:

* **base** — the source the generator emitted last time (recorded, not guessed);
* **ours** — what is on disk now, including whatever a human changed;
* **theirs** — what the generator wants to emit from the current shape.

Comparing at the AST level rather than by text means reformatting, comment
rewrites, and import reordering are not mistaken for edits. A symbol is
"changed" only when its parsed structure changed.

The decision table per symbol:

===============  ===============  =========================================
ours vs base     theirs vs base   outcome
===============  ===============  =========================================
unchanged        changed          take generated
changed          unchanged        keep local edit
changed          changed (same)   take either — they agree
changed          changed (diff)   **conflict**
absent           added            take generated
present          removed          drop, unless locally edited
===============  ===============  =========================================

Anything marked ``# gratimos:keep`` short-circuits the table and stays.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import CodegenError, MergeConflict
from .emitter import HEADER_MARKER, KEEP_MARKER


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    CONSTANT = "constant"
    OTHER = "other"


class Resolution(str, Enum):
    """How one symbol was settled."""

    ADDED = "added"              # generator introduced it
    TAKE_GENERATED = "generated" # generator owns it; no local edits to lose
    KEEP_LOCAL = "local"         # local edit preserved, generator did not move
    KEEP_MARKED = "marked"       # explicitly pinned with the keep marker
    HAND_WRITTEN = "hand"        # exists only on disk; generator never made it
    UNCHANGED = "unchanged"      # both sides agree
    REMOVED = "removed"          # generator dropped it and nobody edited it
    CONFLICT = "conflict"        # both sides moved, differently


class ConflictPolicy(str, Enum):
    """What to do when the table lands on CONFLICT."""

    RAISE = "raise"              # stop; the orchestrator decides (default)
    MARK = "mark"                # emit markers — produces a file that will not import
    PREFER_LOCAL = "local"
    PREFER_GENERATED = "generated"


@dataclass(frozen=True, slots=True)
class Symbol:
    """One top-level definition, with the text and structure behind it."""

    name: str
    kind: SymbolKind
    source: str
    fingerprint: str
    keep: bool = False
    lineno: int = 0

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Symbol {self.kind.value} {self.name}{' keep' if self.keep else ''}>"


@dataclass(slots=True)
class ModuleParts:
    """A module decomposed into the pieces the merge reasons about."""

    header: str = ""
    docstring: str = ""
    imports: list[str] = field(default_factory=list)
    symbols: dict[str, Symbol] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def fingerprints(self) -> dict[str, str]:
        return {name: symbol.fingerprint for name, symbol in self.symbols.items()}


@dataclass(frozen=True, slots=True)
class Decision:
    """Why one symbol ended up the way it did."""

    name: str
    resolution: Resolution
    reason: str = ""

    @property
    def conflicted(self) -> bool:
        return self.resolution is Resolution.CONFLICT

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "resolution": self.resolution.value, "reason": self.reason}

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.resolution.value:<10} {self.name}" + (f"  ({self.reason})" if self.reason else "")


@dataclass(slots=True)
class MergeResult:
    """The merged source and a full account of how it was reached."""

    source: str
    decisions: list[Decision] = field(default_factory=list)
    conflicts: list[Decision] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts

    @property
    def changed(self) -> bool:
        return any(
            d.resolution in (Resolution.ADDED, Resolution.TAKE_GENERATED, Resolution.REMOVED)
            for d in self.decisions
        )

    def by_resolution(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for decision in self.decisions:
            out.setdefault(decision.resolution.value, []).append(decision.name)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "changed": self.changed,
            "lines": self.source.count("\n") + 1,
            "decisions": [d.to_dict() for d in self.decisions],
            "conflicts": [d.to_dict() for d in self.conflicts],
            "summary": self.by_resolution(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "clean" if self.clean else f"{len(self.conflicts)} conflicts"
        return f"<MergeResult {state} decisions={len(self.decisions)}>"


# --- parsing -------------------------------------------------------------


def parse_module(source: str, *, label: str = "module") -> ModuleParts:
    """Decompose a module into header, imports, and top-level symbols."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CodegenError(f"cannot parse {label}: {exc}") from exc

    lines = source.splitlines()
    parts = ModuleParts()
    parts.header = "\n".join(
        line for line in lines[:1] if line.strip().startswith(HEADER_MARKER)
    )

    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        parts.docstring = _segment(lines, body[0])
        body = body[1:]

    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            parts.imports.append(_segment(lines, node).strip())
            continue
        name, kind = _identify(node)
        if name is None:
            # Unnamed top-level statements (a bare call, an `if` block) are
            # kept verbatim under a positional key so they survive the merge.
            name = f"__stmt_{node.lineno}"
            kind = SymbolKind.OTHER
        segment, keep = _segment_with_comments(lines, node)
        parts.symbols[name] = Symbol(
            name=name,
            kind=kind,
            source=segment,
            fingerprint=ast.dump(node, include_attributes=False),
            keep=keep,
            lineno=node.lineno,
        )
        parts.order.append(name)
    return parts


def _identify(node: ast.stmt) -> tuple[str | None, SymbolKind]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, SymbolKind.FUNCTION
    if isinstance(node, ast.ClassDef):
        return node.name, SymbolKind.CLASS
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, SymbolKind.CONSTANT
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, SymbolKind.CONSTANT
    return None, SymbolKind.OTHER


def _start_line(node: ast.stmt) -> int:
    """First line of a definition, counting decorators."""
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)]) if decorators else node.lineno


def _segment(lines: Sequence[str], node: ast.stmt) -> str:
    start = _start_line(node) - 1
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(lines[start:end])


def _segment_with_comments(lines: Sequence[str], node: ast.stmt) -> tuple[str, bool]:
    """Include the comment block above a definition — that is where markers live."""
    start = _start_line(node) - 1
    first = start
    while first > 0:
        candidate = lines[first - 1].strip()
        if candidate.startswith("#"):
            first -= 1
        else:
            break
    end = getattr(node, "end_lineno", node.lineno)
    segment = "\n".join(lines[first:end])
    keep = any(KEEP_MARKER in line for line in lines[first:start + 1])
    return segment, keep


# --- merging -------------------------------------------------------------


def merge_sources(
    ours: str,
    theirs: str,
    base: str | None = None,
    *,
    policy: ConflictPolicy = ConflictPolicy.RAISE,
    label: str = "module",
) -> MergeResult:
    """Three-way merge two versions of a generated module.

    ``base`` is the previously generated source. Without it the merge degrades
    to two-way: it can still tell added from removed, but it cannot distinguish
    "you edited this" from "the generator changed this", so overlapping symbols
    become conflicts rather than silent overwrites.
    """
    our_parts = parse_module(ours, label=f"{label} (local)")
    their_parts = parse_module(theirs, label=f"{label} (generated)")
    base_parts = parse_module(base, label=f"{label} (base)") if base else None
    base_fp = base_parts.fingerprints() if base_parts else {}

    decisions: list[Decision] = []
    chosen: dict[str, str] = {}

    for name in their_parts.order:
        theirs_sym = their_parts.symbols[name]
        ours_sym = our_parts.symbols.get(name)
        based = base_fp.get(name)

        if ours_sym is None:
            chosen[name] = theirs_sym.source
            decisions.append(Decision(name, Resolution.ADDED, "new in generated output"))
            continue
        if ours_sym.keep:
            chosen[name] = ours_sym.source
            decisions.append(Decision(name, Resolution.KEEP_MARKED, f"pinned with {KEEP_MARKER}"))
            continue
        if ours_sym.fingerprint == theirs_sym.fingerprint:
            chosen[name] = theirs_sym.source
            decisions.append(Decision(name, Resolution.UNCHANGED, "both sides agree"))
            continue

        locally_edited = based is not None and ours_sym.fingerprint != based
        generator_moved = based is None or theirs_sym.fingerprint != based

        if based is not None and not locally_edited:
            chosen[name] = theirs_sym.source
            decisions.append(Decision(name, Resolution.TAKE_GENERATED, "no local edits to preserve"))
        elif based is not None and not generator_moved:
            chosen[name] = ours_sym.source
            decisions.append(Decision(name, Resolution.KEEP_LOCAL, "locally edited; generator unchanged"))
        else:
            reason = (
                "both the generator and this file changed this symbol"
                if based is not None else
                "no recorded base: cannot tell a local edit from a generator change"
            )
            decision = Decision(name, Resolution.CONFLICT, reason)
            decisions.append(decision)
            chosen[name] = _resolve(name, ours_sym, theirs_sym, policy, reason)

    for name in our_parts.order:
        if name in chosen:
            continue
        ours_sym = our_parts.symbols[name]
        if ours_sym.keep:
            chosen[name] = ours_sym.source
            decisions.append(Decision(name, Resolution.KEEP_MARKED, f"pinned with {KEEP_MARKER}"))
        elif name in base_fp:
            if ours_sym.fingerprint == base_fp[name]:
                decisions.append(Decision(name, Resolution.REMOVED, "generator dropped it; no local edits"))
            else:
                chosen[name] = ours_sym.source
                decisions.append(Decision(name, Resolution.KEEP_LOCAL, "generator dropped it, but it was edited here"))
        else:
            chosen[name] = ours_sym.source
            decisions.append(Decision(name, Resolution.HAND_WRITTEN, "not generator-owned"))

    conflicts = [d for d in decisions if d.conflicted]
    if conflicts and policy is ConflictPolicy.RAISE:
        first = conflicts[0]
        raise MergeConflict(first.name, f"{first.reason} ({len(conflicts)} conflicted symbol(s))")

    source = _assemble(our_parts, their_parts, chosen, decisions)
    return MergeResult(source=source, decisions=decisions, conflicts=conflicts)


def _resolve(
    name: str, ours: Symbol, theirs: Symbol, policy: ConflictPolicy, reason: str
) -> str:
    if policy is ConflictPolicy.PREFER_LOCAL:
        return ours.source
    if policy is ConflictPolicy.PREFER_GENERATED:
        return theirs.source
    # MARK: emit both sides with bare git-style markers.
    #
    # The markers are deliberately *not* commented out. Commented markers would
    # leave a file that still parses, where Python silently takes whichever
    # definition comes last — a conflict that imports is a conflict that ships.
    # Bare markers guarantee a SyntaxError until a human resolves them.
    return (
        f"<<<<<<< generated (gratimos): {reason}\n"
        f"{theirs.source}\n"
        f"=======\n"
        f"{ours.source}\n"
        f">>>>>>> local\n"
    )


def _assemble(
    ours: ModuleParts,
    theirs: ModuleParts,
    chosen: Mapping[str, str],
    decisions: Sequence[Decision],
) -> str:
    """Rebuild a module: generated header, union of imports, chosen symbols."""
    blocks: list[str] = []
    docstring = theirs.docstring or ours.docstring
    # Header marker and module docstring are one unit — the marker annotates
    # the docstring that follows it, so they must not be blank-line separated.
    preamble = "\n".join(part for part in (theirs.header, docstring) if part)
    if preamble:
        blocks.append(preamble)

    imports = _merge_imports(ours.imports, theirs.imports)
    if imports:
        blocks.append("\n".join(imports))

    generated_order = [n for n in theirs.order if n in chosen]
    local_only = [n for n in ours.order if n in chosen and n not in theirs.order]

    for name in generated_order:
        blocks.append(chosen[name])
    if local_only:
        blocks.append(
            "# --- not generated: defined in this file and preserved across "
            "regeneration ---"
        )
        for name in local_only:
            blocks.append(chosen[name])

    return "\n\n\n".join(block.rstrip() for block in blocks if block.strip()) + "\n"


def _merge_imports(ours: Iterable[str], theirs: Iterable[str]) -> list[str]:
    """Union of import lines, `__future__` first, deduplicated, sorted."""
    seen: dict[str, None] = {}
    for line in list(theirs) + list(ours):
        seen.setdefault(line.strip(), None)
    lines = list(seen)
    future = [line for line in lines if line.startswith("from __future__")]
    rest = sorted(line for line in lines if not line.startswith("from __future__"))
    plain = [line for line in rest if line.startswith("import ")]
    froms = [line for line in rest if not line.startswith("import ")]
    out = future + ([""] if future and (plain or froms) else []) + plain
    if froms:
        out += ([""] if plain else []) + froms
    return out


def diff_symbols(before: str, after: str) -> dict[str, list[str]]:
    """Structural symbol-level diff between two module sources."""
    left = parse_module(before, label="before")
    right = parse_module(after, label="after")
    left_fp, right_fp = left.fingerprints(), right.fingerprints()
    return {
        "added": sorted(set(right_fp) - set(left_fp)),
        "removed": sorted(set(left_fp) - set(right_fp)),
        "changed": sorted(n for n in set(left_fp) & set(right_fp) if left_fp[n] != right_fp[n]),
        "unchanged": sorted(n for n in set(left_fp) & set(right_fp) if left_fp[n] == right_fp[n]),
    }
