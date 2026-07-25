"""The spine: causal ordering, the timetravel guard, rollback, and the journal."""

from __future__ import annotations

import pytest

from gratimos.contextflow import ContextFlow, EventKind, KeyringEvent, Ordering, compare, merge
from gratimos.errors import RollbackError, TimetravelConflict
from gratimos.trace import Journal, RollbackManager


def test_vector_clocks_detect_concurrency():
    a = {"x": 2, "y": 1}
    b = {"x": 1, "y": 2}
    assert compare(a, b) is Ordering.CONCURRENT
    assert compare({"x": 1}, {"x": 2}) is Ordering.BEFORE
    assert compare({"x": 2}, {"x": 1}) is Ordering.AFTER
    assert compare({"x": 1}, {"x": 1}) is Ordering.EQUAL
    assert merge(a, b) == {"x": 2, "y": 2}


def test_version_chain_is_content_addressed():
    flow = ContextFlow("kernel")
    first = flow.emit(EventKind.CAPTURE, paths=["capture.a"], payload={"n": 1})
    head = flow.head
    assert flow.version_of(first.id) == head
    assert flow.is_ancestor(head)

    other = ContextFlow("kernel")
    other.emit(EventKind.CAPTURE, paths=["capture.a"], payload={"n": 1})
    # Different event ids produce different chains even for identical payloads.
    assert other.head != head


def test_keyring_materializes_latest_state():
    flow = ContextFlow("kernel")
    flow.emit(EventKind.SHAPE, paths=["shape.orders"], payload={"shape_digest": "a"})
    flow.emit(EventKind.SHAPE, paths=["shape.orders"], payload={"shape_digest": "b"})
    entry = flow.keyring.require("shape.orders")
    assert entry.revision == 2
    assert entry.shape_digest == "b"
    assert entry.symbol == "ShapeOrders"
    assert len(flow.keyring.history("shape.orders")) == 2


def test_stale_write_to_same_path_is_refused():
    flow = ContextFlow("kernel")
    guard = RollbackManager(flow).guard("agent-a")
    flow.emit(EventKind.CODEGEN, paths=["code.orders"], actor="agent-b")

    with pytest.raises(TimetravelConflict):
        guard.commit(EventKind.CODEGEN, paths=["code.orders"], payload={"stale": True})

    conflict = guard.conflicts[0]
    assert conflict.actor == "agent-a"
    assert "code.orders" in conflict.paths
    assert "same keyring paths" in conflict.detail


def test_stale_write_to_disjoint_path_lands():
    flow = ContextFlow("kernel")
    guard = RollbackManager(flow).guard("agent-a")
    flow.emit(EventKind.CODEGEN, paths=["code.orders"], actor="agent-b")

    event = guard.commit(EventKind.SHAPE, paths=["shape.customers"], payload={"ok": True})
    assert event.id in {e.id for e in flow}


def test_rollback_removes_state_and_supersedes_versions():
    flow = ContextFlow("kernel")
    flow.emit(EventKind.CAPTURE, paths=["capture.orders"])
    checkpoint = flow.checkpoint("before")
    flow.emit(EventKind.CODEGEN, paths=["code.orders"])
    flow.emit(EventKind.SHAPE, paths=["shape.customers"])
    assert flow.keyring.paths() == ["capture.orders", "code.orders", "shape.customers"]

    flow.rollback("before", reason="test")
    assert flow.keyring.paths() == ["capture.orders"]
    assert len(flow.superseded()) == 2
    # The rollback itself is recorded going forward.
    assert flow.events(kind=EventKind.ROLLBACK)


def test_write_from_superseded_version_is_refused():
    flow = ContextFlow("kernel")
    checkpoint = flow.checkpoint("start")
    flow.emit(EventKind.CODEGEN, paths=["code.orders"])
    stale_version = flow.head
    flow.rollback("start", reason="test")

    ghost = KeyringEvent(kind=EventKind.CODEGEN, actor="ghost",
                         paths=("code.orders",), base_version=stale_version)
    with pytest.raises(TimetravelConflict):
        flow.append(ghost)


def test_rollback_scope_rewinds_on_failure():
    flow = ContextFlow("kernel")
    manager = RollbackManager(flow)
    flow.emit(EventKind.CAPTURE, paths=["capture.a"])
    baseline = flow.keyring.paths()

    with manager.scope("risky", reraise=False):
        flow.emit(EventKind.CODEGEN, paths=["code.a"])
        raise RuntimeError("boom")

    assert flow.keyring.paths() == baseline
    assert manager.history[-1].rolled_back
    assert "boom" in manager.history[-1].error


def test_rollback_scope_keeps_work_on_success():
    flow = ContextFlow("kernel")
    manager = RollbackManager(flow)
    with manager.scope("safe"):
        flow.emit(EventKind.CODEGEN, paths=["code.a"])
    assert "code.a" in flow.keyring.paths()
    assert manager.history[-1].ok


def test_unknown_rollback_target_raises():
    flow = ContextFlow("kernel")
    with pytest.raises(RollbackError):
        flow.rollback("never-existed")


def test_journal_round_trip(tmp_path):
    flow = ContextFlow("kernel")
    journal = Journal(tmp_path / "trace.jsonl").attach(flow)
    flow.emit(EventKind.CAPTURE, paths=["capture.a"], payload={"rows": 3})
    flow.checkpoint("cp")
    flow.emit(EventKind.SHAPE, paths=["shape.a"], payload={"shape_digest": "zz"})
    journal.flush()

    replayed = journal.load()
    assert replayed.depth == flow.depth
    assert replayed.keyring.paths() == flow.keyring.paths()
    assert journal.summary()["by_kind"]["shape"] == 1
    journal.close()


def test_journal_skips_a_torn_trailing_line(tmp_path):
    path = tmp_path / "trace.jsonl"
    flow = ContextFlow("kernel")
    journal = Journal(path).attach(flow)
    flow.emit(EventKind.CAPTURE, paths=["capture.a"])
    journal.close()

    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"version": "v1", "eve')  # crash mid-write

    assert len(list(Journal(path).records())) == 1
