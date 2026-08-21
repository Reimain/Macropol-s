"""Loading a manifest, and turning it into a skeleton graph in milliseconds.

The declare-first claim is concrete: :func:`load` reads and validates a file,
and :func:`skeleton` turns it into nodes and edges with no I/O beyond that one
read. The graph exists before any discoverer runs, which is what makes the first
question answerable immediately and what gives discovery something to
*corroborate* rather than something to build from nothing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain.edge import Edge, EdgeKind
from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.identity import Urn
from ..domain.lifecycle import LifecycleState
from ..domain.node import Node, NodeKind
from ..errors import ManifestError
from .manifest import (
    Boundary,
    Declaration,
    DeclarationKind,
    Manifest,
    SecurityPosture,
    Target,
    declaration_name,
)
from .schema import parse_yaml, validate

#: The conventional filename, so `slpie` in a repository root just works.
DEFAULT_FILENAME = "slpie.environment.yaml"


def load(path: str | Path) -> Manifest:
    """Read and validate a manifest from disk."""
    location = Path(path)
    if location.is_dir():
        location = location / DEFAULT_FILENAME
    if not location.exists():
        raise ManifestError(f"no manifest at {location}")
    return loads(location.read_text(encoding="utf-8"), source_uri=location.as_uri())


def loads(text: str, *, source_uri: str = "") -> Manifest:
    """Parse a manifest from text — the path the simulator and the UI use."""
    document = validate(parse_yaml(text))
    lines = _line_index(text)

    declarations: list[Declaration] = []
    for kind in DeclarationKind:
        for entry in document.get(kind.value) or ():
            name = declaration_name(kind, entry)
            declarations.append(Declaration(
                kind=kind,
                name=name,
                attributes={k: v for k, v in entry.items() if k != "name"},
                line=lines.get(name, 0),
            ))

    duplicates = _duplicates(declarations)
    if duplicates:
        # Two declarations with one name would collapse into a single node, and
        # the second would silently overwrite the first.
        raise ManifestError(
            f"duplicate declaration name(s): {', '.join(sorted(duplicates))}"
        )

    return Manifest(
        environment=str(document["environment"]),
        target=Target(document.get("target", "simulated")),
        declarations=tuple(declarations),
        security=_security(document.get("security") or {}),
        api_version=str(document["apiVersion"]),
        source_uri=source_uri,
        metadata=dict(document.get("metadata") or {}),
    )


def _security(section: Mapping[str, Any]) -> SecurityPosture:
    return SecurityPosture(
        concerns=tuple(_as_list(section.get("concerns"))),
        secret_scanners=tuple(_as_list(section.get("secret_scanners"))),
        policies=tuple(_as_list(section.get("policies"))),
        boundaries=tuple(
            Boundary(
                name=str(entry.get("name", "")),
                contains=tuple(str(c) for c in _as_list(entry.get("contains"))),
                classification=str(entry.get("classification", "")),
            )
            for entry in (section.get("boundaries") or ())
            if isinstance(entry, Mapping) and entry.get("name")
        ),
    )


def _as_list(value: Any) -> list[str]:
    """A single string and a list of one mean the same thing to an author."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _duplicates(declarations: Sequence[Declaration]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for declaration in declarations:
        key = f"{declaration.kind.value}:{declaration.name}"
        (repeated if key in seen else seen).add(key)
    return repeated


def _line_index(text: str) -> dict[str, int]:
    """Best-effort line numbers, so declared evidence can cite the manifest.

    Approximate by design — it matches on the value rather than tracking
    positions through the parser. An evidence excerpt pointing at the wrong line
    of the manifest is a cosmetic problem; complicating the parser to avoid it
    would not be.
    """
    index: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip().lstrip("- ")
        if ":" not in stripped:
            continue
        _, _, value = stripped.partition(":")
        value = value.strip().strip("\"'")
        if value and value not in index:
            index[value] = number
            trimmed = value.rstrip("/").rsplit("/", 1)[-1]
            index.setdefault(trimmed, number)
    return index


def skeleton(manifest: Manifest, *, observed_at: int = 0) -> tuple[tuple[Node, ...], tuple[Edge, ...]]:
    """The graph a manifest implies, before anything has been discovered.

    Nodes are `PROPOSED`, not `ACTIVE`: they are declared to exist and have not
    been met. Discovery promotes them by corroborating them, and anything still
    proposed at the end of a scan is a `DECLARED_NOT_FOUND` finding.
    """
    when = observed_at or time.time_ns()
    source = manifest.source_uri or "slpie://manifest"

    nodes: list[Node] = []
    by_name: dict[str, Node] = {}
    for declaration in manifest.declarations:
        node = Node(
            kind=declaration.node_kind,
            identity=declaration.urn,
            evidence=(declaration.evidence(source_uri=source, observed_at=when),),
            properties={
                **dict(declaration.attributes),
                "declared_as": declaration.kind.value,
                "location": declaration.location,
            },
            lifecycle=LifecycleState.PROPOSED,
            observed_at=when,
        )
        nodes.append(node)
        by_name[declaration.name] = node

    boundary_nodes, edges = _boundaries(manifest, by_name, source, when)
    return (*nodes, *boundary_nodes), tuple(edges)


def _boundaries(
    manifest: Manifest,
    by_name: Mapping[str, Node],
    source: str,
    when: int,
) -> tuple[list[Node], list[Edge]]:
    """A node per boundary, and `SECURES` edges to everything it contains.

    Materialised into the graph rather than left as configuration so that a
    boundary violation becomes a *graph* question — "does a path leave this
    set?" — which the traversal already answers, instead of a separate rule
    engine reimplementing reachability.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []

    for boundary in manifest.security.boundaries:
        evidence = Evidence(
            kind=EvidenceKind.DECLARED,
            location=SourceLocation(source),
            extractor="environment.manifest",
            extractor_version="1",
            excerpt=f"boundary: {boundary.name} contains {list(boundary.contains)}",
            observed_at=when,
        )
        boundary_node = Node(
            kind=NodeKind.POLICY,
            identity=Urn.create("boundary", boundary.name),
            evidence=(evidence,),
            properties={
                "contains": list(boundary.contains),
                "classification": boundary.classification,
            },
            lifecycle=LifecycleState.ACTIVE,
            observed_at=when,
        )
        nodes.append(boundary_node)
        for name, node in by_name.items():
            if boundary.holds(name):
                edges.append(Edge(
                    kind=EdgeKind.SECURES,
                    src=boundary_node.id,
                    dst=node.id,
                    evidence=(evidence,),
                    properties={"boundary": boundary.name},
                    observed_at=when,
                ))
    return nodes, edges


def scaffold(environment: str = "my-environment") -> str:
    """A starting manifest for `slpie init`.

    Ships with `target: simulated`. The safe default is the one that cannot
    touch anything real, and moving off it is a deliberate, confirmed act.
    """
    return f"""apiVersion: slpie/v1
environment: {environment}
target: simulated            # simulated | live — the one tag

security:
  concerns: []
  secret_scanners: [entropy, patterns]
  boundaries: []

codebase:
  - root: .
    team: unassigned

data: []

network: []

web: []

iot: []

providers: []
"""
