"""Self-describing data wrappers.

A wrapper carries three things together: the payload, the shape currently
believed to describe it, and the provenance that says where both came from.
Keeping them together is what lets a payload cross a hub, a sandbox boundary, a
code generator, and an agent transport without anyone having to re-derive what
it is.

Wrappers are immutable. ``reshape``, ``cast``, and ``select`` return new
wrappers with lineage pointing back at the old one, so a wrapper is itself a
small trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..ids import StorageName, digest, new_id, now_ns
from .cast import CastMode, CastReport, Caster
from .infer import DEFAULT_OPTIONS, InferenceOptions, infer_shape, infer_value
from .shapes import DataShape, FieldShape, TypeTag


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a wrapper came from and what has happened to it."""

    source: str = ""
    probe: str = ""
    event_id: str = ""
    storage: StorageName | None = None
    lineage: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)

    def descend(self, wrapper_id: str, **labels: str) -> "Provenance":
        return replace(
            self,
            lineage=self.lineage + (wrapper_id,),
            labels={**dict(self.labels), **labels},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "probe": self.probe,
            "event_id": self.event_id,
            "storage": self.storage.key if self.storage else None,
            "lineage": list(self.lineage),
            "labels": dict(self.labels),
        }


@dataclass(frozen=True, slots=True)
class Wrapped:
    """Payload + shape + provenance, travelling as one object."""

    value: Any
    shape: DataShape
    provenance: Provenance = field(default_factory=Provenance)
    id: str = field(default_factory=lambda: new_id("wrp"))
    ts_ns: int = field(default_factory=now_ns)
    resident: bool = True   # False once the payload has been spilled to storage

    # -- construction ----------------------------------------------------

    @classmethod
    def of(
        cls,
        value: Any,
        *,
        name: str = "",
        source: str = "",
        probe: str = "",
        options: InferenceOptions = DEFAULT_OPTIONS,
        **labels: str,
    ) -> "Wrapped":
        """Wrap a raw payload, inferring its shape on the way in."""
        entity = name or "payload"
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            shape = infer_shape(value, name=entity, source=source, options=options)
        else:
            shape = infer_value(value, name=entity, options=options)
            shape = replace(shape, source=source)
        return cls(
            value=value,
            shape=shape,
            provenance=Provenance(source=source, probe=probe, labels=dict(labels)),
        )

    @classmethod
    def shaped(cls, value: Any, shape: DataShape, **kwargs: Any) -> "Wrapped":
        """Wrap a payload whose shape is already known — no inference."""
        return cls(value=value, shape=shape, **kwargs)

    # -- description -----------------------------------------------------

    @property
    def name(self) -> str:
        return self.shape.name

    @property
    def symbol(self) -> str:
        return self.shape.symbol

    @property
    def digest(self) -> str:
        return digest({"shape": self.shape.digest, "value": self._value_digest()})

    def _value_digest(self) -> str:
        if isinstance(self.value, (bytes, bytearray, memoryview)):
            return digest(bytes(self.value))
        return digest(self.value)

    @property
    def rows(self) -> int:
        if isinstance(self.value, Sequence) and not isinstance(self.value, (str, bytes, bytearray)):
            return len(self.value)
        return 1

    def nbytes(self) -> int:
        """Best-effort in-memory footprint, used by the memory budget."""
        return _sizeof(self.value)

    def records(self) -> list[Mapping[str, Any]]:
        """The payload as a list of mappings, whatever it started as."""
        value = self.value
        if isinstance(value, Mapping):
            return [value]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [row if isinstance(row, Mapping) else {"value": row} for row in value]
        return [{"value": value}]

    # -- transformation (all return new wrappers) ------------------------

    def cast(self, *, mode: CastMode = CastMode.LENIENT, shape: DataShape | None = None) -> tuple["Wrapped", CastReport]:
        """Apply a shape to the payload, reporting every coercion."""
        target = shape or self.shape
        rows, report = Caster(target, mode=mode).records(self.records())
        value = rows if self.rows != 1 or isinstance(self.value, list) else rows[0]
        return (
            replace(
                self,
                value=value,
                shape=target,
                id=new_id("wrp"),
                provenance=self.provenance.descend(self.id, op="cast", mode=mode.value),
            ),
            report,
        )

    def reshape(self, shape: DataShape) -> "Wrapped":
        """Adopt a new shape without touching the payload."""
        return replace(
            self,
            shape=shape,
            id=new_id("wrp"),
            provenance=self.provenance.descend(self.id, op="reshape", to=shape.digest),
        )

    def reinfer(self, *, options: InferenceOptions = DEFAULT_OPTIONS) -> "Wrapped":
        """Re-observe the payload — used after a transformation rewrites it."""
        fresh = infer_shape(self.records(), name=self.shape.name, source=self.shape.source, options=options)
        return self.reshape(fresh)

    def select(self, names: Sequence[str]) -> "Wrapped":
        """Project a subset of fields, narrowing the shape to match."""
        keep = [n for n in names if self.shape.field(n) is not None]
        fields = tuple(f for f in self.shape.fields if f.name in set(keep))
        rows = [{k: v for k, v in row.items() if k in set(keep)} for row in self.records()]
        return replace(
            self,
            value=rows if self.rows != 1 else (rows[0] if rows else {}),
            shape=replace(self.shape, fields=fields),
            id=new_id("wrp"),
            provenance=self.provenance.descend(self.id, op="select", fields=",".join(keep)),
        )

    def map(self, fn: Callable[[Mapping[str, Any]], Mapping[str, Any]], *, reinfer: bool = True) -> "Wrapped":
        """Apply a row function, then re-observe the result by default."""
        rows = [dict(fn(row)) for row in self.records()]
        out = replace(
            self,
            value=rows if self.rows != 1 else (rows[0] if rows else {}),
            id=new_id("wrp"),
            provenance=self.provenance.descend(self.id, op="map"),
        )
        return out.reinfer() if reinfer else out

    def merged_with(self, other: "Wrapped") -> "Wrapped":
        """Union two wrappers of the same entity, widening the shape."""
        rows = self.records() + other.records()
        return replace(
            self,
            value=rows,
            shape=self.shape.merge(other.shape),
            id=new_id("wrp"),
            provenance=self.provenance.descend(self.id, op="merge", with_=other.id),
        )

    def evicted(self, storage: StorageName) -> "Wrapped":
        """Drop the payload, keeping shape and the storage key that holds it."""
        return replace(
            self,
            value=None,
            resident=False,
            provenance=replace(self.provenance, storage=storage),
        )

    def rehydrated(self, value: Any) -> "Wrapped":
        return replace(self, value=value, resident=True)

    # -- serialization ---------------------------------------------------

    def to_dict(self, *, include_value: bool = False) -> dict[str, Any]:
        out = {
            "id": self.id,
            "name": self.name,
            "symbol": self.symbol,
            "digest": self.digest,
            "rows": self.rows,
            "bytes": self.nbytes(),
            "resident": self.resident,
            "shape": self.shape.to_dict(),
            "provenance": self.provenance.to_dict(),
            "ts_ns": self.ts_ns,
        }
        if include_value:
            out["value"] = self.value
        return out

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.records())

    def __len__(self) -> int:
        return self.rows

    def __repr__(self) -> str:  # pragma: no cover - display only
        state = "resident" if self.resident else "spilled"
        return f"<Wrapped {self.name} rows={self.rows} fields={len(self.shape)} {state}>"


def _sizeof(value: Any, _seen: set[int] | None = None, _depth: int = 0) -> int:
    """Recursive size estimate that tolerates cycles and gives up gracefully."""
    import sys

    if _depth > 8:
        return 0
    seen = _seen if _seen is not None else set()
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value, 64)
    if isinstance(value, Mapping):
        for key, item in list(value.items())[:10_000]:
            size += _sizeof(key, seen, _depth + 1) + _sizeof(item, seen, _depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in list(value)[:10_000]:
            size += _sizeof(item, seen, _depth + 1)
    return size


def wrap_many(
    payloads: Mapping[str, Any],
    *,
    source: str = "",
    probe: str = "",
) -> list[Wrapped]:
    """Wrap a mapping of entity name to payload."""
    return [Wrapped.of(value, name=name, source=source, probe=probe) for name, value in payloads.items()]


__all__ = [
    "FieldShape",
    "Provenance",
    "TypeTag",
    "Wrapped",
    "wrap_many",
]
