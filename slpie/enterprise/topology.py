"""Deployment topology — what is running, where, and what reaches it.

The application architecture says what exists; the topology says where it *is*.
They are separate views because they answer separate questions and because they
fail separately: a service present in the application view and absent from the
topology is not deployed, and that delta is the interesting part.

Grouped by environment and then by zone, because those are the two boundaries
that actually constrain traffic — and because an ungrouped list of two hundred
deployments is a list, not a topology.

**A deployment with no environment is placed in `unknown`, not dropped.** An
element nobody has told us where to put is exactly the element worth seeing on
the diagram; hiding it would make the topology look tidier than the estate is.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain.edge import EdgeKind
from ..domain.node import Node, NodeKind
from .view import View, identifier, relations_between, unique

#: Things that occupy a place. A package does not; a deployment does.
PLACED_KINDS = (
    NodeKind.DEPLOYMENT, NodeKind.RUNTIME_PROCESS, NodeKind.CLOUD_RESOURCE,
    NodeKind.ENVIRONMENT, NodeKind.SERVICE, NodeKind.DATABASE,
)

#: Where an element says it lives. Checked in order — an explicit `environment`
#: beats one inferred from a deployment target.
PLACE_PROPERTIES = ("environment", "env", "namespace", "cluster", "stage")

#: Where an element sits inside its environment.
ZONE_PROPERTIES = ("zone", "region", "availability_zone", "subnet", "tier")

#: Used when nothing says. Named rather than blank so it sorts and renders.
UNPLACED = "unknown"

TOPOLOGY_EDGES = frozenset({
    EdgeKind.DEPLOYS_TO, EdgeKind.CALLS, EdgeKind.DEPENDS_ON,
    EdgeKind.AUTHENTICATES_WITH,
})


def _first(node: Node, names: Iterable[str]) -> str:
    for name in names:
        value = str(node.properties.get(name, "") or "").strip()
        if value:
            return value
    return ""


def place_of(node: Node) -> tuple[str, str]:
    """(environment, zone) for one node, `unknown` where nothing says."""
    return (
        _first(node, PLACE_PROPERTIES) or UNPLACED,
        _first(node, ZONE_PROPERTIES) or UNPLACED,
    )


def _placed(graph: Any) -> tuple[Node, ...]:
    found = [
        node
        for kind in PLACED_KINDS
        for node in graph.nodes(kind=kind, live=True)
    ]
    return tuple(sorted(found, key=lambda node: (node.identity.to_string(), node.id)))


def topology_view(graph: Any) -> View:
    """Everything that occupies a place, grouped by environment and zone."""
    nodes = _placed(graph)
    rows = unique([
        {
            "id": identifier(node.display),
            "label": node.display,
            "kind": node.kind.value,
            "node_id": node.id,
            "environment": place_of(node)[0],
            "zone": place_of(node)[1],
            "lifecycle": node.lifecycle.value,
            "replicas": str(node.properties.get("replicas", "")),
            "image": str(node.properties.get("image", "")),
            "confidence": round(node.confidence, 4),
        }
        for node in nodes
    ])
    return View(
        name="topology",
        doc=(
            "Deployment topology: what is running, in which environment and "
            "zone, and what reaches it"
        ),
        elements=rows,
        relations=relations_between(graph, nodes, kinds=TOPOLOGY_EDGES),
        orientation="top-down",
    )


def environments(graph: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    """`{environment: {zone: (display, …)}}` — the topology as a nested map.

    Offered alongside the view because a caller rendering a grouped diagram
    needs the grouping, and re-deriving it from the flat rows would be a second
    implementation of `place_of` waiting to disagree with the first.
    """
    grouped: dict[str, dict[str, list[str]]] = {}
    for node in _placed(graph):
        environment, zone = place_of(node)
        grouped.setdefault(environment, {}).setdefault(zone, []).append(node.display)
    return {
        environment: {
            zone: tuple(sorted(members)) for zone, members in sorted(zones.items())
        }
        for environment, zones in sorted(grouped.items())
    }


def undeployed(graph: Any) -> tuple[Node, ...]:
    """Services present in the architecture and absent from any environment.

    The delta between "exists" and "runs", which is the reason topology is a
    separate view rather than a filter on the application one.
    """
    found = [
        node for node in graph.nodes(kind=NodeKind.SERVICE, live=True)
        if place_of(node)[0] == UNPLACED
        and not graph.edges_from(node.id, kind=EdgeKind.DEPLOYS_TO, live=True)
    ]
    return tuple(sorted(found, key=lambda node: node.identity.to_string()))
