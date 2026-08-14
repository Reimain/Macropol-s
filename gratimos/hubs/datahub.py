"""DataHub: where payloads live, and how they get where they are going.

The hub is the join between the four things that decide a payload's fate: the
router says which channels want it, the budget says whether it can stay in
memory, the spill manager stages it when it cannot, and the flow records all of
it. Publishing is a single call because those decisions have to be made together
— routing a payload you then failed to hold is how you get a dangling reference.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import HubError
from ..ids import now_ns
from ..meta import DataShape, Wrapped
from ..storage.base import MemoryStore, ObjectStore
from .channel import Router, RouteResult, default_channels
from .memory import MIB, MemoryBudget
from .metahub import MetaHub, ShapeRevision
from .spill import SpillManager, SpillRecord, Unspillable


@dataclass(slots=True)
class Placement:
    """What happened to one published payload."""

    payload: Wrapped
    route: RouteResult
    revision: ShapeRevision | None = None
    resident: bool = True
    spill: SpillRecord | None = None
    event_id: str = ""
    warnings: list[str] = field(default_factory=list)
    ts_ns: int = field(default_factory=now_ns)

    @property
    def entity(self) -> str:
        return self.payload.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "payload_id": self.payload.id,
            "resident": self.resident,
            "route": self.route.to_dict(),
            "revision": self.revision.revision if self.revision else None,
            "shape_digest": self.payload.shape.digest,
            "spill": self.spill.to_dict() if self.spill else None,
            "event_id": self.event_id,
            "warnings": self.warnings,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        where = "memory" if self.resident else "staged"
        return f"<Placement {self.entity} -> {where} channels={list(self.route.channels)}>"


class DataHub:
    """Holds payloads, routes them, and keeps memory honest."""

    def __init__(
        self,
        *,
        flow: ContextFlow | None = None,
        meta: MetaHub | None = None,
        router: Router | None = None,
        budget: MemoryBudget | None = None,
        store: ObjectStore | None = None,
        actor: str = "datahub",
        auto_channels: bool = True,
    ) -> None:
        # `is None` rather than `or`: an empty Router, MetaHub, ContextFlow, or
        # MemoryBudget is falsy by __len__, so `or` would silently discard the
        # caller's configured instance and substitute a default.
        self.flow = flow if flow is not None else ContextFlow(actor=actor)
        self.meta = meta if meta is not None else MetaHub(self.flow)
        self.router = router if router is not None else Router()
        if auto_channels and not len(self.router):
            default_channels(self.router)
        self.budget = budget if budget is not None else MemoryBudget(256 * MIB)
        self.spill = SpillManager(store if store is not None else MemoryStore("spill"))
        self.actor = actor
        self._payloads: dict[str, Wrapped] = {}
        self._by_entity: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        self.placements: list[Placement] = []

    # -- publish ---------------------------------------------------------

    def publish(self, payload: Wrapped, **labels: str) -> Placement:
        """Route, shape-register, and hold (or stage) a payload."""
        revision = self.meta.observe(payload, actor=self.actor)
        route = self.router.route(payload)
        placement = Placement(payload=payload, route=route, revision=revision)
        if route.unrouted:
            placement.warnings.append("no channel claimed this payload")

        held, spill_record, warning = self._place(payload)
        placement.payload = held
        placement.resident = held.resident
        placement.spill = spill_record
        if warning:
            placement.warnings.append(warning)

        with self._lock:
            self._payloads[held.id] = held
            self._by_entity.setdefault(held.name, []).append(held.id)

        paths = list(route.paths) or [f"residual.{payload.name}"]
        event = self.flow.emit(
            EventKind.ROUTE,
            actor=self.actor,
            paths=paths,
            payload={
                "payload_id": held.id,
                "entity": held.name,
                "rows": payload.rows,
                "bytes": payload.nbytes(),
                "resident": held.resident,
                "channels": list(route.channels),
                "shape_digest": payload.shape.digest,
                "revision": revision.revision if revision else 0,
            },
            labels={**dict(labels), "source": payload.provenance.source[:120]},
            storage=spill_record.storage if spill_record else None,
        )
        placement.event_id = event.id
        if spill_record is not None:
            self.flow.emit(
                EventKind.SPILL,
                actor=self.actor,
                paths=[f"spill.{held.name}"],
                payload={
                    "payload_id": held.id,
                    "entity": held.name,
                    "key": spill_record.key,
                    "encoding": spill_record.encoding,
                    "bytes": spill_record.nbytes,
                    "reason": "memory budget",
                },
                storage=spill_record.storage,
            )
        self.placements.append(placement)
        return placement

    def publish_all(self, payloads: Sequence[Wrapped], **labels: str) -> list[Placement]:
        return [self.publish(payload, **labels) for payload in payloads]

    def _place(self, payload: Wrapped) -> tuple[Wrapped, SpillRecord | None, str]:
        """Decide residency, evicting or staging as the budget requires."""
        nbytes = payload.nbytes()
        admission = self.budget.consider(payload.id, nbytes)

        if admission.admitted:
            for victim in admission.evict:
                self._evict(victim)
            try:
                self.budget.admit(payload.id, nbytes)
                return payload, None, ""
            except Exception as exc:  # noqa: BLE001 - fall through to staging
                warning = f"admission failed after eviction ({exc}); staging instead"
        else:
            warning = admission.reason

        try:
            evicted, record = self.spill.spill(payload, reason="budget")
        except Unspillable as exc:
            # Neither memory nor storage will take it: hold it and say so
            # loudly, rather than dropping data the kernel was asked to govern.
            self.budget.reject(str(exc))
            return payload, None, f"payload exceeds budget and cannot be staged: {exc}"
        return evicted, record, warning

    def _evict(self, payload_id: str) -> bool:
        """Move a resident payload to the spill tier."""
        with self._lock:
            payload = self._payloads.get(payload_id)
        if payload is None or not payload.resident:
            self.budget.release(payload_id)
            return False
        try:
            evicted, _record = self.spill.spill(payload, reason="eviction")
        except Unspillable:
            return False
        self.budget.release(payload_id)
        with self._lock:
            self._payloads[payload_id] = evicted
        return True

    # -- read ------------------------------------------------------------

    def get(self, payload_id: str, *, rehydrate: bool = True) -> Wrapped:
        payload = self._payloads.get(payload_id)
        if payload is None:
            raise HubError(f"unknown payload: {payload_id!r}")
        if payload.resident:
            self.budget.touch(payload_id)
            return payload
        if not rehydrate:
            return payload
        return self.spill.rehydrate(payload)

    def latest(self, entity: str, *, rehydrate: bool = True) -> Wrapped | None:
        ids = self._by_entity.get(entity)
        if not ids:
            return None
        return self.get(ids[-1], rehydrate=rehydrate)

    def all_of(self, entity: str, *, rehydrate: bool = False) -> list[Wrapped]:
        return [self.get(i, rehydrate=rehydrate) for i in self._by_entity.get(entity, ())]

    def entities(self) -> list[str]:
        return sorted(self._by_entity)

    def shape(self, entity: str) -> DataShape | None:
        return self.meta.shape(entity)

    # -- channels --------------------------------------------------------

    def plug(self, *args: Any, **kwargs: Any) -> Any:
        """Add a channel at runtime — delegates to the router."""
        return self.router.plug(*args, **kwargs)

    def unplug(self, name: str) -> bool:
        return self.router.unplug(name)

    # -- reporting -------------------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "entities": self.entities(),
            "payloads": len(self._payloads),
            "resident": sum(1 for p in self._payloads.values() if p.resident),
            "staged": sum(1 for p in self._payloads.values() if not p.resident),
            "memory": self.budget.report(),
            "spill": self.spill.report(),
            "router": self.router.to_dict(),
            "meta": self.meta.report(),
            "flow": {"head": self.flow.head, "depth": self.flow.depth},
        }

    def __contains__(self, entity: object) -> bool:
        return str(entity) in self._by_entity

    def __iter__(self) -> Iterator[Wrapped]:
        return iter(list(self._payloads.values()))

    def __len__(self) -> int:
        return len(self._payloads)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<DataHub entities={len(self._by_entity)} payloads={len(self._payloads)}>"
