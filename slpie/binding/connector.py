"""The Connector protocol — byte-identical on both sides of the binding.

This interface is the whole reason "the same configuration, fired at the real
environment" is a true statement rather than a slogan. A discoverer asks a
connector to read a file, list a directory, or fetch a contract. It never learns
which kind of connector answered, because the answer has the same type and the
same shape either way.

Which is also why the simulator materialises *real* artifacts on a temp
filesystem instead of returning canned responses: if the simulated connector
returned something the live one could not, the two paths would have diverged and
a green simulator case would stop being evidence about the real code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from ..errors import BindingError


@dataclass(frozen=True, slots=True)
class Resource:
    """One thing a connector can hand back, and where it came from.

    ``uri`` is always the *logical* address — the path as the manifest declared
    it — even when the bytes came from a materialised temp directory. Evidence
    cites this uri, so a finding proved in the simulator names the same file it
    would name against the real checkout.
    """

    uri: str
    content: bytes = b""
    media_type: str = ""
    digest: str = ""
    size: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri, "media_type": self.media_type, "digest": self.digest,
            "size": self.size or len(self.content), "metadata": dict(self.metadata),
        }

    def __len__(self) -> int:
        return self.size or len(self.content)


@runtime_checkable
class Connector(Protocol):
    """How the platform reaches an element. Identical shape, simulated or live."""

    #: What this connector can do. The station negotiates against these.
    capabilities: tuple[str, ...]

    #: `simulated` or `live` — for reporting, never for branching above here.
    mode: str

    def available(self) -> bool:
        """Whether the element can be reached at all right now."""
        ...

    def read(self, uri: str) -> Resource:
        """Fetch one resource. Raises :class:`BindingError` when it cannot."""
        ...

    def list(self, uri: str, *, pattern: str = "") -> tuple[str, ...]:
        """Enumerate what lives under a location."""
        ...

    def exists(self, uri: str) -> bool: ...

    def describe(self) -> Mapping[str, Any]:
        """Identity and state, for the Station view."""
        ...


class ReadOnlyConnector:
    """Base for connectors, refusing every write.

    SLPIE is read-only against a live target by default. Writing is a separate,
    separately granted capability — inheriting the refusal means a new connector
    is safe before its author has thought about it, rather than after.
    """

    capabilities: tuple[str, ...] = ()
    mode: str = "simulated"

    def write(self, uri: str, content: bytes) -> None:
        raise BindingError(
            f"{type(self).__name__} is read-only; writing to {uri} requires a separate grant"
        )

    def delete(self, uri: str) -> None:
        raise BindingError(f"{type(self).__name__} is read-only; cannot delete {uri}")

    def exists(self, uri: str) -> bool:
        try:
            self.read(uri)  # type: ignore[attr-defined]
            return True
        except BindingError:
            return False

    def describe(self) -> Mapping[str, Any]:
        return {
            "connector": type(self).__name__,
            "mode": self.mode,
            "capabilities": list(self.capabilities),
            "available": self.available(),  # type: ignore[attr-defined]
        }


@dataclass(slots=True)
class RefusingConnector(ReadOnlyConnector):
    """A connector for something that will not let us in.

    A refusal is a first-class outcome, not an exception to swallow. This exists
    so a refused element still *has* a connector — one that reports why it
    cannot answer — instead of being silently absent from the scan.
    """

    element: str = ""
    reason: str = "no connector is bound for this element"
    mode: str = "simulated"
    capabilities: tuple[str, ...] = ()

    def available(self) -> bool:
        return False

    def read(self, uri: str) -> Resource:
        raise BindingError(f"cannot read {uri}: {self.reason}")

    def list(self, uri: str, *, pattern: str = "") -> tuple[str, ...]:
        return ()

    def exists(self, uri: str) -> bool:
        return False

    def describe(self) -> Mapping[str, Any]:
        return {
            "connector": "refusing", "element": self.element, "mode": self.mode,
            "capabilities": [], "available": False, "reason": self.reason,
        }
