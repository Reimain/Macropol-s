"""The sandbox: what it rejects statically, and what the OS stops at runtime."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fixtures import ORDERS

from gratimos.contextflow import ContextFlow
from gratimos.errors import TransformError
from gratimos.meta import TypeTag, Wrapped
from gratimos.transforms import (
    SandboxPolicy,
    TransformRegistry,
    inspect_source,
    scaffold,
)

GOOD = textwrap.dedent('''
    NAME = "normalize"
    APPLIES_TO = "orders*"
    DESCRIPTION = "Uppercase the customer and derive a margin"
    import datetime

    def transform(records, context):
        context["log"]("saw", len(records))
        out = []
        for row in records:
            row = dict(row)
            row["customer"] = str(row["customer"]).upper()
            row["margin"] = round(float(row["total"]) * 0.18, 2)
            out.append(row)
        return out
''')

HOSTILE = {
    "socket_import": "import socket\ndef transform(records, context):\n    return records\n",
    "subprocess_import": "from subprocess import run\ndef transform(records, context):\n    return records\n",
    "class_escape": "def transform(records, context):\n    return [].__class__.__base__.__subclasses__()\n",
    "eval_call": "def transform(records, context):\n    return eval('1+1')\n",
    "exec_call": "def transform(records, context):\n    exec('x=1')\n    return records\n",
    "open_file": "def transform(records, context):\n    return open('/etc/passwd').read()\n",
    "globals_access": "def transform(records, context):\n    return globals()\n",
    "relative_import": "from . import sibling\ndef transform(records, context):\n    return records\n",
    "no_entrypoint": "def helper():\n    return 1\n",
}


@pytest.mark.parametrize("name,source", sorted(HOSTILE.items()))
def test_static_gate_rejects_escapes(name: str, source: str):
    inspection = inspect_source(source, path=f"{name}.py")
    assert not inspection.approved, f"{name} should have been rejected"
    assert inspection.violations


def test_static_gate_approves_ordinary_data_work():
    inspection = inspect_source(GOOD, path="normalize.py")
    assert inspection.approved, inspection.reasons
    assert inspection.metadata["APPLIES_TO"] == "orders*"
    assert "datetime" in inspection.imports


def test_syntax_errors_are_reported_not_raised():
    inspection = inspect_source("def transform(:\n", path="broken.py")
    assert not inspection.approved
    assert "syntax error" in inspection.reasons[0]


def test_scaffolded_transformations_pass_their_own_gate(tmp_path: Path):
    path = scaffold(tmp_path, "starter", applies_to="orders")
    assert inspect_source(path.read_text(), path=str(path)).approved
    with pytest.raises(TransformError, match="already exists"):
        scaffold(tmp_path, "starter")


def test_approved_transformation_runs_isolated_and_reshapes(tmp_path: Path):
    (tmp_path / "normalize.py").write_text(GOOD)
    registry = TransformRegistry(tmp_path, flow=ContextFlow("kernel"))
    registry.discover()

    outcome = registry.apply("normalize", Wrapped.of(ORDERS, name="orders"))
    assert outcome.ok
    assert outcome.result.rows_out == 3
    assert outcome.result.logs == ["saw 3"]
    assert any(limit.startswith("RLIMIT_CPU") for limit in outcome.result.limits_applied)
    assert outcome.payload.records()[0]["customer"] == "ADA"
    # The shape is re-observed, so a new field appears without being declared.
    assert outcome.payload.shape.field("margin").tag is TypeTag.FLOAT


def test_rejected_transformations_never_execute(tmp_path: Path):
    (tmp_path / "hostile.py").write_text(HOSTILE["socket_import"])
    registry = TransformRegistry(tmp_path)
    registry.discover()

    outcome = registry.apply("hostile", Wrapped.of(ORDERS, name="orders"))
    assert not outcome.ok
    assert "not approved" in outcome.error


def test_runtime_exceptions_come_back_as_failures(tmp_path: Path):
    (tmp_path / "boom.py").write_text("def transform(records, context):\n    return records[999]\n")
    registry = TransformRegistry(tmp_path)
    registry.discover()
    outcome = registry.apply("boom", Wrapped.of(ORDERS, name="orders"))
    assert not outcome.ok
    assert "IndexError" in outcome.error


def test_cpu_limit_stops_a_runaway_transformation(tmp_path: Path):
    (tmp_path / "spin.py").write_text("def transform(records, context):\n    while True:\n        pass\n")
    registry = TransformRegistry(tmp_path, policy=SandboxPolicy(timeout_s=15.0, cpu_seconds=2))
    registry.discover()
    outcome = registry.apply("spin", Wrapped.of([{"a": 1}], name="t"))
    assert not outcome.ok
    assert "resource limit" in outcome.error or "wall-clock" in outcome.error


def test_memory_limit_stops_an_allocating_transformation(tmp_path: Path):
    (tmp_path / "bomb.py").write_text(
        "def transform(records, context):\n"
        "    held = []\n"
        "    while True:\n"
        "        held.append('y' * 10_000_000)\n"
    )
    registry = TransformRegistry(tmp_path, policy=SandboxPolicy(timeout_s=30.0, memory_bytes=128 << 20))
    registry.discover()
    outcome = registry.apply("bomb", Wrapped.of([{"a": 1}], name="t"))
    assert not outcome.ok
    assert "memory" in outcome.error.lower() or "resource limit" in outcome.error


def test_changed_source_is_re_inspected(tmp_path: Path):
    path = tmp_path / "shifty.py"
    path.write_text(GOOD)
    registry = TransformRegistry(tmp_path)
    registry.discover()
    assert registry.get("normalize").approved

    path.write_text(HOSTILE["socket_import"])
    registry.discover()
    assert not registry.get("shifty").approved


def test_matching_transformations_chain_in_name_order(tmp_path: Path):
    (tmp_path / "a_first.py").write_text(
        'NAME = "a_first"\nAPPLIES_TO = "orders"\n'
        'def transform(records, context):\n'
        '    return [dict(r, step="a") for r in records]\n'
    )
    (tmp_path / "b_second.py").write_text(
        'NAME = "b_second"\nAPPLIES_TO = "orders"\n'
        'def transform(records, context):\n'
        '    return [dict(r, step=r["step"] + "b") for r in records]\n'
    )
    registry = TransformRegistry(tmp_path)
    registry.discover()

    outcomes = registry.apply_all(Wrapped.of(ORDERS, name="orders"))
    assert [o.transformation for o in outcomes] == ["a_first", "b_second"]
    assert outcomes[-1].payload.records()[0]["step"] == "ab"


def test_transform_events_record_both_digests(tmp_path: Path):
    (tmp_path / "normalize.py").write_text(GOOD)
    flow = ContextFlow("kernel")
    registry = TransformRegistry(tmp_path, flow=flow)
    registry.discover()
    payload = Wrapped.of(ORDERS, name="orders")
    outcome = registry.apply("normalize", payload)

    event = flow.event(outcome.event_id)
    assert event.payload["payload_before"] == payload.digest
    assert event.payload["payload_after"] == outcome.payload.digest
    assert event.payload["rows_in"] == 3
