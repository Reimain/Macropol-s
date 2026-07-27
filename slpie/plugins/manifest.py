"""Plugin manifests — what an extension declares about itself.

A plugin says which extension point it implements, which files it handles, what
kinds of observation it produces, and which evidence kinds it may claim. That
last field is a constraint rather than documentation: a plugin that declared
`name_heuristic` cannot later emit `lockfile_pin` and inherit certainty it never
earned. Confidence is derived from evidence kind, so the ability to choose your
own evidence kind is the ability to choose your own confidence — and that is
exactly what the evidence model exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import EvidenceKind
from ..errors import PluginError

#: The filename a plugin directory is recognised by.
MANIFEST_FILENAME = "slpie-plugin.json"


class ExtensionPoint(str, Enum):
    """Where a plugin can attach. Built-ins register through the same points."""

    DISCOVERER = "discoverer"          # produces observations from a source
    LINKER = "linker"                  # infers edges between existing nodes
    RULE = "rule"                      # governance
    LAYER = "layer"                    # a reasoning layer
    ARTIFACT = "artifact"              # an output format
    STORE = "store"                    # a graph or ledger backend
    EVENT_SOURCE = "event_source"      # something that notices change
    TOOL = "tool"                      # an agent-callable tool
    CAPABILITY_PROVIDER = "capability_provider"
    CONNECTOR = "connector"            # simulated and live alike


class Runtime(str, Enum):
    """How a plugin executes."""

    IN_PROCESS = "in-process"   # a Python entry point
    EXTERNAL = "external"       # a subprocess speaking JSON over stdio

    @property
    def isolated(self) -> bool:
        return self is Runtime.EXTERNAL


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """A plugin's declaration of itself."""

    id: str
    version: str = "0.0.0"
    kind: ExtensionPoint = ExtensionPoint.DISCOVERER
    runtime: Runtime = Runtime.IN_PROCESS
    entrypoint: tuple[str, ...] = ()
    handles: tuple[str, ...] = ()            # glob patterns
    produces: tuple[str, ...] = ()           # edge/observation kinds
    evidence_kinds: tuple[EvidenceKind, ...] = ()
    capabilities: tuple[str, ...] = ()       # what it needs granted
    description: str = ""
    priority: int = 100                      # lower runs first
    source: str = ""                         # where the manifest was read from

    def __post_init__(self) -> None:
        if not self.id:
            raise PluginError("a plugin must declare an id")
        if not self.entrypoint:
            raise PluginError(f"plugin {self.id!r} declares no entrypoint")

    @property
    def external(self) -> bool:
        return self.runtime is Runtime.EXTERNAL

    def may_claim(self, kind: EvidenceKind) -> bool:
        """Whether this plugin is permitted to produce this evidence kind.

        An empty declaration means "nothing claimed", and so nothing permitted.
        Defaulting to permissive here would make the field decorative.
        """
        return kind in self.evidence_kinds

    def matches(self, uri: str) -> bool:
        """Whether this plugin claims a source file."""
        from fnmatch import fnmatch

        name = uri.rsplit("/", 1)[-1]
        return any(
            fnmatch(uri, pattern) or fnmatch(name, pattern.rsplit("/", 1)[-1])
            for pattern in self.handles
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "") -> "PluginManifest":
        try:
            kinds = tuple(
                EvidenceKind(value) for value in data.get("evidence_kinds", ())
            )
        except ValueError as error:
            raise PluginError(
                f"plugin {data.get('id', '?')!r} declares an unknown evidence kind: {error}"
            ) from None

        entrypoint = data.get("entrypoint", ())
        if isinstance(entrypoint, str):
            entrypoint = (entrypoint,)

        try:
            return cls(
                id=str(data.get("id", "")),
                version=str(data.get("version", "0.0.0")),
                kind=ExtensionPoint(data.get("kind", "discoverer")),
                runtime=Runtime(data.get("runtime", "in-process")),
                entrypoint=tuple(str(part) for part in entrypoint),
                handles=tuple(str(part) for part in data.get("handles", ())),
                produces=tuple(str(part) for part in data.get("produces", ())),
                evidence_kinds=kinds,
                capabilities=tuple(str(part) for part in data.get("capabilities", ())),
                description=str(data.get("description", "")),
                priority=int(data.get("priority", 100)),
                source=source,
            )
        except ValueError as error:
            raise PluginError(f"invalid plugin manifest: {error}") from None

    @classmethod
    def load(cls, path: str | Path) -> "PluginManifest":
        location = Path(path)
        if location.is_dir():
            location = location / MANIFEST_FILENAME
        if not location.is_file():
            raise PluginError(f"no plugin manifest at {location}")
        try:
            data = json.loads(location.read_text(encoding="utf-8"))
        except ValueError as error:
            raise PluginError(f"{location} is not valid JSON: {error}") from None
        return cls.from_dict(data, source=str(location.parent))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "kind": self.kind.value,
            "runtime": self.runtime.value, "entrypoint": list(self.entrypoint),
            "handles": list(self.handles), "produces": list(self.produces),
            "evidence_kinds": [kind.value for kind in self.evidence_kinds],
            "capabilities": list(self.capabilities),
            "description": self.description, "priority": self.priority,
            "source": self.source,
        }

    def __str__(self) -> str:
        return f"{self.id}@{self.version} ({self.kind.value}, {self.runtime.value})"
