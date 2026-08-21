"""C4 model views, rendered as Mermaid.

The four levels are four *filters over the same graph*, not four hand-drawn
pictures. Context is the system and what it talks to; container is what runs;
component is what is inside a container; code is what is inside a component.
Nothing is redrawn between levels, so a diagram cannot disagree with the level
above it — which is the failure mode of every C4 set maintained by hand.

**Determinism is a feature, not tidiness.** Every element alias is derived from
the node identifier, not from its position in the file, so adding one service
changes exactly one line of the `.mmd`. A diagram whose nodes reorder on each
run produces a diff nobody reviews, and a diagram nobody reviews is a diagram
that has stopped being true.

Confidence is carried, never manufactured: an edge drawn from weak evidence is
labelled with the relationship it came from and the reader can look it up. This
module does not decide that something is "probably" a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..domain.edge import Edge, EdgeKind
from ..domain.node import Node, NodeKind
from ..errors import ArtifactError
from ..graph.store import GraphView

__all__ = [
    "C4Element",
    "C4Level",
    "C4Relationship",
    "C4View",
    "code_view",
    "component_view",
    "container_view",
    "context_view",
    "c4_views",
]


class C4Level(str, Enum):
    """The four levels, with the question each one answers."""

    CONTEXT = "context"      # C1 — who and what does the system talk to?
    CONTAINER = "container"  # C2 — what separately deployable things run?
    COMPONENT = "component"  # C3 — what is inside one container?
    CODE = "code"            # C4 — what is inside one component?

    @property
    def number(self) -> int:
        return _LEVEL_NUMBER[self]

    @property
    def title(self) -> str:
        return _LEVEL_TITLE[self]


_LEVEL_NUMBER = {
    C4Level.CONTEXT: 1, C4Level.CONTAINER: 2, C4Level.COMPONENT: 3, C4Level.CODE: 4,
}
_LEVEL_TITLE = {
    C4Level.CONTEXT: "System Context",
    C4Level.CONTAINER: "Container",
    C4Level.COMPONENT: "Component",
    C4Level.CODE: "Code",
}

#: What each level admits. A kind absent from a level is absent from that
#: diagram — the level is defined by the filter, so the filter is the schema.
LEVEL_KINDS: dict[C4Level, frozenset[NodeKind]] = {
    C4Level.CONTEXT: frozenset({
        NodeKind.DOMAIN, NodeKind.TEAM, NodeKind.EXTERNAL_PROVIDER, NodeKind.DEVICE_CLASS,
    }),
    C4Level.CONTAINER: frozenset({
        NodeKind.SERVICE, NodeKind.WEB_APP, NodeKind.DATABASE, NodeKind.QUEUE,
        NodeKind.RUNTIME_PROCESS, NodeKind.DATASET, NodeKind.AI_MODEL,
        NodeKind.CLOUD_RESOURCE,
    }),
    C4Level.COMPONENT: frozenset({
        NodeKind.COMPONENT, NodeKind.API, NodeKind.EVENT, NodeKind.MODULE,
    }),
    C4Level.CODE: frozenset({
        NodeKind.MODULE, NodeKind.PACKAGE, NodeKind.SCHEMA,
    }),
}

#: Mermaid shape per kind — a store looks like a store, a queue like a queue.
#: Keyed by the kind's *value* because a rendered element carries the string.
_SHAPES: dict[str, tuple[str, str]] = {
    NodeKind.DATABASE.value: ("[(", ")]"),
    NodeKind.DATASET.value: ("[(", ")]"),
    NodeKind.TABLE.value: ("[(", ")]"),
    NodeKind.QUEUE.value: (">", "]"),
    NodeKind.EVENT.value: (">", "]"),
    NodeKind.TEAM.value: ("([", "])"),
    NodeKind.EXTERNAL_PROVIDER.value: ("{{", "}}"),
    NodeKind.DEVICE_CLASS.value: ("{{", "}}"),
    NodeKind.API.value: ("([", "])"),
}
_DEFAULT_SHAPE = ("[", "]")

#: Relationships worth drawing. Ownership and governance are true but they are
#: organisation, not architecture, and they turn a container diagram into a
#: hairball.
DRAWN_KINDS: frozenset[EdgeKind] = frozenset({
    EdgeKind.DEPENDS_ON, EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.READS,
    EdgeKind.WRITES, EdgeKind.PUBLISHES, EdgeKind.SUBSCRIBES, EdgeKind.DEPLOYS_TO,
    EdgeKind.AUTHENTICATES_WITH, EdgeKind.TRANSFORMS,
})


def _alias(node_id: str) -> str:
    """A Mermaid-safe alias derived from the node id, never from its position."""
    return f"n{node_id[:12]}"


def _escape(text: str) -> str:
    """Mermaid label text: quotes and brackets would end the label early."""
    return (
        str(text)
        .replace("\\", "/")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\n", " ")
    )


@dataclass(frozen=True, slots=True)
class C4Element:
    """One box on the diagram, and the node it is."""

    alias: str
    node_id: str
    label: str
    kind: str
    technology: str = ""
    description: str = ""
    external: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias, "node_id": self.node_id, "label": self.label,
            "kind": self.kind, "technology": self.technology,
            "description": self.description, "external": self.external,
        }

    @classmethod
    def of(cls, node: Node) -> "C4Element":
        return cls(
            alias=_alias(node.id),
            node_id=node.id,
            label=node.display,
            kind=node.kind.value,
            technology=str(node.properties.get("technology", "")),
            description=str(node.properties.get("description", "")),
            external=node.kind is NodeKind.EXTERNAL_PROVIDER,
        )


@dataclass(frozen=True, slots=True)
class C4Relationship:
    """One arrow, labelled with the relationship the graph actually recorded."""

    source: str          # alias
    target: str          # alias
    kind: str
    qualifier: str = ""
    confidence: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.kind}{f' ({self.qualifier})' if self.qualifier else ''}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "target": self.target, "kind": self.kind,
            "qualifier": self.qualifier, "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class C4View:
    """One level of the C4 model as a typed structure and as a diagram."""

    level: C4Level
    scope: str
    elements: tuple[C4Element, ...]
    relationships: tuple[C4Relationship, ...]

    @property
    def name(self) -> str:
        stem = f"c4-{self.level.number}-{self.level.value}"
        return f"{stem}-{self.scope}" if self.scope else stem

    @property
    def doc(self) -> str:
        return (
            f"C4 level {self.level.number} ({self.level.title}) view"
            + (f" of {self.scope}" if self.scope else "")
        )

    def rows(self) -> tuple[Mapping[str, Any], ...]:
        """The elements, shaped for :mod:`slpie.artifacts.codegen`."""
        return tuple(
            {
                "id": element.label,
                "label": element.label,
                "kind": element.kind,
                "node_id": element.node_id,
                "technology": element.technology,
                "external": element.external,
            }
            for element in self.elements
        )

    def to_diagram(self) -> Any:
        """This view's shape, as data. Rendering lives in `slpie.present`.

        `external` travels as a fact rather than being dropped: C4 draws
        external systems in their own subgraph, and a renderer that could not
        tell them apart would produce a diagram that is C4-shaped and not C4.
        """
        from ..present.diagram import Diagram, Link, Mark

        return Diagram(
            name=self.name,
            doc=self.doc,
            orientation="top-down",
            marks=tuple(
                Mark(
                    id=element.id, label=element.name, kind=element.kind,
                    facts={"external": element.external,
                           "technology": element.technology},
                )
                for element in self.elements
            ),
            links=tuple(
                Link(source=item.source, target=item.target, label=item.label)
                for item in self.relationships
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "number": self.level.number,
            "title": self.level.title,
            "scope": self.scope,
            "elements": [e.to_dict() for e in self.elements],
            "relationships": [r.to_dict() for r in self.relationships],
        }


    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<C4View C{self.level.number} {self.level.value} "
            f"elements={len(self.elements)} rels={len(self.relationships)}>"
        )


def _element_line(element: C4Element) -> str:
    open_shape, close_shape = _SHAPES.get(
        NodeKind(element.kind) if element.kind in NodeKind._value2member_map_ else None,
        _DEFAULT_SHAPE,
    ) if element.kind in NodeKind._value2member_map_ else _DEFAULT_SHAPE
    detail = element.technology or element.kind
    return (
        f'{element.alias}{open_shape}"{_escape(element.label)}'
        f'<br/><small>[{_escape(detail)}]</small>"{close_shape}'
    )


# --- builders ------------------------------------------------------------


def _select(graph: GraphView, kinds: Iterable[NodeKind], *, within: str = "") -> tuple[Node, ...]:
    """Live nodes of the given kinds, optionally restricted to one container.

    "Within" follows containment backwards: a component is inside a container
    when the container owns, deploys or imports it. Containment is a graph fact
    here, so a component that moved between containers moves on the diagram.
    """
    nodes = [
        node
        for kind in sorted(set(kinds), key=lambda k: k.value)
        for node in graph.nodes(kind=kind, live=True)
    ]
    if within:
        contained = {
            edge.dst
            for kind in (EdgeKind.OWNS, EdgeKind.DEPLOYS_TO, EdgeKind.IMPORTS, EdgeKind.DEPENDS_ON)
            for edge in graph.edges_from(within, kind=kind, live=True)
        }
        contained |= {
            edge.src
            for kind in (EdgeKind.DEPLOYS_TO,)
            for edge in graph.edges_to(within, kind=kind, live=True)
        }
        nodes = [node for node in nodes if node.id in contained]
    return tuple(sorted(nodes, key=lambda n: (n.kind.value, n.display, n.id)))


def _relationships(graph: GraphView, nodes: Sequence[Node]) -> tuple[C4Relationship, ...]:
    """Every drawable live edge between the selected nodes, deduplicated."""
    selected = {node.id for node in nodes}
    seen: dict[tuple[str, str, str, str], C4Relationship] = {}
    for node in nodes:
        for edge in graph.edges_from(node.id, live=True):
            if edge.dst not in selected or edge.kind not in DRAWN_KINDS:
                continue
            # Keyed rather than guarded. The graph already collapses two
            # observations of the same relationship onto one edge id, so a
            # `if key not in seen` branch could never be taken — and a
            # never-taken guard reads as though duplicates were expected here,
            # which sends the next reader looking for a problem that lives one
            # layer down. Assigning into the dict is idempotent and says the
            # same thing without the dead branch.
            key = (_alias(edge.src), _alias(edge.dst), edge.kind.value, edge.qualifier)
            seen[key] = C4Relationship(
                source=_alias(edge.src),
                target=_alias(edge.dst),
                kind=edge.kind.value,
                qualifier=edge.qualifier,
                confidence=edge.confidence,
            )
    return tuple(sorted(seen.values(), key=lambda r: (r.source, r.target, r.kind, r.qualifier)))


def _build(graph: GraphView, level: C4Level, *, scope: str = "", scope_id: str = "") -> C4View:
    nodes = _select(graph, LEVEL_KINDS[level], within=scope_id)
    return C4View(
        level=level,
        scope=scope,
        elements=tuple(C4Element.of(node) for node in nodes),
        relationships=_relationships(graph, nodes),
    )


def context_view(graph: GraphView, *, scope: str = "") -> C4View:
    """C1 — the system's domains, its teams, and everything external to it."""
    return _build(graph, C4Level.CONTEXT, scope=scope)


def container_view(graph: GraphView, *, scope: str = "") -> C4View:
    """C2 — the separately deployable things and the stores they use."""
    return _build(graph, C4Level.CONTAINER, scope=scope)


def component_view(graph: GraphView, container: str, *, scope: str = "") -> C4View:
    """C3 — what is inside one container. ``container`` is a node id."""
    if not container:
        raise ArtifactError("a component view needs the node id of a container")
    node = graph.node(container)
    return _build(
        graph,
        C4Level.COMPONENT,
        scope=scope or (node.display if node else container),
        scope_id=container,
    )


def code_view(graph: GraphView, component: str, *, scope: str = "") -> C4View:
    """C4 — the modules and packages that make up one component."""
    if not component:
        raise ArtifactError("a code view needs the node id of a component")
    node = graph.node(component)
    return _build(
        graph,
        C4Level.CODE,
        scope=scope or (node.display if node else component),
        scope_id=component,
    )


def c4_views(
    graph: GraphView, *, container: str = "", component: str = ""
) -> tuple[C4View, ...]:
    """The levels that can be built from this graph, C1 first.

    C3 and C4 need a subject, so they are produced only when one is named. A
    component diagram with no container is not a C4 diagram, it is a list.
    """
    views = [context_view(graph), container_view(graph)]
    if container:
        views.append(component_view(graph, container))
    if component:
        views.append(code_view(graph, component))
    return tuple(views)
