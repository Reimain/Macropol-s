"""SBOM emission — CycloneDX 1.5 and SPDX 2.3, straight off the graph.

This module is the payoff for a decision made all the way back in
:mod:`slpie.domain.identity`: packages are identified by **purl**, the same
identifier CycloneDX, SPDX and OSV already speak. So component identity is not
translated here, it is *passed through*. `bom-ref` is the purl. `externalRefs`
is the purl. There is no mapping table to drift, and an advisory keyed on a purl
matches the graph node keyed on the same purl by string equality.

Two rules the emitters hold to:

* **Nothing is invented.** A licence appears only when a node carries one, a
  hash appears only when evidence supplied one, and a version appears only when
  the identity has one. Absent facts are absent, not `NOASSERTION`-flavoured
  guesses — except where the format itself requires the field, in which case the
  literal `NOASSERTION` is what the specification means by "we did not check".
* **Nothing reads the clock.** The document timestamp is an argument. An SBOM
  that changes every time you generate it cannot be diffed, cannot be attested,
  and cannot be checked into a release. The serial number is derived from the
  document's own content, so identical graphs produce identical documents.

The dependency graph is emitted as a closed set: every `dependsOn` reference
resolves to a component in the same document. A reference that dangles makes a
consuming tool either error or silently drop the edge, and both outcomes are
worse than omitting the edge here where the omission is deliberate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..domain.edge import EdgeKind
from ..domain.evidence import Evidence
from ..domain.identity import Purl, digest
from ..domain.license import LicenseExpression, normalize
from ..domain.node import Node, NodeKind
from ..errors import ArtifactError
from ..graph.store import GraphView

__all__ = [
    "SbomDocument",
    "SbomOptions",
    "cyclonedx_document",
    "spdx_document",
    "write_sbom",
]

#: Node kinds that are components of a software bill of materials. A team or a
#: policy is real architecture but it is not something you ship.
COMPONENT_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.PACKAGE,
    NodeKind.MODULE,
    NodeKind.COMPONENT,
    NodeKind.SERVICE,
    NodeKind.WEB_APP,
    NodeKind.AI_MODEL,
    NodeKind.DEVICE_CLASS,
    NodeKind.EXTERNAL_PROVIDER,
})

#: SLPIE node kind -> CycloneDX component type (the 1.5 enumeration).
_CYCLONEDX_TYPE: dict[NodeKind, str] = {
    NodeKind.PACKAGE: "library",
    NodeKind.MODULE: "library",
    NodeKind.COMPONENT: "library",
    NodeKind.SERVICE: "application",
    NodeKind.WEB_APP: "application",
    NodeKind.AI_MODEL: "machine-learning-model",
    NodeKind.DEVICE_CLASS: "device",
    NodeKind.EXTERNAL_PROVIDER: "application",
}

#: Edge kinds that mean "this component needs that one at build or run time".
DEPENDENCY_KINDS: frozenset[EdgeKind] = frozenset({
    EdgeKind.DEPENDS_ON,
    EdgeKind.IMPORTS,
})

#: Hash algorithm spellings CycloneDX 1.5 accepts, keyed by a lowercased alias.
_HASH_ALGORITHMS: dict[str, str] = {
    "md5": "MD5",
    "sha1": "SHA-1", "sha-1": "SHA-1",
    "sha256": "SHA-256", "sha-256": "SHA-256",
    "sha384": "SHA-384", "sha-384": "SHA-384",
    "sha512": "SHA-512", "sha-512": "SHA-512",
    "sha3-256": "SHA3-256", "sha3-384": "SHA3-384", "sha3-512": "SHA3-512",
    "blake2b-256": "BLAKE2b-256", "blake2b-384": "BLAKE2b-384",
    "blake2b-512": "BLAKE2b-512", "blake3": "BLAKE3",
}


@dataclass(frozen=True, slots=True)
class SbomOptions:
    """Everything the emitters would otherwise have to invent."""

    #: Document timestamp, epoch seconds. Supplied, never read from the clock.
    timestamp: int = 0
    #: The thing the SBOM is *about* — becomes `metadata.component`.
    subject: str = ""
    subject_version: str = ""
    document_name: str = "slpie-sbom"
    namespace: str = "https://slpie.invalid/sbom"
    tool_vendor: str = "SLPIE"
    tool_name: str = "slpie"
    tool_version: str = "0.1.0"

    @property
    def iso_timestamp(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )


DEFAULT_OPTIONS = SbomOptions()


@dataclass(frozen=True, slots=True)
class SbomDocument:
    """An emitted bill of materials, with the format it claims to be."""

    format: str
    spec_version: str
    document: dict[str, Any]
    components: int
    relationships: int

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, so two identical graphs diff to nothing."""
        return json.dumps(self.document, indent=2, sort_keys=True) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "spec_version": self.spec_version,
            "components": self.components,
            "relationships": self.relationships,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SbomDocument {self.format} {self.spec_version} components={self.components}>"


# --- shared selection ----------------------------------------------------


def _components(graph: GraphView) -> tuple[Node, ...]:
    """Every live component node, in a stable order.

    Sorted by the *identity string*, which is the purl — the same key the
    document is written under. Sorting by display name would reorder the
    document whenever two packages shared a name.
    """
    selected = [
        node
        for kind in sorted(COMPONENT_KINDS, key=lambda k: k.value)
        for node in graph.nodes(kind=kind, live=True)
    ]
    return tuple(sorted(selected, key=lambda n: (n.identity.to_string(), n.id)))


def _ref(node: Node) -> str:
    """The document-local reference for a node: its purl, verbatim."""
    return node.identity.to_string()


def _dependencies(
    graph: GraphView, nodes: Sequence[Node]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(ref, refs it depends on) for every component, closed over the document."""
    by_id = {node.id: node for node in nodes}
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in nodes:
        targets: set[str] = set()
        for kind in sorted(DEPENDENCY_KINDS, key=lambda k: k.value):
            for edge in graph.edges_from(node.id, kind=kind, live=True):
                target = by_id.get(edge.dst)
                if target is not None:
                    targets.add(_ref(target))
        out.append((_ref(node), tuple(sorted(targets))))
    return tuple(out)


def _license_expression(node: Node) -> LicenseExpression | None:
    raw = str(node.properties.get("license", "") or "")
    return normalize(raw) if raw.strip() else None


def _hashes(node: Node, evidence: Iterable[Evidence]) -> tuple[tuple[str, str], ...]:
    """(algorithm, content) pairs, only where something actually supplied one.

    Two sources are honoured, in this order: an explicit ``hashes`` property on
    the node (a mapping of algorithm to digest, which is what a lockfile
    integrity field becomes), and evidence labelled with the algorithm it
    digested under. Evidence with a ``content_digest`` but no stated algorithm
    is skipped — writing a digest under a guessed algorithm produces an SBOM
    that fails verification for a reason nobody can find.
    """
    found: dict[str, str] = {}
    declared = node.properties.get("hashes")
    if isinstance(declared, Mapping):
        for algorithm, content in declared.items():
            canonical = _HASH_ALGORITHMS.get(str(algorithm).strip().lower())
            if canonical and str(content).strip():
                found.setdefault(canonical, str(content).strip())
    for item in evidence:
        algorithm = str(item.labels.get("hash_alg", "")).strip().lower()
        canonical = _HASH_ALGORITHMS.get(algorithm)
        if canonical and item.content_digest:
            found.setdefault(canonical, item.content_digest)
    return tuple(sorted(found.items()))


def _serial_number(payload: Mapping[str, Any]) -> str:
    """A content-derived URN UUID — same graph in, same serial out."""
    raw = digest(payload, size=16)
    return (
        f"urn:uuid:{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    )


# --- CycloneDX 1.5 -------------------------------------------------------


def cyclonedx_document(
    graph: GraphView, *, options: SbomOptions = DEFAULT_OPTIONS
) -> SbomDocument:
    """Emit a CycloneDX 1.5 JSON bill of materials from the graph."""
    nodes = _components(graph)
    if not nodes:
        raise ArtifactError("no component nodes in the graph; there is no SBOM to emit")

    components: list[dict[str, Any]] = []
    for node in nodes:
        identity = node.identity
        component: dict[str, Any] = {
            "type": _CYCLONEDX_TYPE.get(node.kind, "library"),
            "bom-ref": _ref(node),
            "name": node.name,
        }
        if isinstance(identity, Purl):
            if identity.namespace:
                component["group"] = identity.namespace
            if identity.version:
                component["version"] = identity.version
            component["purl"] = identity.to_string()
        else:
            # A non-package element still belongs in the BOM. CycloneDX has no
            # slot for a bespoke identifier scheme, so the SLPIE URN travels as
            # the bom-ref (above) and as a property, rather than being bent into
            # a purl it is not.
            component["group"] = node.identity.namespace or ""
            if not component["group"]:
                del component["group"]
        expression = _license_expression(node)
        if expression is not None:
            component["licenses"] = _cyclonedx_licenses(expression)
        hashes = _hashes(node, graph.evidence_for(node.id) or node.evidence)
        if hashes:
            component["hashes"] = [
                {"alg": algorithm, "content": content} for algorithm, content in hashes
            ]
        if node.properties.get("description"):
            component["description"] = str(node.properties["description"])
        component["properties"] = [
            {"name": "slpie:lifecycle", "value": node.lifecycle.value},
            {"name": "slpie:node-id", "value": node.id},
            {"name": "slpie:validation", "value": node.validation.value},
        ]
        components.append(component)

    dependencies = [
        {"ref": ref, "dependsOn": list(targets)}
        for ref, targets in _dependencies(graph, nodes)
    ]

    metadata: dict[str, Any] = {
        "tools": {
            "components": [
                {
                    "type": "application",
                    "name": options.tool_name,
                    "version": options.tool_version,
                    "publisher": options.tool_vendor,
                }
            ]
        }
    }
    if options.timestamp:
        metadata["timestamp"] = options.iso_timestamp
    if options.subject:
        subject_ref = f"{options.namespace}#{options.subject}"
        metadata["component"] = {
            "type": "application",
            "bom-ref": subject_ref,
            "name": options.subject,
            **({"version": options.subject_version} if options.subject_version else {}),
        }
        # The subject is a node of the dependency graph too, or consuming tools
        # cannot tell which components are direct.
        roots = _direct_dependencies(graph, nodes)
        dependencies.insert(0, {"ref": subject_ref, "dependsOn": list(roots)})

    document: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": dependencies,
    }
    document["serialNumber"] = _serial_number(document)
    return SbomDocument(
        format="CycloneDX",
        spec_version="1.5",
        document=document,
        components=len(components),
        relationships=sum(len(d["dependsOn"]) for d in dependencies),
    )


def _cyclonedx_licenses(expression: LicenseExpression) -> list[dict[str, Any]]:
    """Single known identifier becomes `id`; anything else stays an expression.

    Flattening `MIT OR Apache-2.0` to one identifier would silently discard the
    choice, which is the part a compliance reviewer actually needs.
    """
    terms = expression.terms
    if len(terms) == 1 and not expression.is_choice:
        term = terms[0]
        if term.known and not term.exception:
            return [{"license": {"id": term.identifier}}]
        return [{"license": {"name": term.to_string()}}]
    return [{"expression": expression.to_string()}]


def _direct_dependencies(graph: GraphView, nodes: Sequence[Node]) -> tuple[str, ...]:
    """Components nothing else in the document depends on — the document roots."""
    refs = {_ref(node) for node in nodes}
    depended_on: set[str] = set()
    for _ref_from, targets in _dependencies(graph, nodes):
        depended_on.update(targets)
    roots = sorted(refs - depended_on)
    # A cyclic graph can leave no root at all; then every component is direct,
    # which is true and, more importantly, keeps every ref resolvable.
    return tuple(roots) if roots else tuple(sorted(refs))


# --- SPDX 2.3 ------------------------------------------------------------


def _spdx_id(node: Node) -> str:
    """`SPDXRef-Package-<node id>` — the node id is already hex, already stable."""
    return f"SPDXRef-Package-{node.id}"


def spdx_document(
    graph: GraphView, *, options: SbomOptions = DEFAULT_OPTIONS
) -> SbomDocument:
    """Emit an SPDX 2.3 JSON document from the graph."""
    nodes = _components(graph)
    if not nodes:
        raise ArtifactError("no component nodes in the graph; there is no SBOM to emit")

    packages: list[dict[str, Any]] = []
    for node in nodes:
        expression = _license_expression(node)
        package: dict[str, Any] = {
            "SPDXID": _spdx_id(node),
            "name": node.name,
            "downloadLocation": str(node.properties.get("download_location", "NOASSERTION")),
            "filesAnalyzed": False,
            # SPDX distinguishes what the supplier *declared* from what an
            # analysis *concluded*. SLPIE observes declarations; it does not run
            # a licence scanner, so it must not claim a conclusion.
            "licenseDeclared": expression.to_string() if expression else "NOASSERTION",
            "licenseConcluded": "NOASSERTION",
            "copyrightText": str(node.properties.get("copyright", "NOASSERTION")),
        }
        if node.version:
            package["versionInfo"] = node.version
        if isinstance(node.identity, Purl):
            package["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": node.identity.to_string(),
                }
            ]
        hashes = _hashes(node, graph.evidence_for(node.id) or node.evidence)
        if hashes:
            package["checksums"] = [
                {"algorithm": algorithm.replace("-", "").upper(), "checksumValue": content}
                for algorithm, content in hashes
            ]
        packages.append(package)

    by_ref = {_ref(node): _spdx_id(node) for node in nodes}
    relationships: list[dict[str, str]] = []
    for ref, targets in _dependencies(graph, nodes):
        for target in targets:
            relationships.append(
                {
                    "spdxElementId": by_ref[ref],
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": by_ref[target],
                }
            )
    for package in packages:
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": package["SPDXID"],
            }
        )

    document: dict[str, Any] = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": options.document_name,
        "creationInfo": {
            "created": options.iso_timestamp,
            "creators": [
                f"Tool: {options.tool_name}-{options.tool_version}",
                f"Organization: {options.tool_vendor}",
            ],
        },
        "packages": packages,
        "relationships": relationships,
    }
    document["documentNamespace"] = (
        f"{options.namespace}/{options.document_name}/"
        f"{digest(document, size=16)}"
    )
    return SbomDocument(
        format="SPDX",
        spec_version="SPDX-2.3",
        document=document,
        components=len(packages),
        relationships=len(relationships),
    )


def write_sbom(document: SbomDocument, path: Any) -> Any:
    """Write a document as canonical JSON. Returns the path for chaining."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document.to_json(), encoding="utf-8")
    return target
