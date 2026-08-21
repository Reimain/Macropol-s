"""Identity, time, and precision storage naming.

Every artifact the kernel touches gets an id that sorts by creation time and a
storage name that encodes *when* and *what* it is. Naming is deliberately
mechanical: code generators reflect these names back into symbols, so they must
be stable, filesystem-safe, and reversible.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass

from .errors import StorageError

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I/L/O/U
_lock = threading.Lock()
_last_ms = 0
_seq = 0

_SAFE = re.compile(r"[^0-9a-zA-Z]+")


def now_ns() -> int:
    """Wall clock in nanoseconds. Used for storage names, never for ordering."""
    return int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp() * 1_000_000_000)


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def iso(ts_ns: int | None = None) -> str:
    ts = _dt.datetime.fromtimestamp((ts_ns or now_ns()) / 1e9, tz=_dt.timezone.utc)
    return ts.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _b32(value: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid() -> str:
    """Monotonic, lexicographically sortable 26-char id (ULID layout)."""
    global _last_ms, _seq
    with _lock:
        ms = now_ns() // 1_000_000
        if ms == _last_ms:
            _seq += 1
        else:
            _last_ms, _seq = ms, int.from_bytes(os.urandom(5), "big")
        rand = (_seq & ((1 << 80) - 1)) ^ int.from_bytes(os.urandom(10), "big") >> 16
        return _b32(ms, 10) + _b32(rand, 16)


def new_id(prefix: str) -> str:
    """`evt_01J2...` — prefix makes ids self-describing in traces and logs."""
    return f"{slug(prefix)}_{ulid()}"


def slug(text: str, *, max_len: int = 48) -> str:
    """Filesystem- and identifier-safe token, lowercase, `_` separated."""
    cleaned = _SAFE.sub("_", str(text)).strip("_").lower()
    return (cleaned[:max_len] or "x").strip("_") or "x"


def digest(payload: object, *, length: int = 16) -> str:
    """Content digest that is stable across processes for JSON-like payloads."""
    if isinstance(payload, (bytes, bytearray, memoryview)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=length).hexdigest()


def _canonical(obj: object) -> object:
    """Best-effort canonical form so digests survive non-JSON payloads."""
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_canonical(v) for v in obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    if hasattr(obj, "__dict__"):
        return _canonical({k: v for k, v in vars(obj).items() if not k.startswith("_")})
    return repr(obj)


@dataclass(frozen=True, slots=True)
class StorageName:
    """A precision name: what it is, when it happened, and where it lands.

    The layout is time-partitioned so cheap object stores can prune by prefix
    and so code generators can derive a stable symbol from the same tuple.
    """

    kind: str
    entity: str
    artifact_id: str
    ts_ns: int
    ext: str = "bin"

    @classmethod
    def create(cls, kind: str, entity: str, *, artifact_id: str = "", ext: str = "bin") -> "StorageName":
        return cls(
            kind=slug(kind),
            entity=slug(entity),
            artifact_id=artifact_id or new_id(kind[:3] or "art"),
            ts_ns=now_ns(),
            ext=slug(ext, max_len=12),
        )

    @property
    def filename(self) -> str:
        return f"{self.entity}__{self.artifact_id}__{self.ts_ns}.{self.ext}"

    @property
    def key(self) -> str:
        d = _dt.datetime.fromtimestamp(self.ts_ns / 1e9, tz=_dt.timezone.utc)
        return f"{self.kind}/{d:%Y/%m/%d/%H}/{self.filename}"

    @property
    def symbol(self) -> str:
        """The identifier a code generator should reflect this name as."""
        parts = [p for p in (self.kind, self.entity) if p]
        return "".join(p.title().replace("_", "") for p in parts) or "Artifact"

    @classmethod
    def parse(cls, key: str) -> "StorageName":
        try:
            kind, _y, _m, _d, _h, filename = key.split("/")
            stem, _, ext = filename.rpartition(".")
            entity, artifact_id, ts = stem.split("__")
            return cls(kind=kind, entity=entity, artifact_id=artifact_id, ts_ns=int(ts), ext=ext)
        except (ValueError, TypeError) as exc:  # pragma: no cover - defensive
            raise StorageError(f"not a gratimos storage key: {key!r}") from exc

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.key
