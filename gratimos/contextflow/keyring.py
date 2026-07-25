"""The meta keyring: the addressable surface of the context.

A keyring path is a dotted address like ``capture.orders.csv`` or
``shape.orders``. Each path holds the *latest* entry produced for it, together
with the precision storage name and the event that produced it. Code generators
read this structure and reflect it into symbols, so path naming is the single
source of truth for what generated code is called.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..ids import StorageName, slug
from .event import EventKind, KeyringEvent

_PATH_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*(?:\.[a-z0-9]+(?:_[a-z0-9]+)*)*$")


def normalize_path(path: str) -> str:
    """Coerce any label into a canonical dotted keyring path."""
    parts = [slug(p) for p in str(path).split(".") if str(p).strip()]
    if not parts:
        raise ValueError("keyring path cannot be empty")
    return ".".join(parts)


def is_valid_path(path: str) -> bool:
    return bool(_PATH_RE.match(path))


@dataclass(frozen=True, slots=True)
class KeyEntry:
    """What the keyring knows about one path at one point in history."""

    path: str
    kind: EventKind
    event_id: str
    version: str
    storage: StorageName | None = None
    shape_digest: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1

    @property
    def symbol(self) -> str:
        """Stable identifier a generator should emit for this path."""
        if self.storage is not None:
            return self.storage.symbol
        return "".join(part.title().replace("_", "") for part in self.path.split("."))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "event_id": self.event_id,
            "version": self.version,
            "storage": self.storage.key if self.storage else None,
            "shape_digest": self.shape_digest,
            "labels": dict(self.labels),
            "payload": dict(self.payload),
            "revision": self.revision,
            "symbol": self.symbol,
        }


class MetaKeyring:
    """Materialized head state of a ContextFlow, keyed by path.

    The keyring is derived state: it is always rebuilt by replaying events, so a
    rollback simply replays fewer of them. Nothing here is authoritative on its
    own.
    """

    def __init__(self) -> None:
        self._entries: dict[str, KeyEntry] = {}
        self._history: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    # -- mutation (replay only) ------------------------------------------

    def apply(self, event: KeyringEvent, version: str) -> None:
        """Fold one event into head state. Called by ContextFlow during replay."""
        if event.inert:
            return
        with self._lock:
            for raw in event.paths:
                path = normalize_path(raw)
                previous = self._entries.get(path)
                self._entries[path] = KeyEntry(
                    path=path,
                    kind=event.kind,
                    event_id=event.id,
                    version=version,
                    storage=event.storage,
                    shape_digest=str(event.payload.get("shape_digest", "")),
                    labels=dict(event.labels),
                    payload=dict(event.payload),
                    revision=(previous.revision + 1) if previous else 1,
                )
                self._history.setdefault(path, []).append(event.id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._history.clear()

    # -- reads -----------------------------------------------------------

    def get(self, path: str) -> KeyEntry | None:
        return self._entries.get(normalize_path(path))

    def require(self, path: str) -> KeyEntry:
        entry = self.get(path)
        if entry is None:
            raise KeyError(f"keyring path not present: {path!r}")
        return entry

    def under(self, prefix: str) -> list[KeyEntry]:
        """All entries at or below a prefix, in path order."""
        root = normalize_path(prefix)
        return [
            entry
            for path, entry in sorted(self._entries.items())
            if path == root or path.startswith(root + ".")
        ]

    def of_kind(self, kind: EventKind) -> list[KeyEntry]:
        return [e for _, e in sorted(self._entries.items()) if e.kind is kind]

    def history(self, path: str) -> list[str]:
        return list(self._history.get(normalize_path(path), ()))

    def paths(self) -> list[str]:
        return sorted(self._entries)

    def to_dict(self) -> dict[str, Any]:
        return {path: entry.to_dict() for path, entry in sorted(self._entries.items())}

    def __contains__(self, path: object) -> bool:
        try:
            return normalize_path(str(path)) in self._entries
        except ValueError:
            return False

    def __iter__(self) -> Iterator[KeyEntry]:
        return iter(entry for _, entry in sorted(self._entries.items()))

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<MetaKeyring paths={len(self._entries)}>"
