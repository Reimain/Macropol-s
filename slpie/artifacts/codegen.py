"""The codegen bridge — the single point where SLPIE meets Gratimos.

Every other module in SLPIE is written against the graph and the domain types.
This one, and only this one, is allowed to import Gratimos, because Gratimos
owns code generation and SLPIE deliberately does not reimplement it. The
boundary is asserted in ``tests/test_slpie_boundaries.py`` rather than left to
review: one import, in one file, checked by walking the AST of every module.

**Why architecture-as-code needs a merge and not a template.**

An architecture view rendered as a diagram is read and thrown away. An
architecture view rendered as *Python* gets imported: a deployment script asserts
that ``PAYMENTS`` is in the application architecture, a test asserts that no
component depends on a `LEGACY`-classified one, a reviewer adds a docstring
explaining why the reporting service is deliberately outside the transactional
boundary. All of that is real work, it lives in the generated file, and it is
exactly what a templating generator destroys on its next run.

So regeneration here is a three-way merge at the AST level
(:func:`gratimos.codegen.merge_sources`), with the *previously generated* source
as the base. The consequences are the two behaviours this module exists for:

* An architect's annotation marked ``# gratimos:keep`` survives regeneration
  unconditionally — the merge short-circuits its whole decision table for it.
* When the generator and the file have both moved the same symbol, that is a
  genuine conflict and it **raises** (:class:`ArchitectureConflict`). Silently
  choosing a winner would mean the architecture drifted from the graph, or
  somebody's reasoning was deleted, with no record of either.

The mapping from graph to shape is deliberately "one field per element". A view
becomes a frozen dataclass whose attributes *are* its applications, data
entities or technology components, so a downstream module that names an element
which has left the architecture fails at import rather than at review — which is
the only version of architecture-as-code that is worth the file.

Nothing here invents a number. Confidence, evidence and findings pass through
from the graph untouched; the generator's only inputs are what the view already
derived.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from gratimos.codegen import KEEP_MARKER, ConflictPolicy, ModuleRegistry
from gratimos.errors import CodegenError, MergeConflict
from gratimos.meta import DataShape, FieldShape, TypeTag

from ..errors import ArtifactError

__all__ = [
    "KEEP_MARKER",
    "ArchitectureCodegen",
    "ArchitectureConflict",
    "ArchitectureView",
    "EmittedView",
    "MergePolicy",
    "shape_for",
]

#: Row keys that carry structure rather than being free attributes.
_STRUCTURAL_KEYS = frozenset({"id", "label", "kind"})


class MergePolicy(str, Enum):
    """What to do when the generator and the file have both moved a symbol.

    Mirrors :class:`gratimos.codegen.ConflictPolicy` so that no SLPIE caller —
    and no test — has to import Gratimos to choose one.
    """

    RAISE = "raise"
    MARK = "mark"
    PREFER_LOCAL = "local"
    PREFER_GENERATED = "generated"


_POLICIES: dict[MergePolicy, ConflictPolicy] = {
    MergePolicy.RAISE: ConflictPolicy.RAISE,
    MergePolicy.MARK: ConflictPolicy.MARK,
    MergePolicy.PREFER_LOCAL: ConflictPolicy.PREFER_LOCAL,
    MergePolicy.PREFER_GENERATED: ConflictPolicy.PREFER_GENERATED,
}


class ArchitectureConflict(ArtifactError):
    """Regeneration would have overwritten a hand edit.

    Carries the symbol so the operator is told *which* definition diverged. The
    generated file is left exactly as it was: a conflict stops the write rather
    than producing a half-merged module.
    """

    def __init__(self, view: str, symbol: str, detail: str = "") -> None:
        self.view = view
        self.symbol = symbol
        self.detail = detail
        super().__init__(
            f"{view}: regenerating would overwrite the local definition of {symbol!r}"
            + (f" — {detail}" if detail else "")
            + f"; mark it with `{KEEP_MARKER}` to pin it, or reconcile it by hand"
        )


@runtime_checkable
class ArchitectureView(Protocol):
    """What an enterprise view must be able to say about itself.

    Structural, not inherited: :mod:`slpie.enterprise` builds plain frozen
    dataclasses and this module never imports them, which keeps the dependency
    pointing one way.
    """

    @property
    def name(self) -> str:
        """Stable slug-able view name; becomes the module and file stem."""

    @property
    def doc(self) -> str:
        """One line describing what the view shows."""

    def rows(self) -> tuple[Mapping[str, Any], ...]:
        """The view's elements, already ordered. One row becomes one field."""

    def to_dict(self) -> dict[str, Any]:
        """The whole view as JSON-ready data."""

    def to_mermaid(self) -> str:
        """The whole view as a Mermaid diagram."""


@dataclass(frozen=True, slots=True)
class EmittedView:
    """What one emission produced, and how the merge got there."""

    name: str
    module_path: Path
    mermaid_path: Path
    json_path: Path
    revision: int
    shape_digest: str
    elements: int
    decisions: tuple[tuple[str, str], ...] = ()
    rewritten: bool = True

    @property
    def kept(self) -> tuple[str, ...]:
        """Symbols preserved because a human had pinned or edited them."""
        return tuple(
            name for name, resolution in self.decisions
            if resolution in ("marked", "local", "hand")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "module": str(self.module_path),
            "mermaid": str(self.mermaid_path),
            "json": str(self.json_path),
            "revision": self.revision,
            "shape_digest": self.shape_digest,
            "elements": self.elements,
            "rewritten": self.rewritten,
            "kept": list(self.kept),
            "decisions": [{"symbol": n, "resolution": r} for n, r in self.decisions],
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<EmittedView {self.name} r{self.revision} elements={self.elements}>"


def shape_for(view: ArchitectureView) -> DataShape:
    """Map a view onto a :class:`~gratimos.meta.DataShape`.

    One row becomes one field, so the shape's *digest* changes exactly when the
    set of architecture elements changes. That is what makes regeneration cheap:
    Gratimos short-circuits when the digest is unchanged, so re-emitting a view
    the graph has not moved does not rewrite the file at all.
    """
    fields: list[FieldShape] = []
    for row in view.rows():
        identity = str(row.get("id", "")).strip()
        if not identity:
            raise ArtifactError(f"{view.name}: a view row has no 'id'")
        attributes = tuple(
            f"{key}={row[key]}"
            for key in sorted(row)
            if key not in _STRUCTURAL_KEYS and row[key] not in ("", None, (), [])
        )
        fields.append(
            FieldShape(
                name=identity,
                tag=TypeTag.STRING,
                doc=str(row.get("label", identity)),
                format_hint=str(row.get("kind", "")),
                examples=attributes,
                observations=1,
            )
        )
    if not fields:
        raise ArtifactError(
            f"{view.name}: the view has no elements, so there is nothing to generate"
        )
    return DataShape(
        name=view.name,
        fields=tuple(fields),
        rows=len(fields),
        source=f"slpie graph view {view.name}",
        kind="architecture-view",
        doc=view.doc,
    )


class ArchitectureCodegen:
    """Emits enterprise views as importable Python, Mermaid and JSON.

    One instance owns one output directory and the generation history behind it.
    The history matters: it is the *base* of the three-way merge, so the same
    instance must drive successive regenerations of a view for hand edits to be
    distinguishable from generator changes. Handed a fresh instance pointed at a
    directory somebody else wrote, the merge degrades to two-way and overlapping
    symbols conflict rather than being silently resolved — which is the safe
    direction to degrade in.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        policy: MergePolicy = MergePolicy.RAISE,
        actor: str = "slpie.artifacts",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self._registry = ModuleRegistry(
            self.root, actor=actor, conflict_policy=_POLICIES[policy]
        )

    # -- emission --------------------------------------------------------

    def emit(
        self, view: ArchitectureView, *, policy: MergePolicy | None = None
    ) -> EmittedView:
        """Emit one view. Raises :class:`ArchitectureConflict` on divergence."""
        shape = shape_for(view)
        effective = policy if policy is not None else self.policy
        previous = self._registry.current(view.name)
        try:
            generation = self._registry.generate(
                shape, entity=view.name, policy=_POLICIES[effective]
            )
        except MergeConflict as exc:
            raise ArchitectureConflict(view.name, exc.symbol, exc.detail) from exc
        except CodegenError as exc:
            raise ArtifactError(f"{view.name}: {exc}") from exc

        module_path = self.root / f"{generation.module_name}.py"
        mermaid_path = self.root / f"{generation.module_name}.mmd"
        json_path = self.root / f"{generation.module_name}.json"
        mermaid_path.write_text(view.to_mermaid(), encoding="utf-8")
        json_path.write_text(
            json.dumps(view.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return EmittedView(
            name=view.name,
            module_path=module_path,
            mermaid_path=mermaid_path,
            json_path=json_path,
            revision=generation.revision,
            shape_digest=generation.shape_digest,
            elements=len(shape.fields),
            decisions=tuple((d.name, d.resolution.value) for d in generation.decisions),
            # A generation the registry short-circuited keeps its old revision:
            # the shape did not move, so the module on disk was never touched.
            rewritten=previous is None or previous.revision != generation.revision,
        )

    def emit_all(self, views: Iterable[ArchitectureView]) -> tuple[EmittedView, ...]:
        """Emit several views, in the order given, stopping at the first conflict."""
        return tuple(self.emit(view) for view in views)

    # -- inspection ------------------------------------------------------

    def load(self, name: str) -> ModuleType:
        """Import a generated view module, proving it is real Python."""
        try:
            return self._registry.load(name, reload=True)
        except CodegenError as exc:
            raise ArtifactError(f"cannot load generated view {name!r}: {exc}") from exc

    def revisions(self, name: str) -> tuple[int, ...]:
        return tuple(g.revision for g in self._registry.history(name))

    def views(self) -> tuple[str, ...]:
        return tuple(self._registry.entities())

    def report(self) -> dict[str, Any]:
        return {"root": str(self.root), "policy": self.policy.value, **self._registry.report()}

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ArchitectureCodegen root={self.root} views={len(self.views())}>"


def emit_views(
    root: str | Path,
    views: Sequence[ArchitectureView],
    *,
    policy: MergePolicy = MergePolicy.RAISE,
) -> tuple[EmittedView, ...]:
    """One-shot convenience: emit a set of views into a directory."""
    return ArchitectureCodegen(root, policy=policy).emit_all(views)
