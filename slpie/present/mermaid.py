"""Mermaid, written once.

Escaping, the empty case and the arrow syntax were each decided three times
before this existed, in three files that could not see one another. They are
decided here.

── The empty case is a rendering decision, not an error ─────────────────

A diagram with marks and no links is drawn: boxes with no arrows is a true
statement about an architecture with no recorded relationships, and rendering
nothing would look like a broken generator. A diagram with no marks at all
renders as a comment saying so, for the same reason — an empty string is
indistinguishable from a failure, and a reader deserves to know which they have.
"""

from __future__ import annotations

from .diagram import Diagram

#: Mermaid's own keywords for the two orientations a model may ask for.
HEADERS = {"top-down": "graph TD", "left-right": "graph LR"}


def mermaid(diagram: Diagram) -> str:
    """One diagram, as a Mermaid flowchart. Deterministic by construction."""
    header = HEADERS.get(diagram.orientation, HEADERS["left-right"])
    if diagram.empty:
        return f"%% {diagram.name}: nothing to draw\n{header}"

    lines = [header]
    for mark in diagram.marks:
        label = mark.label or mark.id
        text = f"{label}<br/><i>{mark.kind}</i>" if mark.kind else label
        lines.append(f'  {mark.id}["{escape(text)}"]')
    for link in diagram.links:
        arrow = f" -->|{escape(link.label)}| " if link.label else " --> "
        lines.append(f"  {link.source}{arrow}{link.target}")
    return "\n".join(lines)


def document(diagram: Diagram) -> str:
    """The diagram with its own title above it, for a file on disk."""
    return f"%% {diagram.name}: {diagram.doc}\n{mermaid(diagram)}"


#: Characters that end a Mermaid label early or change its meaning. Replaced
#: rather than stripped: a node called `payments"prod` should still be readable
#: as something, and deleting the quote silently renames it.
SUBSTITUTIONS = (('"', "'"), ("[", "("), ("]", ")"), ("{", "("), ("}", ")"),
                 ("|", "/"), ("\n", " "))


def escape(text: str) -> str:
    """A label Mermaid will render as written."""
    for character, replacement in SUBSTITUTIONS:
        text = text.replace(character, replacement)
    return text
