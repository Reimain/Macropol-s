"""The object store contract.

Everything that persists bytes — the spill tier, the raw capture repository, a
Delta table landing zone — implements this. Keeping the surface this small is
what lets a policymaker swap local disk for S3 without any caller noticing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable

from ..ids import StorageName, digest, iso, now_ns


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A handle to stored bytes: where it is, how big, and what it hashes to."""

    key: str
    size: int
    content_digest: str
    store: str = ""
    uri: str = ""
    ts_ns: int = field(default_factory=now_ns)
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def at(self) -> str:
        return iso(self.ts_ns)

    @property
    def name(self) -> StorageName | None:
        """Parse the key back into a precision name when it is one of ours."""
        try:
            return StorageName.parse(self.key)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "size": self.size,
            "digest": self.content_digest,
            "store": self.store,
            "uri": self.uri,
            "at": self.at,
            "metadata": dict(self.metadata),
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.uri or self.key


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal, synchronous blob interface."""

    name: str

    def put(self, key: str, data: bytes, *, metadata: Mapping[str, str] | None = None) -> ObjectRef: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def stat(self, key: str) -> ObjectRef: ...

    def list(self, prefix: str = "") -> Iterator[ObjectRef]: ...


class MemoryStore:
    """In-process store. Useful for tests and for ephemeral staging."""

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self._blobs: dict[str, tuple[bytes, dict[str, str], int]] = {}

    def put(self, key: str, data: bytes, *, metadata: Mapping[str, str] | None = None) -> ObjectRef:
        payload = bytes(data)
        self._blobs[key] = (payload, dict(metadata or {}), now_ns())
        return self._ref(key)

    def get(self, key: str) -> bytes:
        from ..errors import StorageError

        if key not in self._blobs:
            raise StorageError(f"no such object: {key!r}")
        return self._blobs[key][0]

    def exists(self, key: str) -> bool:
        return key in self._blobs

    def delete(self, key: str) -> bool:
        return self._blobs.pop(key, None) is not None

    def stat(self, key: str) -> ObjectRef:
        from ..errors import StorageError

        if key not in self._blobs:
            raise StorageError(f"no such object: {key!r}")
        return self._ref(key)

    def list(self, prefix: str = "") -> Iterator[ObjectRef]:
        for key in sorted(self._blobs):
            if key.startswith(prefix):
                yield self._ref(key)

    def total_bytes(self) -> int:
        return sum(len(blob) for blob, _, _ in self._blobs.values())

    def _ref(self, key: str) -> ObjectRef:
        payload, metadata, ts = self._blobs[key]
        return ObjectRef(
            key=key,
            size=len(payload),
            content_digest=digest(payload),
            store=self.name,
            uri=f"mem://{self.name}/{key}",
            ts_ns=ts,
            metadata=metadata,
        )

    def __len__(self) -> int:
        return len(self._blobs)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<MemoryStore {self.name} objects={len(self._blobs)} bytes={self.total_bytes()}>"
