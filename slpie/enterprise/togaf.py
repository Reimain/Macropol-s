"""TOGAF's three architecture domains, built from the graph.

TOGAF asks the same question three times at different altitudes, and the value is
in keeping them *separate*. One diagram containing services, tables and container
images answers none of the three well: an application architect wants to know what
talks to what, a data architect wants to know where an entity lives and who reads
it, and an infrastructure owner wants to know what runs where and on what version.

===============  =============================================================
Application      services, APIs, web apps, and the calls between them
Data             entities, tables, schemas and datasets, with their readers
Technology       runtimes, deployments, cloud resources and the standards used
===============  =============================================================

Every view here is a **selection over the graph and nothing more**. The
classification on a node — `ArchitectureClass.CORE`, `LifecycleState.DEPRECATED`
— was derived when the node was asserted, and these functions read it rather than
recomputing it. That is what makes a generated architecture defensible: every
statement in it traces back through the same `derived_from` chain as any other
answer, and there is no second place where a component could become "core".

**A view of an empty selection is empty, not absent.** `application_view` over a
graph with no services returns a view with no rows, and the caller decides what
that means. Raising here would make "this environment has no web tier" — a real
and interesting fact — indistinguishable from a broken generator.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain.edge import EdgeKind
from ..domain.node import Node, NodeKind
from .view import Row, View, identifier, relations_between, unique

#: What the application architecture is made of: things that serve requests.
APPLICATION_KINDS = (
    NodeKind.SERVICE, NodeKind.API, NodeKind.WEB_APP, NodeKind.COMPONENT,
    NodeKind.EXTERNAL_PROVIDER, NodeKind.EVENT, NodeKind.QUEUE,
)

#: What the data architecture is made of: things that hold or describe state.
DATA_KINDS = (
    NodeKind.DATABASE, NodeKind.TABLE, NodeKind.ENTITY, NodeKind.SCHEMA,
    NodeKind.DATASET, NodeKind.AI_MODEL,
)

#: What the technology architecture is made of: things that run or host.
TECHNOLOGY_KINDS = (
    NodeKind.RUNTIME_PROCESS, NodeKind.DEPLOYMENT, NodeKind.ENVIRONMENT,
    NodeKind.CLOUD_RESOURCE, NodeKind.PIPELINE, NodeKind.DEVICE_CLASS,
)

#: Application-layer relationships. `owns` is excluded deliberately — team
#: ownership is a governance fact and putting it on the application diagram
#: doubles the arrows without answering "what talks to what".
APPLICATION_EDGES = frozenset({
    EdgeKind.CALLS, EdgeKind.DEPENDS_ON, EdgeKind.PUBLISHES,
    EdgeKind.SUBSCRIBES, EdgeKind.AUTHENTICATES_WITH,
})

#: Data-layer relationships: who touches the state, and how it moves.
DATA_EDGES = frozenset({
    EdgeKind.READS, EdgeKind.WRITES, EdgeKind.TRANSFORMS, EdgeKind.REFERENCES,
    EdgeKind.MIRRORS,
})

#: Technology-layer relationships.
TECHNOLOGY_EDGES = frozenset({
    EdgeKind.DEPLOYS_TO, EdgeKind.DEPENDS_ON, EdgeKind.GENERATES,
})


def _select(graph: Any, kinds: Iterable[NodeKind]) -> tuple[Node, ...]:
    """Live nodes of the given kinds, ordered by identity.

    Sorted by the identity string rather than by display name because two
    elements can share a display name, and a view that reordered itself when
    that happened would produce a spurious diff on every regeneration.
    """
    found = [
        node
        for kind in kinds
        for node in graph.nodes(kind=kind, live=True)
    ]
    return tuple(sorted(found, key=lambda node: (node.identity.to_string(), node.id)))


def _row(node: Node, **extra: Any) -> Row:
    """One node → one row, reading what the graph derived and adding nothing."""
    row: dict[str, Any] = {
        "id": identifier(node.display),
        "label": node.display,
        "kind": node.kind.value,
        "node_id": node.id,
        "identity": node.identity.to_string(),
        "lifecycle": node.lifecycle.value,
        "architecture": node.architecture.value,
        "confidence": round(node.confidence, 4),
        "validation": node.validation.value,
    }
    if node.version:
        row["version"] = node.version
    row.update({key: value for key, value in extra.items() if value not in ("", None)})
    return row


def application_view(graph: Any) -> View:
    """TOGAF Application Architecture — what serves, and what it calls."""
    nodes = _select(graph, APPLICATION_KINDS)
    rows = unique([
        _row(
            node,
            team=str(node.properties.get("team", "")),
            domain=str(node.properties.get("domain", "")),
            protocol=str(node.properties.get("kind", "")),
        )
        for node in nodes
    ])
    return View(
        name="application",
        doc=(
            "TOGAF Application Architecture: the services, APIs and web "
            "applications in this environment, and the calls between them"
        ),
        elements=rows,
        relations=relations_between(graph, nodes, kinds=APPLICATION_EDGES),
        orientation="left-right",
    )


def data_view(graph: Any) -> View:
    """TOGAF Data Architecture — where state lives, and who touches it."""
    nodes = _select(graph, DATA_KINDS)
    rows = unique([
        _row(
            node,
            classification=str(node.properties.get("classification", "")),
            store=str(node.properties.get("store", "")),
            compliance=node.compliance.value,
        )
        for node in nodes
    ])
    return View(
        name="data",
        doc=(
            "TOGAF Data Architecture: the entities, tables and datasets in this "
            "environment, their classification, and the components that read "
            "and write them"
        ),
        elements=rows,
        relations=relations_between(graph, nodes, kinds=DATA_EDGES),
        orientation="top-down",
    )


def technology_view(graph: Any) -> View:
    """TOGAF Technology Architecture — what runs where, on what."""
    nodes = _select(graph, TECHNOLOGY_KINDS)
    rows = unique([
        _row(
            node,
            runtime=str(node.properties.get("runtime", "")),
            region=str(node.properties.get("region", "")),
            provider=str(node.properties.get("provider", "")),
        )
        for node in nodes
    ])
    return View(
        name="technology",
        doc=(
            "TOGAF Technology Architecture: the runtimes, deployments and cloud "
            "resources this environment runs on"
        ),
        elements=rows,
        relations=relations_between(graph, nodes, kinds=TECHNOLOGY_EDGES),
        orientation="top-down",
    )


def standards_view(graph: Any) -> View:
    """The technology standards catalogue — one row per distinct runtime.

    TOGAF's standards catalogue is where "we are on four versions of Python"
    becomes visible. Built by *aggregating* the technology nodes rather than
    listing them, because the question is about the estate's spread, and a list
    of four hundred deployments does not answer it.
    """
    nodes = _select(graph, TECHNOLOGY_KINDS)
    counted: dict[tuple[str, str], list[Node]] = {}
    for node in nodes:
        runtime = str(node.properties.get("runtime", "")) or node.kind.value
        version = str(node.properties.get("runtime_version", "")) or node.version
        counted.setdefault((runtime, version), []).append(node)

    rows = unique([
        {
            "id": identifier(f"{runtime} {version}" if version else runtime),
            "label": f"{runtime} {version}".strip(),
            "kind": "standard",
            "runtime": runtime,
            "version": version,
            "instances": len(members),
            # Named so an operator can go and look, bounded so the row stays
            # readable when a standard has three hundred instances.
            "examples": ", ".join(sorted(node.display for node in members)[:3]),
        }
        for (runtime, version), members in sorted(counted.items())
    ])
    return View(
        name="standards",
        doc=(
            "TOGAF technology standards catalogue: each distinct runtime and "
            "version in the estate, and how many things are on it"
        ),
        elements=rows,
        orientation="left-right",
    )


def togaf_views(graph: Any) -> tuple[View, ...]:
    """Every TOGAF view this graph can support, in TOGAF's own order.

    Empty views are **kept**. A data architecture with no entities is a finding
    about the environment — most likely that nothing has scanned the warehouse
    yet — and dropping it would turn a visible gap into an absence nobody
    notices.
    """
    return (
        application_view(graph),
        data_view(graph),
        technology_view(graph),
        standards_view(graph),
    )
