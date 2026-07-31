"""What every enterprise view is, and the two rules they all obey.

A view is a **projection of the graph, never a second store**. It selects nodes,
reads what the graph already derived, and orders the result. It computes no
confidence, invents no classification and holds no state — so a view cannot
disagree with the graph, because there is nothing in it that could drift.

Two decisions shape the type:

**A view is data, and `rows()` is its whole content.** The codegen bridge turns
one row into one field of a frozen dataclass, so the row *is* the contract: a
downstream deployment script that names an application which has left the
architecture fails at import rather than at review. That only works if rows are
plain mappings with a stable `id`, which is why `Row` is a dict rather than a
class — Gratimos is the thing that turns it into a type, and doing it twice would
mean two definitions of what an element is.

**Ordering is part of the output.** Views are emitted to disk and diffed by
humans and by CI. A view whose row order depended on a set's iteration order
would produce a spurious diff on every run, and a spurious diff is how people
learn to stop reading them. Every view sorts explicitly.

`ArchitectureView` in `slpie/artifacts/codegen.py` is a structural Protocol, so
nothing here inherits from anything there and the dependency points one way:
`enterprise` knows nothing about code generation, and `artifacts` knows nothing
about TOGAF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.node import Node

#: One element of a view. `id` is required and becomes a Python identifier, so
#: it is upper-snake; everything else is free.
Row = Mapping[str, Any]

#: Characters that cannot appear in a Python identifier. Replaced rather than
#: dropped, so two elements differing only in punctuation stay distinct.
_UNSAFE = ".-/@:+ ()[]{}#!?,'\"\\<>=*%$&|^~`;"


def identifier(text: str, *, fallback: str = "ELEMENT") -> str:
    """A node's display name → a stable Python identifier.

    Upper-snake because these become dataclass field names that a human reads in
    an import statement. Deterministic and total: any input yields a valid
    identifier, because a view that raised on an awkward package name would fail
    on exactly the trees most worth describing.
    """
    cleaned = "".join("_" if character in _UNSAFE else character for character in text)
    cleaned = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in cleaned
    )
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_").upper()
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        return f"N_{cleaned}"
    return cleaned


def unique(rows: Sequence[Row]) -> tuple[Row, ...]:
    """Rows with unique ids, later duplicates suffixed rather than dropped.

    Two nodes can legitimately reduce to the same identifier — `my-lib` and
    `my_lib` are different packages. Dropping the second would silently remove an
    element from the architecture; suffixing keeps both and keeps the file
    importable, which is the only outcome that loses nothing.
    """
    seen: dict[str, int] = {}
    out: list[Row] = []
    for row in rows:
        base = str(row.get("id", "")) or "ELEMENT"
        count = seen.get(base, 0)
        seen[base] = count + 1
        # Always written back, never passed through unchanged: a row that
        # arrived without an id would otherwise keep not having one, and
        # `shape_for` refuses a row with no id — so the failure landed in the
        # code generator rather than here where the id is actually decided.
        out.append({**row, "id": base if count == 0 else f"{base}_{count + 1}"})
    return tuple(out)


@dataclass(frozen=True, slots=True)
class View:
    """One enterprise view: named rows, a diagram, and the graph behind it.

    Concrete views are *built* by the functions in this package rather than
    subclassing, because a view is a value. Two runs over an unchanged graph must
    produce equal views, and that is much easier to guarantee for a frozen
    dataclass than for an object with a graph handle inside it.
    """

    name: str
    doc: str
    elements: tuple[Row, ...] = ()
    relations: tuple[tuple[str, str, str], ...] = ()
    diagram: str = "graph LR"

    def rows(self) -> tuple[Row, ...]:
        return self.elements

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "doc": self.doc,
            "elements": [dict(row) for row in self.elements],
            "relations": [
                {"source": source, "kind": kind, "target": target}
                for source, kind, target in self.relations
            ],
            "counts": {
                "elements": len(self.elements),
                "relations": len(self.relations),
            },
        }

    def to_mermaid(self) -> str:
        """The view as a Mermaid diagram.

        Emitted even when the view has no relations: a diagram of boxes with no
        arrows is a true statement about an architecture with no recorded
        relationships, and rendering nothing would look like a broken generator.
        """
        lines = [self.diagram]
        for row in self.elements:
            label = str(row.get("label", row.get("id", "")))
            kind = str(row.get("kind", ""))
            text = f"{label}<br/><i>{kind}</i>" if kind else label
            lines.append(f'  {row["id"]}["{_escape(text)}"]')
        for source, kind, target in self.relations:
            lines.append(f"  {source} -->|{_escape(kind)}| {target}")
        return "\n".join(lines)

    @property
    def empty(self) -> bool:
        return not self.elements

    def __len__(self) -> int:
        return len(self.elements)

    def __str__(self) -> str:
        return (
            f"{self.name}: {len(self.elements)} element(s), "
            f"{len(self.relations)} relation(s)"
        )


def _escape(text: str) -> str:
    """Mermaid label text. Quotes and brackets would end the label early."""
    return (
        str(text)
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\n", " ")
    )


def relations_between(
    graph: Any,
    nodes: Sequence[Node],
    *,
    kinds: Iterable[Any] = (),
    limit: int = 400,
) -> tuple[tuple[str, str, str], ...]:
    """Edges whose both ends are in this view, as (source_id, kind, target_id).

    Closed over the view on purpose. An arrow pointing at a box that is not on
    the diagram renders as a dangling node with no label, which reads as a
    missing element rather than as an out-of-scope one — so an edge leaving the
    view is omitted here and reported by whatever view does contain both ends.
    """
    wanted = frozenset(kinds)
    identifiers = {node.id: identifier(node.display) for node in nodes}
    found: set[tuple[str, str, str]] = set()

    for node in nodes:
        for edge in graph.edges_from(node.id, live=True):
            if wanted and edge.kind not in wanted:
                continue
            target = identifiers.get(edge.dst)
            if target is None:
                continue
            found.add((
                identifiers[node.id],
                str(getattr(edge.kind, "value", edge.kind)),
                target,
            ))
    return tuple(sorted(found))[:limit]
