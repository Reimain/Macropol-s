"""Spill tier: staging payloads out of memory and back.

When the budget refuses a payload, it goes to disk (or S3, or anywhere else the
connector registry can reach) as a temp object named with its id and timestamp,
under a repository the kernel controls.

Encoding is deliberately restricted to JSON and raw bytes. Pickle would spill
anything, and would also mean rehydration executes arbitrary constructors on
data the kernel merely *observed* — the exact input we should trust least. A
payload that will not encode is reported as unspillable rather than quietly made
dangerous.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..errors import StorageError
from ..ids import StorageName, iso, now_ns
from ..meta import DataShape, Wrapped
from ..storage.base import ObjectRef, ObjectStore


class _Encoder(json.JSONEncoder):
    """Encodes the value types shapes admit; refuses anything exotic."""

    def default(self, o: Any) -> Any:
        import datetime
        import decimal
        import uuid

        if isinstance(o, (datetime.datetime, datetime.date, datetime.time)):
            return o.isoformat()
        if isinstance(o, datetime.timedelta):
            return o.total_seconds()
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        if isinstance(o, (set, frozenset, tuple)):
            return list(o)
        if isinstance(o, (bytes, bytearray)):
            import base64
            return {"__bytes__": base64.b64encode(bytes(o)).decode("ascii")}
        raise TypeError(f"{type(o).__name__} is not spillable as JSON")


def _decode_hook(obj: dict[str, Any]) -> Any:
    if set(obj) == {"__bytes__"}:
        import base64
        return base64.b64decode(obj["__bytes__"])
    return obj


@dataclass(frozen=True, slots=True)
class SpillRecord:
    """A staged payload: where it went and how to bring it back."""

    payload_id: str
    entity: str
    storage: StorageName
    ref: ObjectRef
    shape: DataShape
    encoding: str
    nbytes: int
    ts_ns: int = field(default_factory=now_ns)
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.storage.key

    @property
    def at(self) -> str:
        return iso(self.ts_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload_id": self.payload_id,
            "entity": self.entity,
            "key": self.key,
            "uri": self.ref.uri,
            "encoding": self.encoding,
            "bytes": self.nbytes,
            "shape_digest": self.shape.digest,
            "at": self.at,
            "labels": dict(self.labels),
        }


class Unspillable(StorageError):
    """A payload could not be encoded for staging."""


class SpillManager:
    """Moves payloads between memory and a backing store."""

    def __init__(self, store: ObjectStore, *, kind: str = "spill") -> None:
        self.store = store
        self.kind = kind
        self._records: dict[str, SpillRecord] = {}
        self._by_entity: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        self.spilled_bytes = 0
        self.rehydrations = 0

    # -- out -------------------------------------------------------------

    def encode(self, payload: Wrapped) -> tuple[bytes, str]:
        """Serialize a payload's value, or explain why it cannot be."""
        value = payload.value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value), "bytes"
        try:
            return json.dumps(value, cls=_Encoder, separators=(",", ":")).encode("utf-8"), "json"
        except (TypeError, ValueError) as exc:
            raise Unspillable(
                f"payload {payload.name!r} cannot be staged: {exc}. "
                "Spill accepts JSON-encodable values and raw bytes only."
            ) from exc

    def spill(self, payload: Wrapped, **labels: str) -> tuple[Wrapped, SpillRecord]:
        """Stage a payload, returning the evicted wrapper and its record."""
        blob, encoding = self.encode(payload)
        name = StorageName.create(
            self.kind, payload.name, artifact_id=payload.id,
            ext="json" if encoding == "json" else "bin",
        )
        metadata = {
            "payload_id": payload.id,
            "entity": payload.name,
            "encoding": encoding,
            "shape_digest": payload.shape.digest,
            "rows": str(payload.rows),
            "source": payload.provenance.source,
            **labels,
        }
        ref = self.store.put(name.key, blob, metadata=metadata)
        record = SpillRecord(
            payload_id=payload.id, entity=payload.name, storage=name, ref=ref,
            shape=payload.shape, encoding=encoding, nbytes=len(blob), labels=labels,
        )
        with self._lock:
            self._records[payload.id] = record
            self._by_entity.setdefault(payload.name, []).append(payload.id)
            self.spilled_bytes += len(blob)
        return payload.evicted(name), record

    # -- back ------------------------------------------------------------

    def rehydrate(self, payload: Wrapped) -> Wrapped:
        """Reload a spilled payload's value."""
        if payload.resident:
            return payload
        record = self._records.get(payload.id)
        key = record.key if record else (payload.provenance.storage.key if payload.provenance.storage else "")
        if not key:
            raise Unspillable(f"payload {payload.id!r} has no staging record to rehydrate from")
        blob = self.store.get(key)
        encoding = record.encoding if record else ("json" if key.endswith(".json") else "bytes")
        value = json.loads(blob.decode("utf-8"), object_hook=_decode_hook) if encoding == "json" else blob
        with self._lock:
            self.rehydrations += 1
        return payload.rehydrated(value)

    def load(self, payload_id: str) -> Wrapped:
        """Rebuild a wrapper from a staging record alone."""
        record = self._records.get(payload_id)
        if record is None:
            raise Unspillable(f"unknown staged payload: {payload_id!r}")
        blob = self.store.get(record.key)
        value = (
            json.loads(blob.decode("utf-8"), object_hook=_decode_hook)
            if record.encoding == "json" else blob
        )
        return Wrapped.shaped(value, record.shape, id=record.payload_id)

    # -- housekeeping ----------------------------------------------------

    def record(self, payload_id: str) -> SpillRecord | None:
        return self._records.get(payload_id)

    def records(self, entity: str = "") -> list[SpillRecord]:
        if entity:
            return [self._records[i] for i in self._by_entity.get(entity, []) if i in self._records]
        return list(self._records.values())

    def drop(self, payload_id: str) -> bool:
        """Delete a staged object and forget it."""
        with self._lock:
            record = self._records.pop(payload_id, None)
        if record is None:
            return False
        self._by_entity.get(record.entity, []).remove(payload_id) if payload_id in self._by_entity.get(
            record.entity, []
        ) else None
        try:
            return bool(self.store.delete(record.key))
        except StorageError:
            return False

    def sweep(self, *, keep_per_entity: int = 3) -> list[str]:
        """Drop all but the newest staged objects per entity."""
        dropped: list[str] = []
        for entity, ids in list(self._by_entity.items()):
            ordered = sorted(
                (i for i in ids if i in self._records),
                key=lambda i: self._records[i].ts_ns, reverse=True,
            )
            for payload_id in ordered[keep_per_entity:]:
                if self.drop(payload_id):
                    dropped.append(payload_id)
        return dropped

    def report(self) -> dict[str, Any]:
        return {
            "store": getattr(self.store, "name", type(self.store).__name__),
            "staged": len(self._records),
            "spilled_bytes": self.spilled_bytes,
            "rehydrations": self.rehydrations,
            "entities": {k: len(v) for k, v in sorted(self._by_entity.items())},
        }

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SpillManager staged={len(self._records)} bytes={self.spilled_bytes}>"
