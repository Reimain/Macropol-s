"""The Environment Manifest — a requirements file for an entire ecosystem.

A `requirements.txt` is fast to reason about because it is a *declaration of
names*, not a search. This works the same way: you list the codebases, data
folders, network links, web apps, IoT interfaces and security concerns, and the
skeleton graph exists in milliseconds — before a single file has been read.

Each declaration becomes a node carrying `DECLARED` evidence at 0.92. That is
authoritative about *intent* and says nothing about *reality*, which is the
whole design. Discovery then produces independent evidence for the same nodes,
and the delta between the two is first-class intelligence rather than an error:
a declaration nobody can find is a dead entry or a missing grant, and an element
nobody declared is a shadow dependency.

One field decides everything about how it is answered: ``target``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.identity import Urn, digest
from ..domain.node import NodeKind
from ..errors import ManifestError


class Target(str, Enum):
    """Where answers come from. The one tag that changes everything.

    Nothing above the binding layer is allowed to branch on this — the whole
    point is that the simulated and live paths are the *same* code, differing
    only in which connector answers.
    """

    SIMULATED = "simulated"
    LIVE = "live"
    HYBRID = "hybrid"      # per-element override, for a staged cutover

    @property
    def is_live(self) -> bool:
        return self is Target.LIVE

    @property
    def needs_confirmation(self) -> bool:
        """Live and hybrid touch the real world, so both are gated."""
        return self in (Target.LIVE, Target.HYBRID)


class DeclarationKind(str, Enum):
    """What a manifest section declares, and what it becomes in the graph."""

    CODEBASE = "codebase"
    DATA = "data"
    NETWORK = "network"
    WEB = "web"
    IOT = "iot"
    PROVIDER = "providers"

    @property
    def node_kind(self) -> NodeKind:
        return _NODE_KINDS[self]

    @property
    def urn_kind(self) -> str:
        return _URN_KINDS[self]


_NODE_KINDS: dict[DeclarationKind, NodeKind] = {
    DeclarationKind.CODEBASE: NodeKind.REPOSITORY,
    DeclarationKind.DATA: NodeKind.DATASET,
    DeclarationKind.NETWORK: NodeKind.API,
    DeclarationKind.WEB: NodeKind.WEB_APP,
    DeclarationKind.IOT: NodeKind.DEVICE_CLASS,
    DeclarationKind.PROVIDER: NodeKind.EXTERNAL_PROVIDER,
}

_URN_KINDS: dict[DeclarationKind, str] = {
    DeclarationKind.CODEBASE: "repository",
    DeclarationKind.DATA: "dataset",
    DeclarationKind.NETWORK: "api",
    DeclarationKind.WEB: "webapp",
    DeclarationKind.IOT: "deviceclass",
    DeclarationKind.PROVIDER: "provider",
}

#: Data declarations name a more specific node kind than "dataset".
_DATA_KINDS: dict[str, NodeKind] = {
    "database": NodeKind.DATABASE,
    "table": NodeKind.TABLE,
    "schema": NodeKind.SCHEMA,
    "dataset": NodeKind.DATASET,
    "model": NodeKind.AI_MODEL,
}

_NETWORK_KINDS: dict[str, NodeKind] = {
    "rest": NodeKind.API,
    "graphql": NodeKind.API,
    "grpc": NodeKind.API,
    "event-stream": NodeKind.QUEUE,
    "queue": NodeKind.QUEUE,
    "service": NodeKind.SERVICE,
}


@dataclass(frozen=True, slots=True)
class Declaration:
    """One declared element. The unit the platform attaches, tracks and checks."""

    kind: DeclarationKind
    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    line: int = 0

    @property
    def urn(self) -> Urn:
        return Urn.create(self.kind.urn_kind, *self.name.split("/", 1))

    @property
    def node_kind(self) -> NodeKind:
        """The specific kind where the declaration says so, the default otherwise."""
        stated = str(self.attributes.get("kind", "")).lower()
        if self.kind is DeclarationKind.DATA and stated in _DATA_KINDS:
            return _DATA_KINDS[stated]
        if self.kind is DeclarationKind.NETWORK and stated in _NETWORK_KINDS:
            return _NETWORK_KINDS[stated]
        return self.kind.node_kind

    @property
    def location(self) -> str:
        """Where this element physically is — a path, a URL, or a broker."""
        for key in ("root", "uri", "url", "folder", "broker"):
            if value := self.attributes.get(key):
                return str(value)
        return self.name

    @property
    def is_local(self) -> bool:
        """Whether it lives on the filesystem, and so can be materialised."""
        location = self.location
        return location.startswith((".", "/")) or location.startswith("file://")

    def evidence(self, *, source_uri: str, observed_at: int = 0) -> Evidence:
        """`DECLARED` evidence — authoritative about intent, silent about reality."""
        return Evidence(
            kind=EvidenceKind.DECLARED,
            location=SourceLocation(source_uri, line=self.line),
            extractor="environment.manifest",
            extractor_version="1",
            excerpt=f"{self.kind.value}: {self.name}",
            observed_at=observed_at or time.time_ns(),
            labels={"declared_kind": self.kind.value},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "name": self.name, "line": self.line,
            "urn": self.urn.to_string(), "node_kind": self.node_kind.value,
            "location": self.location, "attributes": dict(self.attributes),
        }

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.name}"


@dataclass(frozen=True, slots=True)
class Boundary:
    """A declared security boundary. Crossing it undeclared is a finding.

    This is the only part of the manifest that creates findings by its mere
    existence: everything else describes what should be there, while a boundary
    describes what must not happen.
    """

    name: str
    contains: tuple[str, ...] = ()
    classification: str = ""

    def holds(self, element: str) -> bool:
        return any(part and part in element for part in self.contains)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "contains": list(self.contains),
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class SecurityPosture:
    """What the environment is required to care about."""

    concerns: tuple[str, ...] = ()
    secret_scanners: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    boundaries: tuple[Boundary, ...] = ()

    def boundary_for(self, element: str) -> Boundary | None:
        return next((b for b in self.boundaries if b.holds(element)), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concerns": list(self.concerns),
            "secret_scanners": list(self.secret_scanners),
            "policies": list(self.policies),
            "boundaries": [b.to_dict() for b in self.boundaries],
        }


@dataclass(frozen=True, slots=True)
class Manifest:
    """A parsed, validated environment declaration."""

    environment: str
    target: Target = Target.SIMULATED
    declarations: tuple[Declaration, ...] = ()
    security: SecurityPosture = field(default_factory=SecurityPosture)
    api_version: str = "slpie/v1"
    source_uri: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """Content-addressed. A changed manifest is a different environment."""
        return digest({
            "environment": self.environment,
            "api_version": self.api_version,
            "declarations": [d.to_dict() for d in self.declarations],
            "security": self.security.to_dict(),
        })

    def of_kind(self, kind: DeclarationKind) -> tuple[Declaration, ...]:
        return tuple(d for d in self.declarations if d.kind is kind)

    def named(self, name: str) -> Declaration | None:
        return next((d for d in self.declarations if d.name == name), None)

    @property
    def local(self) -> tuple[Declaration, ...]:
        """Declarations the simulator can materialise as real files."""
        return tuple(d for d in self.declarations if d.is_local)

    def in_boundary(self, name: str) -> Boundary | None:
        return self.security.boundary_for(name)

    def with_target(self, target: Target) -> "Manifest":
        """The one tag, changed. Everything else about the manifest is identical.

        This is what "the same configuration, fired at the real environment"
        means concretely — the object the platform runs on differs in exactly
        one field.
        """
        from dataclasses import replace

        return replace(self, target=target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": self.api_version,
            "environment": self.environment,
            "target": self.target.value,
            "digest": self.digest,
            "source_uri": self.source_uri,
            "security": self.security.to_dict(),
            "declarations": [d.to_dict() for d in self.declarations],
            "metadata": dict(self.metadata),
        }

    def __len__(self) -> int:
        return len(self.declarations)

    def __iter__(self) -> Iterator[Declaration]:
        return iter(self.declarations)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<Manifest {self.environment} target={self.target.value} "
            f"{len(self.declarations)} declarations>"
        )


def declaration_name(kind: DeclarationKind, entry: Mapping[str, Any]) -> str:
    """The name a declaration is known by, preferring what the author wrote.

    An explicit `name:` always wins. Falling back to a path means the identity
    of an element changes if somebody moves the directory — acceptable for a
    codebase, which is why handover exists, but never preferred.
    """
    if name := entry.get("name"):
        return str(name)
    for key in ("root", "uri", "url", "folder", "broker"):
        if value := entry.get(key):
            text = str(value).rstrip("/")
            return text.rsplit("/", 1)[-1] or text
    raise ManifestError(f"a {kind.value} declaration has nothing to name it")
