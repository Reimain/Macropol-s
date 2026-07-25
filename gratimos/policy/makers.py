"""The policymakers themselves.

Four decisions recur throughout a run, and each gets a named policymaker:

* **storage** — hold it, stage it locally, or push it to cold storage;
* **routing** — which channel should carry an entity nothing claimed;
* **codegen** — regenerate now, wait for more evidence, or stop and ask;
* **migration** — is this shape change safe to apply unattended?

They are assembled here as ordinary rules so a deployment can override any one
of them by registering a higher-priority rule, without forking the kernel.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contextflow import ContextFlow
from ..meta import DataShape, ShapeChange, Wrapped
from ..storage.connectors import REGISTRY, ConnectorRegistry
from .rules import PolicyEngine, Situation, Verdict

# --- situation kinds -----------------------------------------------------

STORAGE = "storage"
ROUTING = "routing"
CODEGEN = "codegen"
MIGRATION = "migration"
ADMISSION = "admission"

# --- storage actions -----------------------------------------------------

HOLD = "hold"                 # keep it in memory
STAGE_LOCAL = "stage_local"   # secured local repository
STAGE_COLD = "stage_cold"     # S3 / Delta / cloud
REJECT = "reject"

# --- codegen actions -----------------------------------------------------

GENERATE = "generate"
REGENERATE = "regenerate"
WAIT = "wait"                 # not enough evidence yet
ESCALATE = "escalate"         # a human or a peer agent decides

# --- migration actions ---------------------------------------------------

AUTO_APPLY = "auto_apply"
STAGE_REVIEW = "stage_review"
BLOCK = "block"

#: Below this many observed rows, a shape is a guess rather than a belief.
MIN_ROWS_FOR_CODEGEN = 3
#: Payloads larger than this go straight to storage without trying memory.
LARGE_PAYLOAD_BYTES = 32 << 20


def storage_situation(payload: Wrapped, *, pressure: float, free_bytes: int,
                      cold_available: bool) -> Situation:
    return Situation(
        kind=STORAGE,
        subject=payload.name,
        facts={
            "bytes": payload.nbytes(),
            "rows": payload.rows,
            "pressure": pressure,
            "free_bytes": free_bytes,
            "cold_available": cold_available,
            "probe": payload.provenance.probe,
            "is_media": payload.provenance.probe in ("image", "video"),
        },
    )


def codegen_situation(entity: str, shape: DataShape, *, revision: int,
                      changes: list[ShapeChange], generated_digest: str = "") -> Situation:
    return Situation(
        kind=CODEGEN,
        subject=entity,
        facts={
            "rows": shape.rows,
            "fields": len(shape.fields),
            "revision": revision,
            "digest": shape.digest,
            "generated_digest": generated_digest,
            "up_to_date": generated_digest == shape.digest,
            "changes": len(changes),
            "breaking": sum(1 for c in changes if c.breaking),
            "all_nullable": all(f.nullable for f in shape.fields) if shape.fields else True,
        },
    )


def migration_situation(entity: str, changes: list[ShapeChange], *, rows: int,
                        has_downgrade: bool = True, creating: bool = False) -> Situation:
    return Situation(
        kind=MIGRATION,
        subject=entity,
        facts={
            "creating": creating,
            "changes": len(changes),
            "breaking": sum(1 for c in changes if c.breaking),
            "removals": sum(1 for c in changes if c.kind.value == "removed"),
            "retypes": sum(1 for c in changes if c.kind.value == "retyped"),
            "additions": sum(1 for c in changes if c.kind.value == "added"),
            "rows": rows,
            "has_downgrade": has_downgrade,
        },
    )


def routing_situation(payload: Wrapped, *, channels: list[str]) -> Situation:
    return Situation(
        kind=ROUTING,
        subject=payload.name,
        facts={
            "probe": payload.provenance.probe,
            "rows": payload.rows,
            "fields": len(payload.shape.fields),
            "existing_channels": channels,
            "unrouted": not channels,
            "source": payload.provenance.source,
        },
    )


def install_default_policies(
    engine: PolicyEngine, *, connectors: ConnectorRegistry = REGISTRY
) -> PolicyEngine:
    """Register the kernel's out-of-the-box policy set.

    Every rule here is overridable: register one with a lower ``priority``
    number and it decides first.
    """

    # -- storage ---------------------------------------------------------

    engine.rule(
        "storage.oversized-to-cold", STORAGE,
        when=lambda s: s.number("bytes") > LARGE_PAYLOAD_BYTES and s.flag("cold_available"),
        then=STAGE_COLD,
        reason=f"payload exceeds {LARGE_PAYLOAD_BYTES} bytes and cold storage is reachable",
        priority=10, tier="cold",
    )
    engine.rule(
        "storage.oversized-to-local", STORAGE,
        when=lambda s: s.number("bytes") > LARGE_PAYLOAD_BYTES,
        then=STAGE_LOCAL,
        reason="payload is too large for memory and no cold connector is available",
        priority=15, tier="local",
    )
    engine.rule(
        "storage.media-is-not-held", STORAGE,
        when=lambda s: s.flag("is_media"),
        then=STAGE_LOCAL,
        reason="media payloads are staged by reference; only their metadata stays resident",
        priority=20, tier="local",
    )
    engine.rule(
        "storage.under-pressure", STORAGE,
        when=lambda s: s.number("pressure") >= 0.85 or s.number("bytes") > s.number("free_bytes"),
        then=STAGE_LOCAL,
        reason="memory is at or above the high-water mark",
        priority=30, tier="local",
    )
    engine.default(STORAGE, HOLD, "fits in memory with headroom to spare")

    # -- codegen ---------------------------------------------------------

    engine.rule(
        "codegen.already-current", CODEGEN,
        when=lambda s: s.flag("up_to_date"),
        then=WAIT,
        reason="generated code already matches the current shape digest",
        priority=10,
    )
    engine.rule(
        "codegen.insufficient-evidence", CODEGEN,
        when=lambda s: s.number("rows") < MIN_ROWS_FOR_CODEGEN,
        then=WAIT,
        reason=f"fewer than {MIN_ROWS_FOR_CODEGEN} rows observed; the shape is still a guess",
        priority=20,
    )
    engine.rule(
        "codegen.no-fields", CODEGEN,
        when=lambda s: s.number("fields") == 0,
        then=WAIT, reason="shape has no fields to model", priority=25,
    )
    engine.rule(
        "codegen.breaking-needs-review", CODEGEN,
        when=lambda s: s.number("breaking") > 0 and s.number("revision") > 1,
        then=ESCALATE,
        reason="the shape changed in a way that can break existing readers",
        priority=30,
    )
    engine.rule(
        "codegen.first-generation", CODEGEN,
        when=lambda s: s.number("revision") <= 1,
        then=GENERATE, reason="no module exists for this entity yet", priority=40,
    )
    engine.default(CODEGEN, REGENERATE, "the shape moved and the change is additive")

    # -- migration -------------------------------------------------------

    engine.rule(
        "migration.create-is-safe", MIGRATION,
        when=lambda s: s.flag("creating"),
        then=AUTO_APPLY,
        reason="creating a new entity cannot break a reader that does not exist yet",
        priority=5,
    )
    engine.rule(
        "migration.nothing-to-do", MIGRATION,
        when=lambda s: s.number("changes") == 0,
        then=WAIT, reason="no shape changes to migrate", priority=10,
    )
    engine.rule(
        "migration.irreversible", MIGRATION,
        when=lambda s: not s.flag("has_downgrade") and s.number("breaking") > 0,
        then=BLOCK,
        reason="a breaking change with no downgrade path cannot be rolled back",
        priority=20,
    )
    engine.rule(
        "migration.data-loss", MIGRATION,
        when=lambda s: s.number("removals") > 0 and s.number("rows") > 0,
        then=STAGE_REVIEW,
        reason="dropping a field discards observed data; hold for review",
        priority=30,
    )
    engine.rule(
        "migration.retype-review", MIGRATION,
        when=lambda s: s.number("retypes") > 0,
        then=STAGE_REVIEW,
        reason="an incompatible retype needs a decided conversion, not a guessed one",
        priority=40,
    )
    engine.rule(
        "migration.additive-is-safe", MIGRATION,
        when=lambda s: s.number("breaking") == 0,
        then=AUTO_APPLY,
        reason="additive and widening changes preserve every existing reader",
        priority=50,
    )
    engine.default(MIGRATION, STAGE_REVIEW, "change is not provably safe to apply unattended")

    # -- routing ---------------------------------------------------------

    engine.rule(
        "routing.media-lane", ROUTING,
        when=lambda s: s.get("probe") in ("image", "video"),
        then=_channel("media", keyspace="media"),
        reason="media has its own lane and its own retention", priority=10,
    )
    engine.rule(
        "routing.ops-lane", ROUTING,
        when=lambda s: s.get("probe") == "shell",
        then=_channel("scripts", keyspace="ops"),
        reason="operational artifacts are not business data", priority=20,
    )
    engine.rule(
        "routing.wide-records", ROUTING,
        when=lambda s: s.number("fields") >= 15,
        then=_channel("wide", keyspace="data"),
        reason="wide records are worth isolating for storage layout", priority=30,
    )
    engine.rule(
        "routing.records-lane", ROUTING,
        when=lambda s: s.number("rows") > 0,
        then=_channel("records", keyspace="data"),
        reason="ordinary tabular payload", priority=40,
    )
    engine.default(ROUTING, "residual", "nothing claimed this payload")

    return engine


def _channel(name: str, **params: Any):
    """An outcome that names a channel to route to."""
    def outcome(situation: Situation) -> Verdict:
        return Verdict(action=name, params={"channel": name, **params}, subject=situation.subject)
    return outcome


class Policymakers:
    """Convenience façade binding the engine to the four decision points."""

    def __init__(
        self,
        flow: ContextFlow | None = None,
        *,
        engine: PolicyEngine | None = None,
        connectors: ConnectorRegistry = REGISTRY,
    ) -> None:
        self.engine = engine if engine is not None else PolicyEngine(flow)
        self.connectors = connectors
        if not len(self.engine):
            install_default_policies(self.engine, connectors=connectors)

    @property
    def cold_available(self) -> bool:
        return any(s in self.connectors.available_schemes() for s in ("s3", "delta", "gs", "az"))

    def storage(self, payload: Wrapped, *, pressure: float, free_bytes: int) -> Verdict:
        return self.engine.decide(storage_situation(
            payload, pressure=pressure, free_bytes=free_bytes, cold_available=self.cold_available
        ))

    def routing(self, payload: Wrapped, *, channels: list[str]) -> Verdict:
        return self.engine.decide(routing_situation(payload, channels=channels))

    def codegen(self, entity: str, shape: DataShape, *, revision: int,
                changes: list[ShapeChange], generated_digest: str = "") -> Verdict:
        return self.engine.decide(codegen_situation(
            entity, shape, revision=revision, changes=changes, generated_digest=generated_digest
        ))

    def migration(self, entity: str, changes: list[ShapeChange], *, rows: int,
                  has_downgrade: bool = True, creating: bool = False) -> Verdict:
        return self.engine.decide(migration_situation(
            entity, changes, rows=rows, has_downgrade=has_downgrade, creating=creating
        ))

    def report(self) -> dict[str, Any]:
        return {
            "cold_available": self.cold_available,
            "connectors": self.connectors.available_schemes(),
            **self.engine.report(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Policymakers rules={len(self.engine)} cold={self.cold_available}>"
