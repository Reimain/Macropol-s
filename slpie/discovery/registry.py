"""Registering the built-in discoverers, and running a scan.

Every built-in goes through :meth:`Registry.register_builtin` — the same call a
third-party plugin uses. Nothing here has a private path into the platform, and
that is checked by the fact that this module imports only the public plugin API.

:class:`Scanner` is the loop: for each attached element, list what the connector
offers, hand each file to whichever plugins claim it, and turn the observations
into nodes and edges. It never decides what an observation *means* — that is
linking, in phase 9. Here an observation becomes a node or an edge with the
evidence that was cited, and nothing more.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..binding.connector import Connector
from ..core.commands import RecordObservation
from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence
from ..domain.identity import Identity, parse_identity
from ..domain.node import Node, NodeKind
from ..errors import BindingError, IdentityError
from ..plugins.manifest import ExtensionPoint
from ..plugins.protocol import DiscoveryResult, Observation
from ..plugins.registry import Registry
from .base import Source

#: Observation kinds that become edges, and the edge kind each maps to.
EDGE_KINDS: dict[str, EdgeKind] = {
    "depends_on": EdgeKind.DEPENDS_ON,
    "imports": EdgeKind.IMPORTS,
    "exports": EdgeKind.EXPORTS,
    "calls": EdgeKind.CALLS,
    "reads": EdgeKind.READS,
    "writes": EdgeKind.WRITES,
    "publishes": EdgeKind.PUBLISHES,
    "subscribes": EdgeKind.SUBSCRIBES,
    "deploys_to": EdgeKind.DEPLOYS_TO,
    "references": EdgeKind.REFERENCES,
    "generates": EdgeKind.GENERATES,
    "owns": EdgeKind.OWNS,
}

#: Files never worth reading, whatever claims them.
SKIP = ("/node_modules/", "/.venv/", "/__pycache__/", "/.tox/", "/dist/", "/build/")

#: A single file bigger than this is not source. Reading it would cost more than
#: anything it could tell us.
MAX_SOURCE_BYTES = 4 * 1024 * 1024


def register_builtins(registry: Registry) -> Registry:
    """Register every built-in discoverer, through the public plugin path."""
    from .code import javascript, python_ast
    from .ecosystems import npm, python

    modules = (npm, python, python_ast, javascript)
    for module in modules:
        for plugin_id, handler, handles, produces, evidence_kinds in module.DISCOVERERS:
            if plugin_id in registry:
                continue
            registry.register_builtin(
                plugin_id,
                _adapt(handler),
                kind=ExtensionPoint.DISCOVERER,
                handles=handles,
                produces=produces,
                evidence_kinds=evidence_kinds,
                # Manifests and lockfiles before source: the packaging layer
                # names things, and source analysis is corroboration.
                priority=10 if "lock" in plugin_id else (20 if "npm" in plugin_id or "python" in plugin_id else 50),
                description=f"built-in {plugin_id} discoverer",
            )
    return registry


def _adapt(handler: Any) -> Any:
    """Wrap a `(Source) -> DiscoveryResult` function in the plugin signature."""

    def run(uri: str, **context: Any) -> DiscoveryResult:
        return handler(Source(
            uri=uri,
            text=str(context.get("content", "")),
            digest=str(context.get("digest", "")),
            element=str(context.get("element", "")),
        ))

    run.__name__ = getattr(handler, "__name__", "discoverer")
    return run


@dataclass(slots=True)
class ScanReport:
    """What one scan did, and what it could not do."""

    files_seen: int = 0
    files_read: int = 0
    observations: int = 0
    nodes: int = 0
    edges: int = 0
    errors: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    elements: tuple[str, ...] = ()
    duration_ns: int = 0

    @property
    def clean(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_seen": self.files_seen, "files_read": self.files_read,
            "observations": self.observations, "nodes": self.nodes, "edges": self.edges,
            "errors": list(self.errors), "skipped": list(self.skipped),
            "elements": list(self.elements), "duration_ns": self.duration_ns,
            "clean": self.clean,
        }

    def __str__(self) -> str:
        return (
            f"{self.files_read}/{self.files_seen} files, "
            f"{self.nodes} nodes, {self.edges} edges"
            + (f", {len(self.errors)} errors" if self.errors else "")
        )


class Scanner:
    """Runs discovery across attached elements and records what it finds."""

    def __init__(self, registry: Registry, *, commands: Any = None) -> None:
        self.registry = registry
        self.commands = commands

    def scan_element(
        self, element: str, connector: Connector, *, root: str = ""
    ) -> tuple[tuple[Observation, ...], list[str], int, int]:
        """Discover one element. Returns observations, errors, seen, read."""
        observations: list[Observation] = []
        errors: list[str] = []
        seen = read = 0

        try:
            listing = connector.list(root)
        except BindingError as error:
            # An element that will not list is a gap the station already
            # reports. Recording it here too would double-count it.
            return (), [f"{element}: {error}"], 0, 0

        for uri in listing:
            seen += 1
            if any(part in uri for part in SKIP):
                continue
            if not self.registry.for_uri(uri):
                continue

            try:
                resource = connector.read(uri)
            except BindingError as error:
                errors.append(f"{uri}: {error}")
                continue
            if len(resource.content) > MAX_SOURCE_BYTES:
                errors.append(f"{uri}: too large to analyse")
                continue

            read += 1
            outcome = self.registry.discover(
                uri, content=resource.text, digest=resource.digest, element=element,
            )
            observations.extend(outcome.observations)
            errors.extend(outcome.errors)

        return tuple(observations), errors, seen, read

    def scan(self, bindings: Iterable[Any], *, actor: str = "scan") -> ScanReport:
        """Scan every binding and record the result as one correlated operation."""
        started = time.time_ns()
        observations: list[Observation] = []
        errors: list[str] = []
        elements: list[str] = []
        seen = read = 0

        for binding in bindings:
            if not binding.available:
                continue
            elements.append(binding.element)
            found, element_errors, element_seen, element_read = self.scan_element(
                binding.element,
                binding.connector,
                root=binding.declaration.location if binding.declaration else "",
            )
            observations.extend(found)
            errors.extend(element_errors)
            seen += element_seen
            read += element_read

        nodes, edges, conversion_errors = materialise(observations)
        errors.extend(conversion_errors)

        if self.commands is not None and (nodes or edges):
            self.commands.dispatch(RecordObservation(
                nodes=nodes, edges=edges,
                source_uri="slpie://scan",
                discoverer="slpie.scanner",
                actor=actor,
                correlation_id=f"scan-{started}",
            ))

        return ScanReport(
            files_seen=seen, files_read=read, observations=len(observations),
            nodes=len(nodes), edges=len(edges), errors=tuple(errors),
            elements=tuple(elements), duration_ns=time.time_ns() - started,
        )


def materialise(
    observations: Sequence[Observation],
) -> tuple[tuple[Node, ...], tuple[Edge, ...], list[str]]:
    """Turn observations into nodes and edges, merging evidence per subject.

    Merging matters. A package named in a manifest, pinned in a lockfile and
    imported in source is *one* node with three pieces of evidence, and its
    confidence follows from that. Emitting three nodes would both fragment the
    graph and throw away the corroboration that makes the claim strong.
    """
    node_evidence: dict[str, list[Evidence]] = {}
    node_identity: dict[str, Identity] = {}
    node_properties: dict[str, dict[str, Any]] = {}
    edge_evidence: dict[tuple[str, str, str, str], list[Evidence]] = {}
    edge_properties: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    errors: list[str] = []

    def remember(raw: str) -> str | None:
        try:
            identity = parse_identity(raw)
        except IdentityError as error:
            errors.append(f"{raw}: {error}")
            return None
        key = identity.to_string()
        node_identity.setdefault(key, identity)
        node_evidence.setdefault(key, [])
        node_properties.setdefault(key, {})
        return key

    for observation in observations:
        if observation.evidence is None:
            errors.append(f"{observation}: no evidence")
            continue

        subject = remember(observation.subject)
        if subject is None:
            continue

        if not observation.is_relationship:
            node_evidence[subject].append(observation.evidence)
            node_properties[subject].update(dict(observation.properties))
            continue

        target = remember(observation.object)
        if target is None:
            continue

        # A relationship is also evidence that both endpoints exist.
        node_evidence[subject].append(observation.evidence)
        node_evidence[target].append(observation.evidence)

        edge_kind = EDGE_KINDS.get(observation.kind)
        if edge_kind is None:
            errors.append(f"unknown observation kind {observation.kind!r}")
            continue

        key = (edge_kind.value, subject, target, observation.qualifier)
        edge_evidence.setdefault(key, []).append(observation.evidence)
        edge_properties.setdefault(key, {}).update(dict(observation.properties))

    nodes: list[Node] = []
    node_ids: dict[str, str] = {}
    for key, identity in node_identity.items():
        evidence = _dedupe(node_evidence[key])
        if not evidence:
            continue
        properties = node_properties[key]
        node = Node(
            kind=_node_kind(properties, identity),
            identity=identity,
            evidence=evidence,
            properties={k: v for k, v in properties.items() if k != "node_kind"},
        )
        nodes.append(node)
        node_ids[key] = node.id

    edges: list[Edge] = []
    for (kind, subject, target, qualifier), evidence in edge_evidence.items():
        source_id, target_id = node_ids.get(subject), node_ids.get(target)
        if not source_id or not target_id:
            continue
        edges.append(Edge(
            kind=EdgeKind(kind), src=source_id, dst=target_id,
            evidence=_dedupe(evidence), qualifier=qualifier,
            properties=edge_properties[(kind, subject, target, qualifier)],
        ))

    return tuple(nodes), tuple(edges), errors


def _dedupe(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Identical observations are one piece of evidence, not several."""
    return tuple({item.id: item for item in evidence}.values())


def _node_kind(properties: Mapping[str, Any], identity: Identity) -> NodeKind:
    stated = str(properties.get("node_kind", ""))
    if stated:
        try:
            return NodeKind(stated)
        except ValueError:
            pass
    text = identity.to_string()
    if text.startswith("pkg:"):
        return NodeKind.PACKAGE
    if ":module:" in text:
        return NodeKind.MODULE
    if ":repository:" in text:
        return NodeKind.REPOSITORY
    return NodeKind.COMPONENT
