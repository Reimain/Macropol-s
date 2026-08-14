"""Shapes: the kernel's description of what data *is*.

A shape is not a schema you author — it is a shape you *observe*, revise as more
data arrives, and reconcile when observations disagree. Everything downstream is
derived from it: generated code, storage layout, routing decisions, migrations.

The type lattice is what makes revision safe. Merging two observations never
loses information; it widens to the smallest type that admits both, and `JSON`
is the top of the lattice where structure genuinely disagrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import ShapeError
from ..ids import digest, slug

_KEYWORDS = frozenset(
    """False None True and as assert async await break class continue def del elif else except
    finally for from global if import in is lambda nonlocal not or pass raise return try while
    with yield""".split()
)


class TypeTag(str, Enum):
    """Portable type vocabulary. Backends map these onto their own systems."""

    NULL = "null"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    DECIMAL = "decimal"
    STRING = "string"
    BYTES = "bytes"
    DATE = "date"
    DATETIME = "datetime"
    DURATION = "duration"
    UUID = "uuid"
    LIST = "list"
    STRUCT = "struct"
    MAP = "map"
    MEDIA = "media"
    JSON = "json"

    @property
    def scalar(self) -> bool:
        return self not in {TypeTag.LIST, TypeTag.STRUCT, TypeTag.MAP, TypeTag.JSON}


#: Widening chains: later members admit every earlier member without loss.
_NUMERIC = (TypeTag.BOOL, TypeTag.INT, TypeTag.FLOAT, TypeTag.DECIMAL)
_TEMPORAL = (TypeTag.DATE, TypeTag.DATETIME)
_STRINGABLE = frozenset(
    {TypeTag.BOOL, TypeTag.INT, TypeTag.FLOAT, TypeTag.DECIMAL, TypeTag.UUID,
     TypeTag.DATE, TypeTag.DATETIME, TypeTag.DURATION, TypeTag.STRING}
)

PYTHON_TYPES: dict[TypeTag, str] = {
    TypeTag.NULL: "None",
    TypeTag.BOOL: "bool",
    TypeTag.INT: "int",
    TypeTag.FLOAT: "float",
    TypeTag.DECIMAL: "decimal.Decimal",
    TypeTag.STRING: "str",
    TypeTag.BYTES: "bytes",
    TypeTag.DATE: "datetime.date",
    TypeTag.DATETIME: "datetime.datetime",
    TypeTag.DURATION: "datetime.timedelta",
    TypeTag.UUID: "uuid.UUID",
    TypeTag.LIST: "list",
    TypeTag.STRUCT: "dict",
    TypeTag.MAP: "dict",
    TypeTag.MEDIA: "bytes",
    TypeTag.JSON: "typing.Any",
}


def promote(left: TypeTag, right: TypeTag) -> TypeTag:
    """Smallest tag that admits both observations."""
    if left is right:
        return left
    if left is TypeTag.NULL:
        return right
    if right is TypeTag.NULL:
        return left
    for chain in (_NUMERIC, _TEMPORAL):
        if left in chain and right in chain:
            return chain[max(chain.index(left), chain.index(right))]
    if left in _STRINGABLE and right in _STRINGABLE:
        return TypeTag.STRING
    if {left, right} == {TypeTag.STRUCT, TypeTag.MAP}:
        return TypeTag.MAP
    if {left, right} <= {TypeTag.BYTES, TypeTag.MEDIA}:
        return TypeTag.MEDIA
    return TypeTag.JSON


_EXAMPLE_CAP = 5
_EXAMPLE_WIDTH = 60


def render_example(value: Any) -> str:
    """Render a sampled value as short, hashable, log-safe text."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    text = repr(value) if not isinstance(value, str) else value
    text = " ".join(text.split())
    return text if len(text) <= _EXAMPLE_WIDTH else text[: _EXAMPLE_WIDTH - 1] + "…"


def identifier(name: str) -> str:
    """A python-safe identifier derived from an arbitrary field name."""
    ident = slug(name) or "field"
    if ident[0].isdigit():
        ident = f"f_{ident}"
    if ident in _KEYWORDS:
        ident = f"{ident}_"
    return ident


@dataclass(frozen=True, slots=True)
class FieldShape:
    """One observed field, with the evidence that produced the observation."""

    name: str
    tag: TypeTag = TypeTag.NULL
    nullable: bool = False
    optional: bool = False
    children: tuple["FieldShape", ...] = ()
    item: "FieldShape | None" = None
    observations: int = 0
    nulls: int = 0
    #: Short rendered samples. Kept as text so a shape stays hashable,
    #: serializable, and safe to paste into a generated docstring.
    examples: tuple[str, ...] = ()
    format_hint: str = ""
    unit: str = ""
    doc: str = ""

    @property
    def ident(self) -> str:
        return identifier(self.name)

    @property
    def python_type(self) -> str:
        if self.tag is TypeTag.LIST:
            inner = self.item.python_type if self.item else "typing.Any"
            return f"list[{inner}]"
        if self.tag in (TypeTag.STRUCT, TypeTag.MAP) and self.children:
            return "dict[str, typing.Any]"
        return PYTHON_TYPES[self.tag]

    @property
    def null_ratio(self) -> float:
        return (self.nulls / self.observations) if self.observations else 0.0

    def merge(self, other: "FieldShape") -> "FieldShape":
        """Union two observations of the same field."""
        if other.name != self.name:
            raise ShapeError(
                f"cannot merge fields {self.name!r} and {other.name!r}"
            )
        tag = promote(self.tag, other.tag)
        children: tuple[FieldShape, ...] = ()
        if tag in (TypeTag.STRUCT, TypeTag.MAP):
            children = tuple(_merge_fields(self.children, other.children))
        item = self.item
        if tag is TypeTag.LIST:
            if self.item and other.item:
                item = self.item.merge(other.item)
            else:
                item = self.item or other.item
        examples = tuple(dict.fromkeys(self.examples + other.examples))[:_EXAMPLE_CAP]
        return FieldShape(
            name=self.name,
            tag=tag,
            nullable=self.nullable or other.nullable,
            optional=self.optional or other.optional,
            children=children,
            item=item,
            observations=self.observations + other.observations,
            nulls=self.nulls + other.nulls,
            examples=examples,
            format_hint=self.format_hint or other.format_hint,
            unit=self.unit or other.unit,
            doc=self.doc or other.doc,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "ident": self.ident,
            "tag": self.tag.value,
            "nullable": self.nullable,
            "optional": self.optional,
            "observations": self.observations,
            "nulls": self.nulls,
        }
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        if self.item:
            out["item"] = self.item.to_dict()
        if self.format_hint:
            out["format_hint"] = self.format_hint
        if self.unit:
            out["unit"] = self.unit
        if self.doc:
            out["doc"] = self.doc
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldShape":
        return cls(
            name=data["name"],
            tag=TypeTag(data.get("tag", "null")),
            nullable=bool(data.get("nullable", False)),
            optional=bool(data.get("optional", False)),
            children=tuple(cls.from_dict(c) for c in data.get("children", ())),
            item=cls.from_dict(data["item"]) if data.get("item") else None,
            observations=int(data.get("observations", 0)),
            nulls=int(data.get("nulls", 0)),
            format_hint=data.get("format_hint", ""),
            unit=data.get("unit", ""),
            doc=data.get("doc", ""),
        )


def _merge_fields(left: Sequence[FieldShape], right: Sequence[FieldShape]) -> list[FieldShape]:
    """Merge two field lists, preserving first-seen order and marking absences."""
    index = {f.name: f for f in left}
    order = [f.name for f in left]
    for f in right:
        if f.name in index:
            index[f.name] = index[f.name].merge(f)
        else:
            index[f.name] = replace(f, optional=True)
            order.append(f.name)
    left_names = {f.name for f in left}
    right_names = {f.name for f in right}
    for name in left_names - right_names:
        if right:  # only "missing" if the other side actually had fields
            index[name] = replace(index[name], optional=True)
    return [index[name] for name in order]


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    WIDENED = "widened"
    NARROWED = "narrowed"
    RETYPED = "retyped"
    NULLABILITY = "nullability"


@dataclass(frozen=True, slots=True)
class ShapeChange:
    """One difference between two revisions of a shape."""

    kind: ChangeKind
    field: str
    before: str = ""
    after: str = ""

    @property
    def breaking(self) -> bool:
        """Whether replaying this change can invalidate existing readers."""
        return self.kind in (ChangeKind.REMOVED, ChangeKind.NARROWED, ChangeKind.RETYPED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "breaking": self.breaking,
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        arrow = f" {self.before} -> {self.after}" if self.before or self.after else ""
        return f"{self.kind.value}:{self.field}{arrow}"


@dataclass(frozen=True, slots=True)
class DataShape:
    """A named collection of field shapes, plus where it came from."""

    name: str
    fields: tuple[FieldShape, ...] = ()
    rows: int = 0
    source: str = ""
    kind: str = "record"
    labels: Mapping[str, str] = field(default_factory=dict)
    doc: str = ""

    # -- access ----------------------------------------------------------

    @property
    def symbol(self) -> str:
        return "".join(part.title() for part in slug(self.name).split("_")) or "Record"

    @property
    def digest(self) -> str:
        """Structural fingerprint — changes only when the structure changes."""
        return digest(
            {
                "name": self.name,
                "fields": [
                    {"n": f.name, "t": f.tag.value, "null": f.nullable, "opt": f.optional}
                    for f in self.fields
                ],
            }
        )

    def field(self, name: str) -> FieldShape | None:
        return next((f for f in self.fields if f.name == name), None)

    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    # -- revision --------------------------------------------------------

    def merge(self, other: "DataShape") -> "DataShape":
        """Fold a new observation into this shape."""
        return DataShape(
            name=self.name or other.name,
            fields=tuple(_merge_fields(self.fields, other.fields)),
            rows=self.rows + other.rows,
            source=self.source or other.source,
            kind=self.kind,
            labels={**dict(other.labels), **dict(self.labels)},
            doc=self.doc or other.doc,
        )

    def diff(self, other: "DataShape") -> list[ShapeChange]:
        """Changes needed to go from ``self`` to ``other``."""
        changes: list[ShapeChange] = []
        mine = {f.name: f for f in self.fields}
        theirs = {f.name: f for f in other.fields}
        for name, f in theirs.items():
            if name not in mine:
                changes.append(ShapeChange(ChangeKind.ADDED, name, after=f.tag.value))
        for name, f in mine.items():
            if name not in theirs:
                changes.append(ShapeChange(ChangeKind.REMOVED, name, before=f.tag.value))
                continue
            other_field = theirs[name]
            if f.tag is not other_field.tag:
                widened = promote(f.tag, other_field.tag) is other_field.tag
                narrowed = promote(f.tag, other_field.tag) is f.tag
                kind = (
                    ChangeKind.WIDENED if widened
                    else ChangeKind.NARROWED if narrowed
                    else ChangeKind.RETYPED
                )
                changes.append(ShapeChange(kind, name, before=f.tag.value, after=other_field.tag.value))
            elif f.nullable != other_field.nullable:
                changes.append(
                    ShapeChange(
                        ChangeKind.NULLABILITY, name,
                        before=str(f.nullable).lower(), after=str(other_field.nullable).lower(),
                    )
                )
        return sorted(changes, key=lambda c: (c.field, c.kind.value))

    def compatible_with(self, other: "DataShape") -> bool:
        """True when moving to ``other`` breaks nothing that reads ``self``."""
        return not any(c.breaking for c in self.diff(other))

    def rename(self, name: str) -> "DataShape":
        return replace(self, name=name)

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "digest": self.digest,
            "rows": self.rows,
            "source": self.source,
            "kind": self.kind,
            "labels": dict(self.labels),
            "doc": self.doc,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataShape":
        return cls(
            name=data["name"],
            fields=tuple(FieldShape.from_dict(f) for f in data.get("fields", ())),
            rows=int(data.get("rows", 0)),
            source=data.get("source", ""),
            kind=data.get("kind", "record"),
            labels=dict(data.get("labels", {})),
            doc=data.get("doc", ""),
        )

    def __iter__(self) -> Iterator[FieldShape]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def __repr__(self) -> str:  # pragma: no cover - display only
        cols = ", ".join(f"{f.ident}:{f.tag.value}" for f in self.fields[:6])
        more = "…" if len(self.fields) > 6 else ""
        return f"<DataShape {self.name}({cols}{more}) rows={self.rows}>"


def unify(shapes: Iterable[DataShape], *, name: str = "") -> DataShape:
    """Merge many observations into one shape."""
    it = iter(shapes)
    try:
        base = next(it)
    except StopIteration:
        return DataShape(name=name or "empty")
    for shape in it:
        base = base.merge(shape)
    return base.rename(name) if name else base
