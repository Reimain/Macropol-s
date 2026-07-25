"""The probe contract: how the kernel learns what is in front of it.

A probe answers two questions. ``sniff`` says "how confident am I that this is
mine?" — cheaply, from a name and the first few bytes. ``capture`` then actually
reads it and hands back wrapped, shaped payloads.

Confidence is a float rather than a boolean because targets lie: a ``.txt`` full
of JSON, a ``.dat`` that is really SQLite. The registry picks the most confident
probe and records the runner-up, so a wrong guess is visible in the trace.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse

from ..ids import StorageName, now_ns, slug
from ..meta import Wrapped

#: How many bytes a probe may look at during ``sniff``.
PEEK_BYTES = 4096


@dataclass(frozen=True, slots=True)
class Target:
    """Something in the environment that might contain data."""

    uri: str
    path: Path | None = None
    size: int = 0
    head: bytes = b""
    mime: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def scheme(self) -> str:
        return (urlparse(self.uri).scheme or "file").lower()

    @property
    def suffix(self) -> str:
        name = self.path.name if self.path else urlparse(self.uri).path
        return Path(name).suffix.lower()

    @property
    def stem(self) -> str:
        name = self.path.name if self.path else urlparse(self.uri).path
        return Path(name).stem or "payload"

    @property
    def entity(self) -> str:
        return slug(self.stem)

    @property
    def text_head(self) -> str:
        return self.head.decode("utf-8", "replace")

    @classmethod
    def of(cls, uri: str | Path, **labels: str) -> "Target":
        """Build a target, peeking at local files so probes can sniff content."""
        if isinstance(uri, Path) or "://" not in str(uri):
            path = Path(uri).expanduser()
            size = path.stat().st_size if path.is_file() else 0
            head = b""
            if path.is_file():
                with path.open("rb") as handle:
                    head = handle.read(PEEK_BYTES)
            mime = mimetypes.guess_type(path.name)[0] or ""
            return cls(uri=path.as_uri() if path.is_absolute() else str(path),
                       path=path, size=size, head=head, mime=mime, labels=dict(labels))
        return cls(uri=str(uri), mime=mimetypes.guess_type(str(uri))[0] or "", labels=dict(labels))

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "path": str(self.path) if self.path else None,
            "size": self.size,
            "mime": self.mime,
            "suffix": self.suffix,
            "labels": dict(self.labels),
        }


@dataclass(slots=True)
class Capture:
    """What a probe found: shaped payloads plus how it got them."""

    probe: str
    target: Target
    payloads: list[Wrapped] = field(default_factory=list)
    bytes_read: int = 0
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    labels: Mapping[str, str] = field(default_factory=dict)
    ts_ns: int = field(default_factory=now_ns)

    @property
    def ok(self) -> bool:
        return bool(self.payloads)

    @property
    def rows(self) -> int:
        return sum(p.rows for p in self.payloads)

    def storage_name(self, payload: Wrapped) -> StorageName:
        """Precision name this payload should be stored under."""
        return StorageName.create(
            "capture", payload.name, ext=self.target.suffix.lstrip(".") or "bin"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe,
            "target": self.target.to_dict(),
            "confidence": round(self.confidence, 3),
            "bytes_read": self.bytes_read,
            "rows": self.rows,
            "payloads": [p.to_dict() for p in self.payloads],
            "warnings": self.warnings,
            "labels": dict(self.labels),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Capture {self.probe} {self.target.entity} payloads={len(self.payloads)} rows={self.rows}>"


@runtime_checkable
class Probe(Protocol):
    """A reader for one family of sources."""

    name: str
    #: Extensions this probe claims, used for fast pre-filtering.
    suffixes: tuple[str, ...]

    def sniff(self, target: Target) -> float:
        """Confidence in ``[0, 1]`` that this probe can read the target."""
        ...

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        """Read the target and return shaped payloads."""
        ...


class BaseProbe:
    """Shared plumbing: suffix matching, limits, and error capture."""

    name: str = "base"
    suffixes: tuple[str, ...] = ()
    #: Rows read per payload when the caller does not specify a limit.
    default_limit: int = 5000

    def sniff(self, target: Target) -> float:  # pragma: no cover - overridden
        return 0.6 if target.suffix in self.suffixes else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- helpers for subclasses ------------------------------------------

    def _limit(self, limit: int) -> int:
        return limit if limit > 0 else self.default_limit

    def _capture(self, target: Target, **kwargs: Any) -> Capture:
        return Capture(probe=self.name, target=target, confidence=self.sniff(target), **kwargs)

    def _wrap(
        self,
        rows: Sequence[Any] | Mapping[str, Any],
        target: Target,
        *,
        name: str = "",
        **labels: str,
    ) -> Wrapped:
        return Wrapped.of(
            rows,
            name=name or target.entity,
            source=target.uri,
            probe=self.name,
            **labels,
        )

    def _read_bytes(self, target: Target, *, max_bytes: int = 0) -> bytes:
        if target.path is None:
            raise ValueError(f"{self.name} probe needs a local path, got {target.uri!r}")
        with target.path.open("rb") as handle:
            return handle.read(max_bytes) if max_bytes else handle.read()

    def _read_text(self, target: Target, *, max_bytes: int = 0, encoding: str = "utf-8") -> str:
        return self._read_bytes(target, max_bytes=max_bytes).decode(encoding, "replace")

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<{type(self).__name__} {self.name}>"


def iter_targets(root: str | Path, *, recursive: bool = True, skip_hidden: bool = True) -> Iterable[Target]:
    """Walk a directory, yielding a target per file."""
    base = Path(root).expanduser()
    if base.is_file():
        yield Target.of(base)
        return
    if not base.is_dir():
        return
    paths = base.rglob("*") if recursive else base.glob("*")
    for path in sorted(paths):
        if not path.is_file():
            continue
        if skip_hidden and any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        yield Target.of(path)
