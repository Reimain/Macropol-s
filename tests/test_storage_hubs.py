"""Storage containment, memory accounting, routing, and the spill tier."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fixtures import ORDERS

from gratimos.errors import HubError, StorageError, UnsafePathError
from gratimos.hubs import (
    ALWAYS,
    DataHub,
    Delivery,
    MemoryBudget,
    Router,
    SpillManager,
    Unspillable,
    by_probe,
    default_channels,
    has_fields,
    larger_than,
)
from gratimos.ids import StorageName
from gratimos.meta import Wrapped
from gratimos.storage import REGISTRY, LocalRepository, MemoryStore, resolve

# --- storage -------------------------------------------------------------


@pytest.mark.parametrize("key", ["../escape", "/etc/passwd", "a/../../b", "", "."])
def test_local_repository_refuses_paths_outside_its_root(tmp_path: Path, key: str):
    repo = LocalRepository(tmp_path / "repo")
    with pytest.raises(UnsafePathError):
        repo.resolve(key)


def test_local_repository_writes_owner_only(tmp_path: Path):
    repo = LocalRepository(tmp_path / "repo")
    name = StorageName.create("capture", "orders", ext="csv")
    ref = repo.put(name.key, b"id,total\n1,9.99\n", metadata={"probe": "csv"})

    path = repo.resolve(name.key)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert repo.get(name.key).startswith(b"id,total")
    assert repo.stat(name.key).metadata == {"probe": "csv"}
    assert ref.name.entity == "orders"


def test_storage_names_round_trip():
    name = StorageName.create("capture", "orders.csv", ext="csv")
    assert StorageName.parse(name.key).artifact_id == name.artifact_id
    assert name.symbol == "CaptureOrdersCsv"


def test_prune_keeps_only_the_newest(tmp_path: Path):
    repo = LocalRepository(tmp_path / "repo")
    for index in range(5):
        repo.put(StorageName.create("spill", f"e{index}").key, b"x")
    assert len(repo.prune("spill", keep=2)) == 3
    assert len(list(repo.list("spill"))) == 2


def test_unavailable_connectors_explain_themselves():
    assert "file" in REGISTRY.available_schemes()
    with pytest.raises(StorageError, match="boto3"):
        resolve("s3://bucket/prefix")
    with pytest.raises(StorageError, match="no connector"):
        resolve("frobnicate://x")


# --- memory --------------------------------------------------------------


def test_budget_admits_until_full_then_nominates_evictions():
    budget = MemoryBudget(1000, per_payload_limit=400)
    budget.admit("a", 400)
    budget.admit("b", 400)
    assert budget.free == 200

    admission = budget.consider("c", 400)
    assert admission.admitted
    assert admission.evict  # something must go first
    assert budget.under_pressure is False

    oversized = budget.consider("d", 900)
    assert not oversized.admitted
    assert "per-payload limit" in oversized.reason


def test_pinned_payloads_are_never_eviction_candidates():
    budget = MemoryBudget(1000, per_payload_limit=1000)
    budget.admit("keep", 500, pinned=True)
    budget.admit("drop", 400)
    assert budget.candidates(400) == ["drop"]


# --- routing -------------------------------------------------------------


def test_fallback_channel_only_sees_unclaimed_payloads():
    router = default_channels(Router())
    records = Wrapped.of(ORDERS, name="orders", probe="csv")
    media = Wrapped.of([{"width": 1}], name="logo", probe="image")

    assert [c.name for c in router.select(records)] == ["records"]
    assert [c.name for c in router.select(media)] == ["media"]


def test_tee_channels_always_receive_a_copy():
    router = default_channels(Router())
    seen: list[str] = []
    router.plug("audit", ALWAYS, delivery=Delivery.TEE, priority=1, sinks=[lambda p: seen.append(p.name)])
    result = router.route(Wrapped.of(ORDERS, name="orders", probe="csv"))
    assert "audit" in result.channels
    assert seen == ["orders"]


def test_predicates_compose_over_shape_and_provenance():
    payload = Wrapped.of(ORDERS, name="orders", probe="csv")
    assert has_fields("order_id", "total")(payload)
    assert not has_fields("nonexistent")(payload)
    assert by_probe("csv")(payload)
    assert larger_than(2)(payload)


def test_duplicate_channel_names_are_refused():
    router = Router()
    router.plug("x")
    with pytest.raises(HubError):
        router.plug("x")


# --- spill ---------------------------------------------------------------


def test_spill_round_trips_through_storage(tmp_path: Path):
    manager = SpillManager(LocalRepository(tmp_path / "spill"))
    payload = Wrapped.of(ORDERS, name="orders")
    evicted, record = manager.spill(payload)

    assert not evicted.resident
    assert evicted.value is None
    assert record.encoding == "json"
    assert manager.rehydrate(evicted).records() == payload.records()


def test_unspillable_payloads_are_reported_not_pickled(tmp_path: Path):
    manager = SpillManager(MemoryStore())
    payload = Wrapped.shaped(object(), Wrapped.of([{"a": 1}], name="t").shape)
    with pytest.raises(Unspillable, match="JSON-encodable"):
        manager.spill(payload)


# --- hub -----------------------------------------------------------------


def test_hub_stages_payloads_that_exceed_the_budget(tmp_path: Path):
    hub = DataHub(
        budget=MemoryBudget(2048, per_payload_limit=800),
        store=LocalRepository(tmp_path / "spill"),
    )
    for index in range(6):
        hub.publish(Wrapped.of(ORDERS, name=f"orders_{index}"))

    staged = [p for p in hub if not p.resident]
    assert staged, "the budget should have forced at least one spill"
    rehydrated = hub.get(staged[0].id)
    assert rehydrated.resident
    assert rehydrated.records()[0]["customer"] == "Ada"


def test_hub_registers_shape_revisions_as_data_evolves():
    hub = DataHub()
    hub.publish(Wrapped.of(ORDERS, name="orders"))
    hub.publish(Wrapped.of([{**ORDERS[0], "channel": "web"}], name="orders"))

    revision = hub.meta.current("orders")
    assert revision.revision == 2
    assert [c.field for c in revision.changes] == ["channel"]
    assert not revision.breaking
    assert hub.shape("orders").field("channel").optional


def test_re_publishing_identical_data_does_not_inflate_revisions():
    hub = DataHub()
    hub.publish(Wrapped.of(ORDERS, name="orders"))
    hub.publish(Wrapped.of(ORDERS, name="orders"))
    assert hub.meta.current("orders").revision == 1
