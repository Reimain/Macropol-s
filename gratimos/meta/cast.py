"""Malleable type casting.

Casting is where "be like water" has to stay honest. A lenient cast that
silently turns ``"abc"`` into ``0`` destroys evidence; a strict cast that
refuses ``"12"`` where an int is wanted destroys throughput. So casting is a
policy: the caller picks the mode, and every coercion that loses or invents
information is reported rather than assumed.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ..errors import CastError
from .infer import is_null, media_kind
from .shapes import DataShape, FieldShape, TypeTag


class CastMode(str, Enum):
    STRICT = "strict"    # only exact type matches pass
    LENIENT = "lenient"  # parse text into the target type, refuse nonsense
    COERCE = "coerce"    # last resort: substitute a default and report it


@dataclass(slots=True)
class CastReport:
    """What a cast actually did, so a policymaker can act on it."""

    converted: int = 0
    unchanged: int = 0
    defaulted: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.failures and not self.defaulted

    def merge(self, other: "CastReport") -> "CastReport":
        return CastReport(
            converted=self.converted + other.converted,
            unchanged=self.unchanged + other.unchanged,
            defaulted=self.defaulted + other.defaulted,
            failures=self.failures + other.failures,
            notes=self.notes + other.notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "converted": self.converted,
            "unchanged": self.unchanged,
            "defaulted": self.defaulted,
            "failures": self.failures[:20],
            "failure_count": len(self.failures),
            "notes": self.notes[:20],
            "clean": self.clean,
        }


_DEFAULTS: dict[TypeTag, Any] = {
    TypeTag.BOOL: False,
    TypeTag.INT: 0,
    TypeTag.FLOAT: 0.0,
    TypeTag.DECIMAL: decimal.Decimal(0),
    TypeTag.STRING: "",
    TypeTag.BYTES: b"",
    TypeTag.MEDIA: b"",
    TypeTag.LIST: (),
    TypeTag.STRUCT: {},
    TypeTag.MAP: {},
    TypeTag.JSON: None,
}

_TRUE = {"true", "yes", "y", "1", "on", "t"}
_FALSE = {"false", "no", "n", "0", "off", "f"}


def _parse_datetime(text: str) -> _dt.datetime:
    raw = text.strip().replace("Z", "+00:00")
    return _dt.datetime.fromisoformat(raw)


def _parse_duration(text: str) -> _dt.timedelta:
    raw = text.strip().upper()
    if not raw.startswith("P"):
        raise ValueError(f"not an ISO-8601 duration: {text!r}")
    date_part, _, time_part = raw[1:].partition("T")
    total = 0.0
    for part, units in ((date_part, {"Y": 31_557_600, "M": 2_629_800, "D": 86_400}),
                        (time_part, {"H": 3600, "M": 60, "S": 1})):
        number = ""
        for char in part:
            if char.isdigit() or char == ".":
                number += char
            elif char in units:
                total += float(number or 0) * units[char]
                number = ""
            else:
                raise ValueError(f"unexpected {char!r} in duration {text!r}")
    return _dt.timedelta(seconds=total)


def cast_value(value: Any, tag: TypeTag, *, mode: CastMode = CastMode.LENIENT) -> Any:
    """Cast a single value, raising :class:`CastError` when it cannot be done."""
    if tag is TypeTag.JSON:
        return value
    if is_null(value):
        return None
    if mode is CastMode.STRICT:
        return _strict(value, tag)
    try:
        return _lenient(value, tag)
    except CastError:
        if mode is CastMode.COERCE:
            return _DEFAULTS.get(tag)
        raise


def _strict(value: Any, tag: TypeTag) -> Any:
    expected: dict[TypeTag, type | tuple[type, ...]] = {
        TypeTag.BOOL: bool,
        TypeTag.INT: int,
        TypeTag.FLOAT: float,
        TypeTag.DECIMAL: decimal.Decimal,
        TypeTag.STRING: str,
        TypeTag.BYTES: (bytes, bytearray),
        TypeTag.MEDIA: (bytes, bytearray),
        TypeTag.DATE: _dt.date,
        TypeTag.DATETIME: _dt.datetime,
        TypeTag.DURATION: _dt.timedelta,
        TypeTag.UUID: uuid.UUID,
        TypeTag.LIST: (list, tuple),
        TypeTag.STRUCT: Mapping,
        TypeTag.MAP: Mapping,
    }
    want = expected.get(tag)
    if want is None or isinstance(value, want):
        # datetime is a date subclass; keep the distinction meaningful.
        if tag is TypeTag.DATE and isinstance(value, _dt.datetime):
            raise CastError(f"strict cast refuses datetime where date expected: {value!r}")
        return value
    raise CastError(f"strict cast refuses {type(value).__name__} as {tag.value}: {value!r}")


def _lenient(value: Any, tag: TypeTag) -> Any:  # noqa: PLR0911, PLR0912 - a dispatch table
    text = value.strip() if isinstance(value, str) else None
    try:
        if tag is TypeTag.BOOL:
            if isinstance(value, bool):
                return value
            if text is not None:
                low = text.lower()
                if low in _TRUE:
                    return True
                if low in _FALSE:
                    return False
                raise ValueError(text)
            return bool(value)
        if tag is TypeTag.INT:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{value} is not integral")
            return int(text) if text is not None else int(value)
        if tag is TypeTag.FLOAT:
            return float(text) if text is not None else float(value)
        if tag is TypeTag.DECIMAL:
            return decimal.Decimal(text if text is not None else str(value))
        if tag is TypeTag.STRING:
            if isinstance(value, (bytes, bytearray)):
                return bytes(value).decode("utf-8", "replace")
            if isinstance(value, (Mapping, list, tuple)):
                return json.dumps(value, default=str, sort_keys=True)
            return str(value)
        if tag in (TypeTag.BYTES, TypeTag.MEDIA):
            if isinstance(value, (bytes, bytearray, memoryview)):
                return bytes(value)
            return str(value).encode("utf-8")
        if tag is TypeTag.DATE:
            if isinstance(value, _dt.datetime):
                return value.date()
            if isinstance(value, _dt.date):
                return value
            return _dt.date.fromisoformat(str(text or value))
        if tag is TypeTag.DATETIME:
            if isinstance(value, _dt.datetime):
                return value
            if isinstance(value, _dt.date):
                return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
            return _parse_datetime(str(text or value))
        if tag is TypeTag.DURATION:
            if isinstance(value, _dt.timedelta):
                return value
            if isinstance(value, (int, float)):
                return _dt.timedelta(seconds=float(value))
            return _parse_duration(str(text or value))
        if tag is TypeTag.UUID:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(text or value))
        if tag is TypeTag.LIST:
            if isinstance(value, (list, tuple)):
                return list(value)
            if text is not None and text.startswith("["):
                return list(json.loads(text))
            return [value]
        if tag in (TypeTag.STRUCT, TypeTag.MAP):
            if isinstance(value, Mapping):
                return dict(value)
            if text is not None and text.startswith("{"):
                return dict(json.loads(text))
            raise ValueError(f"cannot read {type(value).__name__} as a struct")
    # Every `raise ValueError` above is caught here and re-raised as `CastError`
    # with the value and the target tag attached. They are internal control flow
    # into this funnel, not taxonomy violations — raising `CastError` at each site
    # would skip this line and lose the context it adds.
    except (ValueError, TypeError, ArithmeticError, decimal.InvalidOperation, json.JSONDecodeError) as exc:
        raise CastError(f"cannot cast {value!r} to {tag.value}: {exc}") from exc
    return value


class Caster:
    """Applies a shape to records, reporting what it had to bend."""

    def __init__(self, shape: DataShape, *, mode: CastMode = CastMode.LENIENT) -> None:
        self.shape = shape
        self.mode = mode

    def value(self, field_shape: FieldShape, value: Any) -> tuple[Any, CastReport]:
        report = CastReport()
        if is_null(value):
            if not field_shape.nullable and self.mode is CastMode.STRICT:
                report.failures.append(f"{field_shape.name}: null in non-nullable field")
                return None, report
            report.unchanged += 1
            return None, report
        try:
            out = cast_value(value, field_shape.tag, mode=self.mode)
        except CastError as exc:
            report.failures.append(f"{field_shape.name}: {exc}")
            return value, report
        if out is value or out == value:
            report.unchanged += 1
        elif self.mode is CastMode.COERCE and out == _DEFAULTS.get(field_shape.tag):
            report.defaulted += 1
            report.notes.append(f"{field_shape.name}: substituted default for {value!r}")
        else:
            report.converted += 1
        return out, report

    def record(self, row: Mapping[str, Any]) -> tuple[dict[str, Any], CastReport]:
        out: dict[str, Any] = {}
        report = CastReport()
        for field_shape in self.shape.fields:
            if field_shape.name not in row:
                if not field_shape.optional and self.mode is CastMode.STRICT:
                    report.failures.append(f"{field_shape.name}: required field missing")
                continue
            value, sub = self.value(field_shape, row[field_shape.name])
            out[field_shape.ident] = value
            report = report.merge(sub)
        extra = set(row) - {f.name for f in self.shape.fields}
        if extra:
            report.notes.append(f"unmapped fields dropped: {sorted(extra)[:10]}")
        return out, report

    def records(self, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], CastReport]:
        out: list[dict[str, Any]] = []
        report = CastReport()
        for row in rows:
            cast_row, sub = self.record(row)
            out.append(cast_row)
            report = report.merge(sub)
        return out, report


def cast_records(
    rows: Sequence[Mapping[str, Any]],
    shape: DataShape,
    *,
    mode: CastMode = CastMode.LENIENT,
) -> tuple[list[dict[str, Any]], CastReport]:
    """Convenience wrapper over :class:`Caster`."""
    return Caster(shape, mode=mode).records(rows)


def detect_media(payload: bytes) -> str:
    """Re-exported so callers casting to MEDIA can label what they got."""
    return media_kind(payload)
