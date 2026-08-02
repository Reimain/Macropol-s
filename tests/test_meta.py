"""Shapes, inference, casting, and the self-describing wrappers."""

from __future__ import annotations

import datetime as dt

import pytest

from gratimos.errors import CastError
from gratimos.meta import (
    CastMode,
    ChangeKind,
    TypeTag,
    Wrapped,
    cast_value,
    infer_shape,
    promote,
    unify,
)

from fixtures import CUSTOMERS, ORDERS


def test_type_lattice_widens_without_losing_information():
    assert promote(TypeTag.INT, TypeTag.FLOAT) is TypeTag.FLOAT
    assert promote(TypeTag.NULL, TypeTag.DATE) is TypeTag.DATE
    assert promote(TypeTag.DATE, TypeTag.DATETIME) is TypeTag.DATETIME
    assert promote(TypeTag.INT, TypeTag.STRING) is TypeTag.STRING
    # Structure that genuinely disagrees goes to the top of the lattice.
    assert promote(TypeTag.STRUCT, TypeTag.LIST) is TypeTag.JSON


def test_inference_reads_semantics_out_of_text():
    shape = infer_shape(ORDERS, name="orders")
    types = {f.ident: f.tag for f in shape}
    assert types["order_id"] is TypeTag.INT
    assert types["total"] is TypeTag.FLOAT
    assert types["placed"] is TypeTag.DATE
    assert types["priority"] is TypeTag.BOOL
    # The text origin is recorded so casting stays an explicit step.
    assert shape.field("order_id").format_hint == "text"


def test_inference_records_nulls_and_nested_structure():
    shape = infer_shape(
        [{"a": 1, "meta": {"x": 1}}, {"a": None, "meta": {"x": 2, "y": 3}}], name="t"
    )
    assert shape.field("a").nullable
    assert shape.field("a").null_ratio == 0.5
    assert {c.ident for c in shape.field("meta").children} == {"x", "y"}


def test_shape_diff_classifies_breaking_changes():
    base = infer_shape([{"a": 1.5, "b": "x"}], name="t")
    widened = infer_shape([{"a": 1.5, "b": "x", "c": True}], name="t")
    narrowed = infer_shape([{"a": 1, "b": "x"}], name="t")
    retyped = infer_shape([{"a": {"n": 1}, "b": "x"}], name="t")

    assert [c.kind for c in base.diff(widened)] == [ChangeKind.ADDED]
    assert base.compatible_with(widened)

    assert base.diff(narrowed)[0].kind is ChangeKind.NARROWED
    assert not base.compatible_with(narrowed)

    assert base.diff(retyped)[0].kind is ChangeKind.RETYPED
    assert base.diff(retyped)[0].breaking


def test_unify_marks_fields_absent_from_one_side_optional():
    left = infer_shape([{"a": 1}], name="t")
    right = infer_shape([{"a": 2, "b": "x"}], name="t")
    merged = unify([left, right])
    assert merged.field("b").optional
    assert not merged.field("a").optional


def test_casting_modes_differ_on_the_same_value():
    assert cast_value("12", TypeTag.INT, mode=CastMode.LENIENT) == 12
    with pytest.raises(CastError):
        cast_value("12", TypeTag.INT, mode=CastMode.STRICT)
    with pytest.raises(CastError):
        cast_value("abc", TypeTag.INT, mode=CastMode.LENIENT)
    # COERCE substitutes a default rather than failing — and says so in the report.
    assert cast_value("abc", TypeTag.INT, mode=CastMode.COERCE) == 0


def test_strict_cast_keeps_date_and_datetime_distinct():
    with pytest.raises(CastError):
        cast_value(dt.datetime(2026, 1, 1), TypeTag.DATE, mode=CastMode.STRICT)
    assert cast_value(dt.datetime(2026, 1, 1), TypeTag.DATE, mode=CastMode.LENIENT) == dt.date(2026, 1, 1)


def test_wrapper_casts_and_reports():
    wrapped = Wrapped.of(ORDERS, name="orders", source="file://orders.csv")
    cast, report = wrapped.cast()
    assert report.clean
    row = cast.records()[0]
    assert row["order_id"] == 1001
    assert row["placed"] == dt.date(2026, 1, 14)
    assert row["priority"] is True


def test_wrapper_failures_are_reported_not_raised():
    wrapped = Wrapped.of([{"n": "abc"}], name="t").reshape(
        infer_shape([{"n": 1}], name="t")
    )
    _, report = wrapped.cast(mode=CastMode.LENIENT)
    assert not report.clean
    assert "n:" in report.failures[0]


def test_wrapper_operations_are_immutable_and_traced():
    wrapped = Wrapped.of(CUSTOMERS, name="customers")
    projected = wrapped.select(["id", "name"])
    assert wrapped.shape.names() != projected.shape.names()
    assert projected.shape.names() == ["id", "name"]
    assert wrapped.id in projected.provenance.lineage
    assert projected.provenance.labels["op"] == "select"


def test_map_reinfers_the_shape():
    wrapped = Wrapped.of(ORDERS, name="orders")
    mapped = wrapped.map(lambda row: {**row, "margin": float(row["total"]) * 0.2})
    assert mapped.shape.field("margin").tag is TypeTag.FLOAT


def test_wrapper_digest_tracks_both_shape_and_value():
    a = Wrapped.of(ORDERS, name="orders")
    b = Wrapped.of(ORDERS, name="orders")
    assert a.digest == b.digest
    assert a.digest != Wrapped.of(ORDERS[:2], name="orders").digest
