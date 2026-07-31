"""The engine — one object that wires the platform together.

Everything below is already independent: the ledger does not know about the
graph, the graph does not know about the station, the station does not know
about the UI. This assembles them, and it is the only place that knows the
assembly order.

That order is not arbitrary. Projections must be subscribed before any command
is dispatched, or the first events would land in the ledger with nothing
listening and the read models would start out silently behind. The engine
therefore builds every projection first and attaches the buses last.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .binding.resolver import Resolver
from .core.commands import DeclareEnvironment, RecordObservation
from .core.handlers import CommandBus
from .core.queries import QueryBus
from .domain.finding import Gap, GapKind, merge_gaps
from .domain.reasoning import Guidance, Question, ReasoningPath, ReasoningStep
from .environment.loader import load, loads, skeleton
from .environment.manifest import Manifest, Target
from .environment.reconcile import (
    Reconciliation,
    findings as reconciliation_findings,
    reconcile,
)
from .errors import SlpieError
from .graph.query import GraphProjection
from .graph.snapshot import Snapshot, SnapshotStore
from .graph.sqlite_graph import SqliteGraph
from .graph.traversal import Traverser
from .ledger.projection import (
    CountingProjection,
    FindingProjection,
    ProjectionSet,
    SourceProjection,
    StationProjection,
)
from .ledger.sqlite_ledger import open_ledger
from .station.registry import Station


@dataclass(slots=True)
class Engine:
    """The assembled platform: ledger, graph, station, buses."""

    manifest: Manifest | None = None
    ledger: Any = None
    graph: SqliteGraph | None = None
    commands: CommandBus | None = None
    queries: QueryBus | None = None
    projections: ProjectionSet | None = None
    station: Station | None = None
    resolver: Resolver | None = None
    world: Any = None
    registry: Any = None
    snapshots: SnapshotStore | None = None
    #: What the last scan read. Kept so `ask` can reason over it without walking
    #: the tree again — a console that re-scanned per question would be unusable
    #: on any tree worth asking about.
    observed: tuple[Any, ...] = ()
    stream: Any = None
    started_at: int = 0

    # -- construction ----------------------------------------------------

    @classmethod
    def create(
        cls,
        manifest: Manifest | None = None,
        *,
        ledger_path: str | Path | None = None,
        graph_path: str | Path | None = None,
    ) -> "Engine":
        """Build a platform. Everything in memory unless given paths."""
        ledger = open_ledger(ledger_path)
        graph = SqliteGraph(graph_path)
        commands = CommandBus(ledger)

        # Subscribed before the first dispatch, so no read model starts behind.
        projections = ProjectionSet(
            GraphProjection(graph),
            StationProjection(),
            FindingProjection(),
            SourceProjection(),
            CountingProjection(),
        )
        projections.subscribe_to(commands.events)

        engine = cls(
            manifest=manifest,
            ledger=ledger,
            graph=graph,
            commands=commands,
            queries=QueryBus(projections, ledger),
            projections=projections,
            snapshots=SnapshotStore(graph),
            started_at=time.time_ns(),
        )
        return engine

    @classmethod
    def from_manifest(
        cls,
        path: str | Path,
        *,
        ledger_path: str | Path | None = None,
        graph_path: str | Path | None = None,
    ) -> "Engine":
        return cls.create(
            load(path), ledger_path=ledger_path, graph_path=graph_path
        )

    @classmethod
    def from_text(cls, text: str, *, source_uri: str = "slpie://manifest") -> "Engine":
        return cls.create(loads(text, source_uri=source_uri))

    # -- declare-first ---------------------------------------------------

    def declare(self) -> int:
        """Ingest the manifest, producing the skeleton graph.

        Returns the node count. This is the millisecond path — no I/O beyond
        reading the manifest, and the graph is answerable immediately after.
        """
        self._require_manifest()
        nodes, edges = skeleton(self.manifest)

        self.commands.dispatch(DeclareEnvironment(
            environment=self.manifest.environment,
            target=self.manifest.target.value,
            declarations=tuple(d.to_dict() for d in self.manifest),
            manifest_digest=self.manifest.digest,
            source_uri=self.manifest.source_uri,
        ))
        self.commands.dispatch(RecordObservation(
            nodes=nodes, edges=edges,
            source_uri=self.manifest.source_uri or "slpie://manifest",
            discoverer="environment.manifest",
        ))
        return len(nodes)

    def simulate(self, *, root: str | Path | None = None, **options: Any) -> Any:
        """Materialise the declared world and bind to it."""
        from .simulator.world import SimulatedWorld

        self._require_manifest()
        self.world = SimulatedWorld.build(self.manifest, root=root, **options)
        self.resolver = self.world.bind()
        self.station = Station(self.commands, self.resolver, clock=self.world.clock)
        return self.world

    def attach(self, *, wanted: Iterable[str] = ()) -> tuple[Any, ...]:
        """Register every declared element and negotiate capabilities."""
        self._require_manifest()
        if self.station is None:
            raise SlpieError("nothing is bound; call simulate() or bind() first")
        return self.station.attach_all(self.manifest, wanted=wanted)

    def fire(self, scenario: str, **parameters: Any) -> Any:
        from .simulator.scenarios import fire

        if self.world is None:
            raise SlpieError("no simulated world; call simulate() first")
        return fire(scenario, self.world, **parameters)

    # -- reading ---------------------------------------------------------

    def reconcile(self) -> Reconciliation:
        self._require_manifest()
        return reconcile(self.manifest, self.graph)

    def reconciliation_findings(self) -> list[Any]:
        return reconciliation_findings(self.reconcile(), self.manifest)

    def traverser(self) -> Traverser:
        return Traverser(self.graph)

    def seal(self, *, label: str = "") -> Snapshot:
        return self.snapshots.seal(ledger_version=self.ledger.version, label=label)

    def gaps(self) -> tuple[Gap, ...]:
        """Everything limiting the platform's answers right now.

        Collected from the station's refusals and from the binding layer, which
        is the only place that knows whether an answer came from the simulator.
        Computed on demand rather than accumulated, so a gap that has been
        closed stops being reported without anyone clearing it.
        """
        return merge_gaps(
            self.station.gaps() if self.station else (),
            self.resolver.gaps() if self.resolver else (),
            self.registry.gaps() if self.registry is not None else (),
        )

    def ask(self, question: str) -> Guidance:
        """Answer a question: the layers' conclusions, their limits, what next.

        The engine's own gaps — refused capabilities, stale heartbeats, elements
        that were declared and never attached — are merged with the pipeline's,
        because both limit the same answer and an operator reading it should not
        have to consult two lists to learn what it rests on.

        A question asked before anything has been scanned is answered honestly
        rather than refused: the environment's gaps are real information, and
        `Guidance` is the shape they travel in whether or not the layers ran.
        """
        from .reasoning.guidance import guidance_for
        from .reasoning.pipeline import Pipeline

        observations = self.observed
        if not observations:
            return Guidance(
                answer=None,
                reasoning=ReasoningPath(question=question),
                gaps=self.gaps(),
                summary=(
                    "nothing has been scanned yet, so there is nothing to reason "
                    "over; this is what the environment declares it cannot see"
                ),
                next_questions=(
                    Question(
                        text="what is actually in the elements that are attached?",
                        intent="scan", information_gain=0.9,
                        rationale="the graph is a skeleton until discovery corroborates it",
                        parameters={"pipeline": "scan | link | findings"},
                    ),
                    Question(
                        text="where does what I declared disagree with what is there?",
                        intent="reconcile", information_gain=0.6,
                        parameters={"pipeline": "reconcile"},
                    ),
                ),
            )

        result = Pipeline().run(observations, element=self.environment)
        guidance = guidance_for(result, question=question, root=".")
        return replace(guidance, gaps=merge_gaps((*guidance.gaps, *self.gaps())))

    @property
    def environment(self) -> str:
        return self.manifest.environment if self.manifest else ""

    def plugins(self) -> Any:
        """The plugin registry, with the built-in discoverers registered.

        Built lazily so an engine that only ingests a manifest never pays for
        it, and cached so a scan does not re-register on every call.
        """
        from .discovery.registry import register_builtins
        from .plugins.registry import Registry

        if self.registry is None:
            self.registry = register_builtins(Registry(commands=self.commands))
        return self.registry

    def scan(self, *, actor: str = "scan") -> dict[str, Any]:
        """Run discovery over every bound element.

        Everything discovery produces goes through the same command as the
        manifest skeleton did, so the graph cannot tell where an observation
        came from — only what evidence supports it.
        """
        from .discovery.registry import Scanner

        if self.resolver is None:
            raise SlpieError("nothing is bound; call simulate() or bind() first")

        report = Scanner(self.plugins(), commands=self.commands).scan(
            self.resolver.bindings, actor=actor,
        )
        self.observed = report.captured
        return report.to_dict()

    # -- status ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        counts = self.graph.counts() if self.graph else {}
        return {
            "environment": self.manifest.environment if self.manifest else "",
            "target": self.manifest.target.value if self.manifest else "",
            "manifest_digest": self.manifest.digest if self.manifest else "",
            "declarations": len(self.manifest) if self.manifest else 0,
            "ledger_version": self.ledger.version if self.ledger else 0,
            "ledger_digest": (self.ledger.digest[:16] if self.ledger else ""),
            "graph": counts,
            "attached": len(self.station.attached) if self.station else 0,
            "plugins": len(self.registry) if self.registry is not None else 0,
            "gaps": len(self.gaps()),
            "simulated": self.world is not None,
            "uptime_ns": time.time_ns() - self.started_at,
        }

    def rebuild(self) -> dict[str, int]:
        """Refold every read model from sequence 0 — the recovery path."""
        return self.projections.rebuild(self.ledger.read())

    def close(self) -> None:
        if self.graph is not None:
            self.graph.close()
        if hasattr(self.ledger, "close"):
            self.ledger.close()
        if self.world is not None:
            self.world.tear_down()

    def _require_manifest(self) -> None:
        if self.manifest is None:
            raise SlpieError("this engine has no manifest; create it with one")

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - display only
        environment = self.manifest.environment if self.manifest else "unconfigured"
        return f"<Engine {environment} ledger={self.ledger.version if self.ledger else 0}>"
