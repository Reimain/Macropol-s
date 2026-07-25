"""Shape inference — reading structure out of whatever showed up.

Inference is deliberately evidence-based and reversible: it records how many
values it saw, how many were null, and what the raw examples looked like, so a
later observation can revise the conclusion rather than fight it.

String sniffing is the interesting case. A CSV column of ``"1"`` and ``"2"`` is
textually a string and semantically an integer. We record the semantic tag *and*
keep ``format_hint="text"`` so casting stays an explicit, auditable step.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .shapes import DataShape, FieldShape, TypeTag, promote, render_example

_INT_RE = re.compile(r"^[+-]?\d{1,19}$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DURATION_RE = re.compile(r"^P(?=\d|T)(\d+Y)?(\d+M)?(\d+D)?(T(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$")
_BOOL_WORDS = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False}
_NULL_WORDS = {"", "null", "none", "nil", "n/a", "na", "-", "nan"}

#: Magic bytes we recognize without a media library present.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "media/riff"),
    (b"\x00\x00\x00\x18ftyp", "video/mp4"),
    (b"\x00\x00\x00\x20ftyp", "video/mp4"),
    (b"\x1aE\xdf\xa3", "video/matroska"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"SQLite format 3\x00", "application/x-sqlite3"),
)


@dataclass(frozen=True, slots=True)
class InferenceOptions:
    """Knobs the orchestrator turns when the context depth changes."""

    sniff_strings: bool = True
    sample_limit: int = 500
    example_limit: int = 3
    null_words: frozenset[str] = frozenset(_NULL_WORDS)
    max_depth: int = 6


DEFAULT_OPTIONS = InferenceOptions()


def media_kind(payload: bytes) -> str:
    """Best-effort content type from magic bytes, ``""`` when unrecognized."""
    if payload[:4] == b"RIFF":
        # RIFF is a container; the subtype at offset 8 says what is inside.
        return {b"AVI ": "video/avi", b"WAVE": "audio/wav"}.get(payload[8:12], "media/riff")
    for magic, kind in _MAGIC:
        if payload.startswith(magic):
            return kind
    return ""


def is_null(value: Any, options: InferenceOptions = DEFAULT_OPTIONS) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in options.null_words:
        return True
    return False


def sniff_string(text: str) -> tuple[TypeTag, str]:
    """Semantic tag for a textual value, plus the hint that it came as text."""
    raw = text.strip()
    if not raw:
        return TypeTag.STRING, ""
    if raw.lower() in _BOOL_WORDS:
        return TypeTag.BOOL, "text"
    if _INT_RE.match(raw):
        return TypeTag.INT, "text"
    if _FLOAT_RE.match(raw):
        return TypeTag.FLOAT, "text"
    if _DATE_RE.match(raw):
        return TypeTag.DATE, "iso8601"
    if _DATETIME_RE.match(raw):
        return TypeTag.DATETIME, "iso8601"
    if _UUID_RE.match(raw):
        return TypeTag.UUID, "text"
    if _DURATION_RE.match(raw):
        return TypeTag.DURATION, "iso8601"
    return TypeTag.STRING, ""


def tag_of(value: Any, options: InferenceOptions = DEFAULT_OPTIONS) -> tuple[TypeTag, str]:
    """Tag a single python value."""
    if is_null(value, options):
        return TypeTag.NULL, ""
    if isinstance(value, bool):
        return TypeTag.BOOL, ""
    if isinstance(value, int):
        return TypeTag.INT, ""
    if isinstance(value, float):
        return TypeTag.FLOAT, ""
    if isinstance(value, decimal.Decimal):
        return TypeTag.DECIMAL, ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        kind = media_kind(bytes(value)[:32])
        return (TypeTag.MEDIA, kind) if kind else (TypeTag.BYTES, "")
    if isinstance(value, _dt.datetime):
        return TypeTag.DATETIME, ""
    if isinstance(value, _dt.date):
        return TypeTag.DATE, ""
    if isinstance(value, _dt.timedelta):
        return TypeTag.DURATION, ""
    if isinstance(value, uuid.UUID):
        return TypeTag.UUID, ""
    if isinstance(value, Mapping):
        return TypeTag.STRUCT, ""
    if isinstance(value, (list, tuple, set, frozenset)):
        return TypeTag.LIST, ""
    if isinstance(value, str):
        return sniff_string(value) if options.sniff_strings else (TypeTag.STRING, "")
    return TypeTag.JSON, type(value).__name__


def infer_field(
    name: str,
    values: Sequence[Any],
    *,
    options: InferenceOptions = DEFAULT_OPTIONS,
    depth: int = 0,
) -> FieldShape:
    """Infer one field from a column of observed values."""
    sample = list(values)[: options.sample_limit]
    tag = TypeTag.NULL
    hint = ""
    nulls = 0
    examples: list[str] = []
    child_rows: list[Mapping[str, Any]] = []
    item_values: list[Any] = []

    for value in sample:
        value_tag, value_hint = tag_of(value, options)
        if value_tag is TypeTag.NULL:
            nulls += 1
            continue
        tag = promote(tag, value_tag)
        hint = hint or value_hint
        if len(examples) < options.example_limit:
            examples.append(render_example(value))
        if value_tag is TypeTag.STRUCT and depth < options.max_depth:
            child_rows.append(value)
        elif value_tag is TypeTag.LIST and depth < options.max_depth:
            item_values.extend(list(value)[: options.example_limit])

    children: tuple[FieldShape, ...] = ()
    if tag in (TypeTag.STRUCT, TypeTag.MAP) and child_rows:
        nested = infer_shape(child_rows, name=name, options=options, depth=depth + 1)
        children = nested.fields
    item = None
    if tag is TypeTag.LIST and item_values:
        item = infer_field("item", item_values, options=options, depth=depth + 1)

    return FieldShape(
        name=str(name),
        tag=tag if tag is not TypeTag.NULL else TypeTag.STRING,
        nullable=nulls > 0,
        children=children,
        item=item,
        observations=len(sample),
        nulls=nulls,
        examples=tuple(examples),
        format_hint=hint,
    )


def infer_shape(
    records: Iterable[Any],
    *,
    name: str = "record",
    source: str = "",
    options: InferenceOptions = DEFAULT_OPTIONS,
    depth: int = 0,
) -> DataShape:
    """Infer a shape from an iterable of records.

    Accepts rows as mappings, sequences (positional columns), or scalars (a
    single-field ``value`` shape). Mixed inputs are normalized, not rejected —
    the whole point is that the kernel meets data where it is.
    """
    rows = list(records)[: options.sample_limit]
    if not rows:
        return DataShape(name=name, source=source, rows=0)

    columns: dict[str, list[Any]] = {}
    order: list[str] = []
    scalar_rows: list[Any] = []

    for row in rows:
        if isinstance(row, Mapping):
            for key, value in row.items():
                key = str(key)
                if key not in columns:
                    columns[key] = []
                    order.append(key)
                columns[key].append(value)
        elif isinstance(row, (list, tuple)) and not isinstance(row, (str, bytes)):
            for index, value in enumerate(row):
                key = f"c{index}"
                if key not in columns:
                    columns[key] = []
                    order.append(key)
                columns[key].append(value)
        else:
            scalar_rows.append(row)

    if scalar_rows and not columns:
        return DataShape(
            name=name,
            fields=(infer_field("value", scalar_rows, options=options, depth=depth),),
            rows=len(scalar_rows),
            source=source,
            kind="scalar",
        )

    # Pad short rows so absence is recorded as a null, not as a missing column.
    for key in order:
        missing = len(rows) - len(columns[key])
        if missing > 0:
            columns[key].extend([None] * missing)

    fields = tuple(
        infer_field(key, columns[key], options=options, depth=depth) for key in order
    )
    return DataShape(name=name, fields=fields, rows=len(rows), source=source)


def infer_value(value: Any, *, name: str = "value", options: InferenceOptions = DEFAULT_OPTIONS) -> DataShape:
    """Infer a shape from a single payload of unknown structure."""
    if isinstance(value, Mapping):
        return infer_shape([value], name=name, options=options)
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        return infer_shape(list(value), name=name, options=options)
    return infer_shape([value], name=name, options=options)
