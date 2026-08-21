"""What a model says about its own shape — and nothing about how it is drawn.

A `Diagram` is the handover: the last thing the intelligence layer produces and
the first thing a renderer consumes. It carries marks, links, and an
*orientation* — which is a layout intent rather than syntax, so a model can say
"this reads better top-down" without knowing that Mermaid spells that `graph TD`.

That distinction is the whole point of the type. The enterprise `View` used to
carry the literal string `"graph TD"`, which meant every view in the platform
had a Mermaid keyword baked into it and no other renderer could be added without
either parsing that string or ignoring it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: How a diagram reads. Two values because those are the two that matter and a
#: third would be a renderer's option rather than a model's intent.
ORIENTATIONS = ("top-down", "left-right")


@dataclass(frozen=True, slots=True)
class Mark:
    """One thing on the diagram."""

    id: str
    label: str = ""
    kind: str = ""
    #: Anything a renderer may use and none may require: a severity, a
    #: confidence, a count. Kept open because a renderer that needed a new
    #: field would otherwise have to change every model that builds one.
    facts: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"id": self.id, "label": self.label or self.id}
        if self.kind:
            out["kind"] = self.kind
        if self.facts:
            out["facts"] = dict(self.facts)
        return out


@dataclass(frozen=True, slots=True)
class Link:
    """One relationship, in the direction the graph records it."""

    source: str
    target: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "label": self.label}


@dataclass(frozen=True, slots=True)
class Diagram:
    """A drawable shape, with no idea what will draw it."""

    name: str
    marks: tuple[Mark, ...] = ()
    links: tuple[Link, ...] = ()
    orientation: str = "left-right"
    doc: str = ""

    @property
    def empty(self) -> bool:
        return not self.marks

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "doc": self.doc, "orientation": self.orientation,
            "marks": [mark.to_dict() for mark in self.marks],
            "links": [link.to_dict() for link in self.links],
        }
