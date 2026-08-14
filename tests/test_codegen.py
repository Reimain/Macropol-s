"""Emission, the AST merge, protobuf numbering, and generation history."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gratimos.codegen import (
    ConflictPolicy,
    ModuleRegistry,
    Resolution,
    diff_symbols,
    emit_module,
    emit_proto,
    merge_sources,
    parse_module,
)
from gratimos.contextflow import ContextFlow
from gratimos.errors import CodegenError, MergeConflict
from gratimos.meta import Wrapped, infer_shape

from fixtures import ORDERS

BASE = '''# gratimos:generated
"""v1"""
from __future__ import annotations
import typing

SHAPE_DIGEST = "aaa"


def describe():
    return {"digest": SHAPE_DIGEST}
'''


def _shape(rows=ORDERS, name="orders"):
    wrapped, _ = Wrapped.of(rows, name=name).cast()
    return wrapped.shape


# --- emission ------------------------------------------------------------


def test_generated_module_imports_and_round_trips(tmp_path: Path):
    registry = ModuleRegistry(tmp_path / "generated")
    registry.generate(_shape())
    module = registry.load("orders")

    assert module.describe()["entity"] == "orders"
    record = module.Orders.from_record(ORDERS[0])
    assert record.order_id == 1001
    assert record.to_record()["customer"] == "Ada"


def test_generated_module_rejects_uncastable_input(tmp_path: Path):
    registry = ModuleRegistry(tmp_path / "generated")
    registry.generate(_shape())
    module = registry.load("orders")
    with pytest.raises(ValueError, match="cannot build Orders"):
        module.Orders.from_record({**ORDERS[0], "order_id": "not-a-number"})


def test_nullable_fields_default_and_sort_after_required():
    shape = infer_shape([{"a": 1, "b": None}, {"a": 2, "b": "x"}], name="t")
    source = emit_module(shape).source
    tree = ast.parse(source)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fields = [n.target.id for n in klass.body if isinstance(n, ast.AnnAssign)]
    assert fields.index("a") < fields.index("b")  # required first


def test_emitting_a_shape_with_no_fields_is_refused():
    with pytest.raises(CodegenError, match="no fields"):
        emit_module(infer_shape([], name="empty"))


# --- merge ---------------------------------------------------------------


def test_merge_preserves_local_edits_and_takes_generator_changes():
    ours = BASE.replace('return {"digest": SHAPE_DIGEST}',
                        'return {"digest": SHAPE_DIGEST, "team": "billing"}')
    ours += '\n\ndef audit(rows):\n    return len(rows)\n'
    theirs = BASE.replace('"aaa"', '"bbb"')

    result = merge_sources(ours, theirs, BASE)
    decisions = {d.name: d.resolution for d in result.decisions}

    assert decisions["SHAPE_DIGEST"] is Resolution.TAKE_GENERATED
    assert decisions["describe"] is Resolution.KEEP_LOCAL
    assert decisions["audit"] is Resolution.HAND_WRITTEN
    assert '"bbb"' in result.source
    assert '"team": "billing"' in result.source
    assert "def audit" in result.source
    ast.parse(result.source)


def test_keep_marker_pins_a_symbol_against_regeneration():
    ours = BASE.replace("def describe", "# gratimos:keep\ndef describe").replace(
        'return {"digest": SHAPE_DIGEST}', 'return {"mine": True}'
    )
    theirs = BASE.replace('return {"digest": SHAPE_DIGEST}', 'return {"generated": True}')

    result = merge_sources(ours, theirs, BASE)
    assert [d.resolution for d in result.decisions if d.name == "describe"] == [Resolution.KEEP_MARKED]
    assert '{"mine": True}' in result.source


def test_conflicting_edits_raise_by_default():
    ours = BASE.replace('"aaa"', '"local"')
    theirs = BASE.replace('"aaa"', '"generated"')
    with pytest.raises(MergeConflict, match="SHAPE_DIGEST"):
        merge_sources(ours, theirs, BASE)


def test_marked_conflicts_cannot_be_imported():
    ours = "def f():\n    return 2\n"
    theirs = "def f():\n    return 3\n"
    base = "def f():\n    return 1\n"
    result = merge_sources(ours, theirs, base, policy=ConflictPolicy.MARK)
    assert not result.clean
    with pytest.raises(SyntaxError):
        ast.parse(result.source)


def test_conflict_policies_pick_a_side_but_still_record_the_conflict():
    ours, theirs, base = ("def f():\n    return 2\n", "def f():\n    return 3\n",
                          "def f():\n    return 1\n")
    local = merge_sources(ours, theirs, base, policy=ConflictPolicy.PREFER_LOCAL)
    generated = merge_sources(ours, theirs, base, policy=ConflictPolicy.PREFER_GENERATED)
    assert "return 2" in local.source and len(local.conflicts) == 1
    assert "return 3" in generated.source and len(generated.conflicts) == 1


def test_without_a_base_overlapping_symbols_are_conflicts():
    with pytest.raises(MergeConflict, match="no recorded base"):
        merge_sources("def f():\n    return 2\n", "def f():\n    return 3\n")


def test_merge_ignores_reformatting():
    ours = BASE.replace('return {"digest": SHAPE_DIGEST}', 'return {\n        "digest": SHAPE_DIGEST,\n    }')
    result = merge_sources(ours, BASE, BASE)
    assert result.clean
    assert all(d.resolution is not Resolution.CONFLICT for d in result.decisions)


def test_symbol_diff_is_structural():
    assert diff_symbols(BASE, BASE + "\n\ndef extra():\n    pass\n")["added"] == ["extra"]
    assert parse_module(BASE).imports == ["from __future__ import annotations", "import typing"]


# --- lifecycle -----------------------------------------------------------


def test_regeneration_merges_into_hand_edited_source(tmp_path: Path):
    registry = ModuleRegistry(tmp_path / "generated", flow=ContextFlow("kernel"))
    registry.generate(_shape())

    path = registry.root / "orders.py"
    path.write_text(
        path.read_text() + '\n\ndef local_helper(rows):\n    return sorted(rows)\n'
    )

    evolved = _shape().merge(infer_shape([{"order_id": 1, "channel": "web"}], name="orders"))
    generation = registry.generate(evolved)

    assert generation.revision == 2
    assert generation.clean
    source = path.read_text()
    assert "local_helper" in source
    assert "channel" in source

    registry.unload_all()
    module = registry.load("orders", reload=True)
    assert module.local_helper([3, 1, 2]) == [1, 2, 3]
    assert "channel" in module.describe()["fields"]


def test_regenerating_an_unchanged_shape_is_a_no_op(tmp_path: Path):
    registry = ModuleRegistry(tmp_path / "generated")
    first = registry.generate(_shape())
    second = registry.generate(_shape())
    assert first.id == second.id
    assert len(registry.history("orders")) == 1


def test_restore_rewrites_the_file_as_a_new_generation(tmp_path: Path):
    registry = ModuleRegistry(tmp_path / "generated")
    registry.generate(_shape())
    evolved = _shape().merge(infer_shape([{"order_id": 1, "channel": "web"}], name="orders"))
    registry.generate(evolved)
    assert "channel" in (registry.root / "orders.py").read_text()

    restored = registry.restore("orders", 1)
    assert restored.revision == 3
    assert "channel" not in (registry.root / "orders.py").read_text()


# --- protobuf ------------------------------------------------------------


def test_proto_field_numbers_survive_field_removal():
    shape = _shape()
    schema = emit_proto(shape)
    numbers = {name: a.number for name, a in schema.allocation.fields.items()}

    narrowed = infer_shape([{"order_id": 1, "customer": "A"}], name="orders")
    second = emit_proto(narrowed, allocation=schema.allocation)

    assert second.allocation.fields["order_id"].number == numbers["order_id"]
    assert second.allocation.fields["customer"].number == numbers["customer"]
    assert set(second.allocation.reserved()) == {numbers["total"], numbers["placed"], numbers["priority"]}
    assert "reserved" in second.source


def test_proto_emits_nested_messages_and_repeated_fields():
    shape = infer_shape([{"tags": ["a"], "meta": {"city": "London"}}], name="t")
    source = emit_proto(shape).source
    assert "repeated string tags" in source
    assert "message Meta {" in source
