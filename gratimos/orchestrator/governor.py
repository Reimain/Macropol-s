"""The governor: point it at an environment and let it build.

One cycle is: **sense → absorb → decide → generate → migrate → transform →
reflect**. Every stage is gated by the configured :class:`~.depth.Depth`, every
stage records what it did on the ContextFlow, and the whole cycle runs inside a
rollback scope — so a cycle that fails halfway leaves the context where it
started rather than half-built.

The loop stops when the depth is satisfied: either the budget runs out, or a
cycle produces nothing new. "Nothing new" is the interesting condition — it
means the kernel has finished modelling what it can see, which is what
*governing the context* actually looks like from the outside.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..a2a import AgentRegistry
from ..codegen import ConflictPolicy, Generation, ModuleRegistry
from ..contextflow import ContextFlow, EventKind
from ..errors import CodegenError, GratimosError, TransformError
from ..hubs import DataHub, MemoryBudget, Placement
from ..ids import iso, new_id, now_ns
from ..meta import Wrapped
from ..migrations import AlembicAdapter, MigrationLedger
from ..policy import (
    AUTO_APPLY,
    ESCALATE,
    GENERATE,
    REGENERATE,
    STAGE_REVIEW,
    WAIT,
    Policymakers,
)
from ..probes import (
    AccessPolicy,
    Discovery,
    ExecPolicy,
    ProbeRegistry,
    default_registry,
)
from ..storage import LocalRepository
from ..trace import Journal, RollbackManager
from ..transforms import DEFAULT_POLICY, SandboxPolicy, TransformRegistry
from .depth import STANDARD, Budget, Depth


@dataclass(slots=True)
class Workspace:
    """Where the kernel keeps what it builds.

    Everything the run produces lands under one root, so a run is inspectable,
    archivable, and deletable as a unit.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        for path in (self.generated, self.transformations, self.spill, self.raw, self.migrations):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def generated(self) -> Path:
        return self.root / "generated"

    @property
    def transformations(self) -> Path:
        return self.root / "transformations"

    @property
    def spill(self) -> Path:
        return self.root / "spill"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def migrations(self) -> Path:
        return self.root / "migrations"

    @property
    def trace(self) -> Path:
        return self.root / "trace.jsonl"

    def to_dict(self) -> dict[str, Any]:
        return {"root": str(self.root), "generated": str(self.generated),
                "transformations": str(self.transformations), "trace": str(self.trace)}


@dataclass(slots=True)
class CycleReport:
    """What one pass through the environment accomplished."""

    index: int
    discovered: int = 0
    published: int = 0
    entities: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    regenerated: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    migrations_proposed: list[str] = field(default_factory=list)
    migrations_applied: list[str] = field(default_factory=list)
    migrations_held: list[str] = field(default_factory=list)
    transformed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events_added: int = 0
    duration_s: float = 0.0
    rolled_back: bool = False

    @property
    def productive(self) -> bool:
        """True when this cycle changed the context in some way."""
        return bool(
            self.generated or self.regenerated or self.migrations_proposed
            or self.transformed or self.published
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.index,
            "discovered": self.discovered,
            "published": self.published,
            "entities": self.entities,
            "generated": self.generated,
            "regenerated": self.regenerated,
            "escalated": self.escalated,
            "migrations": {
                "proposed": self.migrations_proposed,
                "applied": self.migrations_applied,
                "held": self.migrations_held,
            },
            "transformed": self.transformed,
            "conflicts": self.conflicts,
            "warnings": self.warnings[:20],
            "events_added": self.events_added,
            "duration_s": round(self.duration_s, 3),
            "productive": self.productive,
            "rolled_back": self.rolled_back,
        }


@dataclass(slots=True)
class GovernanceReport:
    """The outcome of a whole run."""

    root: str
    depth: str
    cycles: list[CycleReport] = field(default_factory=list)
    stopped_because: str = ""
    started_ns: int = field(default_factory=now_ns)
    duration_s: float = 0.0
    head: str = ""

    @property
    def entities(self) -> list[str]:
        return sorted({e for c in self.cycles for e in c.entities})

    @property
    def stable(self) -> bool:
        return bool(self.cycles) and not self.cycles[-1].productive

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "depth": self.depth,
            "started": iso(self.started_ns),
            "duration_s": round(self.duration_s, 3),
            "cycles": [c.to_dict() for c in self.cycles],
            "entities": self.entities,
            "stopped_because": self.stopped_because,
            "stable": self.stable,
            "head": self.head,
        }

    def summary(self) -> str:
        """A few lines a human can read without opening the JSON."""
        generated = sorted({m for c in self.cycles for m in c.generated + c.regenerated})
        escalated = sorted({e for c in self.cycles for e in c.escalated})
        lines = [
            f"depth={self.depth}  cycles={len(self.cycles)}  {self.duration_s:.2f}s",
            f"entities  : {', '.join(self.entities) or 'none'}",
            f"generated : {', '.join(generated) or 'none'}",
        ]
        if escalated:
            lines.append(f"escalated : {', '.join(escalated)} (breaking shape change)")
        held = sorted({m for c in self.cycles for m in c.migrations_held})
        if held:
            lines.append(f"held      : {len(held)} migration(s) awaiting review")
        lines.append(f"stopped   : {self.stopped_because}")
        return "\n".join(lines)


class Governor:
    """Runs the sense-to-govern loop over one environment."""

    def __init__(
        self,
        root: str | Path,
        workspace: str | Path | None = None,
        *,
        depth: Depth | str | int = Depth.GENERATE,
        budget: Budget = STANDARD,
        actor: str = "governor",
        probes: ProbeRegistry | None = None,
        access_policy: AccessPolicy | None = None,
        exec_policy: ExecPolicy | None = None,
        sandbox_policy: SandboxPolicy = DEFAULT_POLICY,
        conflict_policy: ConflictPolicy = ConflictPolicy.RAISE,
        agents: AgentRegistry | None = None,
        journal: bool = True,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.depth = Depth.parse(depth)
        self.budget = budget
        self.actor = actor

        self.workspace = Workspace(Path(workspace) if workspace else self.root / ".gratimos")
        self.flow = ContextFlow(actor=actor, name=f"gratimos:{self.root.name}")
        self.rollback = RollbackManager(self.flow)
        self.journal = Journal(self.workspace.trace).attach(self.flow) if journal else None

        self.probes = probes if probes is not None else default_registry(
            access_policy=access_policy or AccessPolicy(),
            exec_policy=exec_policy,
        )
        self.hub = DataHub(
            flow=self.flow,
            budget=MemoryBudget(budget.memory_bytes, per_payload_limit=budget.per_payload_bytes),
            store=LocalRepository(self.workspace.spill, name="spill"),
            actor=actor,
        )
        self.modules = ModuleRegistry(
            self.workspace.generated, flow=self.flow, conflict_policy=conflict_policy
        )
        self.transforms = TransformRegistry(
            self.workspace.transformations, policy=sandbox_policy, flow=self.flow
        )
        self.policy = Policymakers(self.flow)
        self.ledger = MigrationLedger(self.flow)
        self.alembic = AlembicAdapter()
        self.agents = agents if agents is not None else AgentRegistry(self.flow, actor=actor)
        self._seen_digests: dict[str, str] = {}
        self._applied_transforms: set[str] = set()

    # -- the loop --------------------------------------------------------

    def run(self, *, cycles: int = 0) -> GovernanceReport:
        """Run until the depth is satisfied or the budget is spent."""
        limit = cycles or self.budget.max_cycles
        report = GovernanceReport(root=str(self.root), depth=self.depth.label)
        started = time.monotonic()

        self.flow.emit(
            EventKind.NOTE,
            actor=self.actor,
            payload={
                "run": new_id("run"), "root": str(self.root), "depth": self.depth.label,
                "intent": self.depth.describe(), "budget": self.budget.to_dict(),
            },
            labels={"stage": "start"},
        )

        for index in range(1, limit + 1):
            elapsed = time.monotonic() - started
            if elapsed >= self.budget.max_seconds:
                report.stopped_because = f"time budget of {self.budget.max_seconds}s reached"
                break
            cycle = self.cycle(index)
            report.cycles.append(cycle)
            if cycle.rolled_back:
                report.stopped_because = f"cycle {index} failed and was rolled back"
                break
            if self.budget.stop_when_stable and not cycle.productive:
                report.stopped_because = "context is stable: a full cycle changed nothing"
                break
        else:
            report.stopped_because = f"cycle budget of {limit} reached"

        report.duration_s = time.monotonic() - started
        report.head = self.flow.head
        if self.journal is not None:
            self.journal.flush()
        return report

    def cycle(self, index: int = 1) -> CycleReport:
        """One sense-to-reflect pass, atomic with respect to the flow."""
        report = CycleReport(index=index)
        started = time.monotonic()
        before = self.flow.depth

        try:
            with self.rollback.scope(f"cycle-{index}", actor=self.actor, reraise=False):
                discovery = self._sense(report)
                self._absorb(discovery, report)
                if self.depth.allows(Depth.MODEL):
                    self._decide(report)
                if self.depth.allows(Depth.TRANSFORM):
                    self._transform(report)
                if self.depth.allows(Depth.GOVERN):
                    self._govern(report)
        except Exception as exc:  # noqa: BLE001 - the scope already rewound the flow
            report.rolled_back = True
            report.warnings.append(f"cycle failed: {type(exc).__name__}: {exc}")

        if self.rollback.history and self.rollback.history[-1].rolled_back:
            report.rolled_back = True
            report.warnings.append(self.rollback.history[-1].error)

        report.events_added = max(self.flow.depth - before, 0)
        report.duration_s = time.monotonic() - started
        return report

    # -- stages ----------------------------------------------------------

    def _sense(self, report: CycleReport) -> Discovery:
        """Walk the environment and read whatever a probe recognizes."""
        discovery = self.probes.scan(
            self.root,
            limit=self.budget.max_rows_per_payload,
            max_files=self.budget.max_files,
        )
        report.discovered = len(discovery.captures)
        report.warnings.extend(discovery.warnings()[:20])
        self.flow.emit(
            EventKind.CAPTURE,
            actor=self.actor,
            paths=[f"capture.{c.target.entity}" for c in discovery.captures] or ["capture.none"],
            payload={
                "root": str(self.root), "captures": len(discovery.captures),
                "payloads": len(discovery.payloads), "rows": discovery.rows,
                "by_probe": discovery.by_probe(), "skipped": len(discovery.skipped),
                "contested": len(discovery.contested),
            },
            labels={"stage": "sense"},
        )
        return discovery

    def _absorb(self, discovery: Discovery, report: CycleReport) -> None:
        """Route and hold what was captured, skipping payloads that did not move."""
        placements: list[Placement] = []
        for capture in discovery.captures:
            for payload in capture.payloads:
                previous = self._seen_digests.get(payload.name)
                if previous == payload.digest:
                    continue  # identical to last cycle; nothing to re-absorb
                self._seen_digests[payload.name] = payload.digest
                placements.append(self.hub.publish(payload, stage="absorb"))
        report.published = len(placements)
        report.entities = sorted({p.entity for p in placements})
        for placement in placements:
            report.warnings.extend(f"{placement.entity}: {w}" for w in placement.warnings)

    def _decide(self, report: CycleReport) -> None:
        """Ask the policymakers what to do about each entity, then do it."""
        for entity in self.hub.entities():
            revision = self.hub.meta.current(entity)
            if revision is None:
                continue
            current = self.modules.current(entity)
            verdict = self.policy.codegen(
                entity, revision.shape,
                revision=revision.revision,
                changes=list(revision.changes),
                generated_digest=current.shape_digest if current else "",
            )

            if verdict.action == ESCALATE:
                report.escalated.append(entity)
                self._escalate(entity, revision, report)
            elif verdict.action in (GENERATE, REGENERATE) and self.depth.allows(Depth.GENERATE):
                self._generate(entity, revision, verdict.action, report)
            elif verdict.action == WAIT:
                continue

            self._plan_migration(entity, revision, report)

    def _generate(self, entity: str, revision: Any, action: str, report: CycleReport) -> None:
        try:
            generation: Generation = self.modules.generate(revision.shape, entity=entity)
        except CodegenError as exc:
            report.warnings.append(f"{entity}: codegen failed: {exc}")
            report.conflicts.append(entity)
            return
        if generation.revision == 1:
            report.generated.append(generation.module_name)
        else:
            report.regenerated.append(generation.module_name)
        if not generation.clean:
            report.conflicts.append(entity)
            report.warnings.append(
                f"{entity}: {len(generation.conflicts)} unresolved merge conflict(s)"
            )
        try:
            self.modules.generate_proto(revision.shape, entity=entity)
        except CodegenError as exc:
            report.warnings.append(f"{entity}: proto generation failed: {exc}")

    def _plan_migration(self, entity: str, revision: Any, report: CycleReport) -> None:
        """Record a migration for the shape change, and decide whether to apply it."""
        creating = revision.revision == 1
        if creating:
            proposed = self.ledger.create_entity(revision.shape)
        else:
            proposed = self.ledger.propose(
                entity, list(revision.changes), shape=revision.shape,
                message=f"{entity} revision {revision.revision}",
            )
        if proposed is None:
            return
        report.migrations_proposed.append(proposed.revision)

        verdict = self.policy.migration(
            entity, list(revision.changes), rows=revision.shape.rows,
            has_downgrade=proposed.reversible, creating=creating,
        )
        # The ledger is a chain: a revision cannot land while an older one for
        # the same entity is still pending review. Applying out of order is a
        # ledger error, so check here rather than letting it raise.
        pending = self.ledger.pending(entity)
        blocked = bool(pending) and pending[0].revision != proposed.revision

        if verdict.action == AUTO_APPLY and self.depth.allows(Depth.GOVERN) and not blocked:
            self.ledger.apply(proposed.revision)
            report.migrations_applied.append(proposed.revision)
            return

        report.migrations_held.append(proposed.revision)
        if blocked:
            report.warnings.append(
                f"{entity}: migration {proposed.revision[:12]} is queued behind "
                f"{pending[0].revision[:12]}, which is awaiting review"
            )
        if verdict.action == STAGE_REVIEW:
            self.alembic.render(proposed).write(self.workspace.migrations)

    def _transform(self, report: CycleReport) -> None:
        """Run every approved transformation matching each held entity."""
        self.transforms.discover()
        for rejected in self.transforms.rejected():
            report.warnings.append(
                f"transformation {rejected.name!r} rejected: {'; '.join(rejected.inspection.reasons[:2])}"
            )
        for entity in self.hub.entities():
            payload = self.hub.latest(entity)
            if payload is None:
                continue
            for transformation in sorted(self.transforms.for_entity(entity), key=lambda t: t.name):
                # A transformation is a function of its input: re-running it on
                # a payload it has already consumed produces the same output and
                # would keep every cycle looking "productive" forever.
                fingerprint = f"{transformation.name}@{transformation.source_digest}:{payload.digest}"
                if fingerprint in self._applied_transforms:
                    continue
                self._applied_transforms.add(fingerprint)
                outcome = self.transforms.apply(transformation, payload)
                if not outcome.ok:
                    report.warnings.append(
                        f"{entity}: transformation {outcome.transformation!r} failed: {outcome.error}"
                    )
                    break
                report.transformed.append(f"{entity}:{outcome.transformation}")
                if outcome.payload is not None:
                    self.hub.publish(outcome.payload, stage="transform")
                    payload = outcome.payload

    def _govern(self, report: CycleReport) -> None:
        """Bring peers in on whatever the kernel could not settle itself."""
        if not len(self.agents):
            return
        unresolved = sorted(set(report.escalated) | set(report.conflicts))
        for entity in unresolved:
            payload = self.hub.latest(entity)
            if payload is None:
                continue
            try:
                exchange = self._consult(entity, payload)
            except GratimosError as exc:
                report.warnings.append(f"{entity}: no peer could review it: {exc}")
                continue
            if exchange is not None and exchange.ok:
                report.warnings.append(f"{entity}: peer review — {exchange.text()[:200]}")

    def _consult(self, entity: str, payload: Wrapped) -> Any:
        peer = self.agents.best_for("analyze") or self.agents.best_for()
        if peer is None:
            return None
        return peer.client.delegate(
            payload, skill="analyze" if peer.can("analyze") else "",
            instruction=(
                f"The shape of {entity!r} changed in a way that may break existing readers. "
                "Assess the risk and recommend whether to adopt it."
            ),
        )

    # -- reporting -------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Everything the kernel currently believes, in one object."""
        return {
            "root": str(self.root),
            "workspace": self.workspace.to_dict(),
            "depth": self.depth.label,
            "budget": self.budget.to_dict(),
            "flow": {"head": self.flow.head, "depth": self.flow.depth,
                     "checkpoints": [c.name for c in self.flow.checkpoints()]},
            "hub": self.hub.report(),
            "catalog": self.hub.meta.catalog(),
            "modules": self.modules.report(),
            "transforms": self.transforms.report(),
            "migrations": self.ledger.report(),
            "policy": self.policy.report(),
            "agents": self.agents.catalog(),
        }

    def close(self) -> None:
        if self.journal is not None:
            self.journal.close()
        self.modules.unload_all()

    def __enter__(self) -> "Governor":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Governor {self.root} depth={self.depth.label} entities={len(self.hub.entities())}>"


def govern(
    root: str | Path,
    *,
    depth: Depth | str | int = Depth.GENERATE,
    budget: Budget = STANDARD,
    workspace: str | Path | None = None,
    **kwargs: Any,
) -> GovernanceReport:
    """Point the kernel at an environment and let it run to its depth."""
    with Governor(root, workspace, depth=depth, budget=budget, **kwargs) as governor:
        return governor.run()
