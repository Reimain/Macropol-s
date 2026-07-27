"""Resolving (element, capability) to a connector — and the filesystem connector.

The resolver is the boundary. Above it, code asks for "a connector for the
payments repository" and gets one. Below it is the only code in the platform
that knows whether the answer will come from a materialised simulation or from a
real checkout.

:class:`FilesystemConnector` serves both sides. That is deliberate and it is the
strongest form of the guarantee: the simulator writes real files to a temp
directory and binds *this* connector to them, so the simulated path is not
merely similar to the live path — it is the same class, reading real bytes off a
real disk. The only difference is which root it was pointed at.
"""

from __future__ import annotations

import fnmatch
import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlparse

from ..environment.manifest import Declaration, Manifest, Target
from ..errors import BindingError
from .connector import Connector, ReadOnlyConnector, RefusingConnector, Resource
from .guard import Guard
from .target import TargetSelection

#: Never read into memory during discovery. A vendored binary or a checked-in
#: dataset would otherwise be loaded in full to prove it is not source code.
MAX_READ_BYTES = 8 * 1024 * 1024


class FilesystemConnector(ReadOnlyConnector):
    """Reads files under a root. Used unchanged for simulated and live targets.

    ``logical_root`` is what the manifest declared; ``physical_root`` is where
    the bytes actually are. Under a live target they are the same. Under
    simulation the physical root is a temp directory, and every uri the
    connector reports is still the logical one — so evidence gathered in the
    simulator cites the same paths it would cite against the real checkout.
    """

    capabilities = ("file-read", "directory-list", "lockfile-read", "static-analysis")

    def __init__(
        self,
        logical_root: str,
        physical_root: str | Path,
        *,
        mode: str = "simulated",
    ) -> None:
        self.logical_root = logical_root.rstrip("/")
        self.physical_root = Path(physical_root).resolve()
        self.mode = mode

    def available(self) -> bool:
        return self.physical_root.exists()

    def read(self, uri: str) -> Resource:
        path = self._resolve(uri)
        if not path.is_file():
            raise BindingError(f"no such file: {uri}")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise BindingError(
                f"{uri} is {size} bytes, over the {MAX_READ_BYTES} read limit"
            )
        content = path.read_bytes()
        return Resource(
            uri=self._logical(path),
            content=content,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            digest=hashlib.blake2b(content, digest_size=16).hexdigest(),
            size=size,
            metadata={"mode": self.mode},
        )

    def list(self, uri: str, *, pattern: str = "") -> tuple[str, ...]:
        path = self._resolve(uri)
        if not path.is_dir():
            return ()
        found = [
            self._logical(child)
            for child in sorted(path.rglob("*"))
            if child.is_file() and not self._ignored(child)
        ]
        if pattern:
            found = [uri for uri in found if fnmatch.fnmatch(uri, pattern)]
        return tuple(found)

    def _resolve(self, uri: str) -> Path:
        """Map a logical uri onto disk, refusing anything outside the root.

        The containment check is not paranoia about a hostile manifest so much
        as about a `..` that a discoverer assembled from a relative import. A
        connector that could be walked out of its root would make the simulator
        able to read the real filesystem, which would defeat the isolation the
        simulator exists to provide.
        """
        relative = self._relative(uri)
        candidate = (self.physical_root / relative).resolve()
        if candidate != self.physical_root and self.physical_root not in candidate.parents:
            raise BindingError(f"{uri} resolves outside the bound root")
        return candidate

    def _relative(self, uri: str) -> str:
        text = uri
        if text.startswith("file://"):
            text = urlparse(text).path
        if self.logical_root and text.startswith(self.logical_root):
            text = text[len(self.logical_root):]
        return text.lstrip("/") or "."

    def _logical(self, path: Path) -> str:
        relative = path.relative_to(self.physical_root).as_posix()
        return f"{self.logical_root}/{relative}" if self.logical_root else relative

    @staticmethod
    def _ignored(path: Path) -> bool:
        parts = set(path.parts)
        return bool(parts & {".git", "__pycache__", ".venv", "node_modules", ".tox"})

    def describe(self) -> Mapping[str, Any]:
        return {
            "connector": "filesystem",
            "mode": self.mode,
            "logical_root": self.logical_root,
            "capabilities": list(self.capabilities),
            "available": self.available(),
        }


@dataclass(slots=True)
class Binding:
    """One element, bound to one connector, at one target."""

    element: str
    connector: Connector
    target: Target
    declaration: Declaration | None = None

    @property
    def available(self) -> bool:
        return self.connector.available()

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element,
            "target": self.target.value,
            "available": self.available,
            "connector": dict(self.connector.describe()),
        }


class Resolver:
    """Binds declared elements to connectors, under a guard.

    Connector *factories* are registered per target, so adding a live Kubernetes
    connector later means registering one — not editing a branch that every
    caller above would then have to know about.
    """

    def __init__(self, *, guard: Guard | None = None) -> None:
        self.guard = guard or Guard()
        self._factories: dict[tuple[str, Target], Any] = {}
        self._bindings: MutableMapping[str, Binding] = {}

    def register(self, kind: str, target: Target, factory: Any) -> None:
        """Register how to build a connector for a declaration kind and target."""
        self._factories[(kind, target)] = factory

    def bind(self, declaration: Declaration, **context: Any) -> Binding:
        """Bind one element. The guard decides whether it may reach reality."""
        target = self.guard.check_binding(declaration.name)
        factory = self._factories.get((declaration.kind.value, target))

        if factory is None:
            # No connector for this kind and target. Not an error — a refusal
            # that becomes a reported gap, so the answer says what it could not
            # see rather than quietly omitting it.
            connector: Connector = RefusingConnector(
                element=declaration.name,
                reason=(
                    f"no {target.value} connector is registered for "
                    f"{declaration.kind.value} elements"
                ),
                mode=target.value,
            )
        else:
            connector = factory(declaration, target=target, **context)

        binding = Binding(
            element=declaration.name, connector=connector, target=target,
            declaration=declaration,
        )
        self._bindings[declaration.name] = binding
        return binding

    def bind_all(self, manifest: Manifest, **context: Any) -> tuple[Binding, ...]:
        return tuple(self.bind(declaration, **context) for declaration in manifest)

    def connector_for(self, element: str) -> Connector:
        binding = self._bindings.get(element)
        if binding is None:
            raise BindingError(f"{element} is not bound")
        return binding.connector

    def capability(self, element: str, capability: str) -> Connector:
        """Fetch a connector for a specific capability, checked by the guard."""
        self.guard.check_capability(element, capability)
        connector = self.connector_for(element)
        if capability not in connector.capabilities:
            raise BindingError(
                f"{element} does not offer {capability!r}; it offers "
                f"{', '.join(connector.capabilities) or 'nothing'}"
            )
        return connector

    @property
    def bindings(self) -> tuple[Binding, ...]:
        return tuple(self._bindings.values())

    @property
    def unavailable(self) -> tuple[Binding, ...]:
        """Bound but unreachable — every one of these becomes a gap."""
        return tuple(b for b in self._bindings.values() if not b.available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bindings": [b.to_dict() for b in self._bindings.values()],
            "guard": self.guard.audit(),
        }


def filesystem_factory(root: str | Path):
    """A factory binding local declarations to a filesystem root.

    The same factory serves both targets — pointed at a materialised world for
    simulation and at the real checkout for live. That the two cases differ only
    in this argument is the guarantee, expressed in code.
    """
    base = Path(root)

    def factory(declaration: Declaration, *, target: Target, **_context: Any) -> Connector:
        location = declaration.location
        if not declaration.is_local:
            return RefusingConnector(
                element=declaration.name,
                reason=f"{location} is not a filesystem location",
                mode=target.value,
            )
        physical = base / declaration.name if target is Target.SIMULATED else Path(location)
        return FilesystemConnector(
            logical_root=location.rstrip("/"),
            physical_root=physical,
            mode=target.value,
        )

    return factory
