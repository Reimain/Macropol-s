"""The HTTP API — a thin skin over the query and command buses.

Every read route goes through the QueryBus and every write route through the
CommandBus, which means the interface has no privileges of its own. In
particular the live-target gate is *not* re-implemented here: `POST /api/target`
dispatches a `ChangeTarget` command, and the guard refuses it at the write side.
A UI that enforced its own version of that rule would be a second rule to keep
in sync, and the one an API client bypasses.

Routes are declared as data rather than as an if-chain so the route table is
inspectable — the UI fetches it to discover what this build supports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..core.commands import AskQuestion, ChangeTarget, FireScenario, SealSnapshot
from ..core.queries import (
    Causation,
    History,
    LedgerIntegrity,
    OpenFindings,
    ProjectionStatus,
    StationStatus,
)
from ..errors import SlpieError, TargetRefused

Handler = Callable[["Request"], Any]


@dataclass(frozen=True, slots=True)
class Request:
    """One parsed HTTP request."""

    method: str
    path: str
    query: Mapping[str, str]
    body: Mapping[str, Any]

    def param(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)

    def integer(self, name: str, default: int = 0) -> int:
        try:
            return int(self.query.get(name, default))
        except (TypeError, ValueError):
            return default


@dataclass(frozen=True, slots=True)
class Response:
    """One JSON response, with a status the server maps onto HTTP."""

    body: Any
    status: int = 200

    def encode(self) -> bytes:
        return json.dumps(self.body, default=str).encode("utf-8")


class Api:
    """Routes HTTP onto the buses. Holds no state of its own."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self._routes: dict[tuple[str, str], Handler] = {}
        self._register()

    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._routes[(method, path)] = handler
            return handler

        return register

    @property
    def routes(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._routes))

    def handle(self, request: Request) -> Response:
        handler = self._routes.get((request.method, request.path))
        if handler is None:
            return Response(
                {"error": f"no route for {request.method} {request.path}",
                 "routes": [f"{m} {p}" for m, p in self.routes]},
                status=404,
            )
        try:
            return Response(handler(request))
        except TargetRefused as error:
            # A refusal is an answer, not a server fault. 403 so the UI can
            # render the reason rather than a generic failure.
            return Response({"error": str(error), "refused": True}, status=403)
        except SlpieError as error:
            return Response({"error": str(error), "type": type(error).__name__}, status=400)
        except (KeyError, ValueError) as error:
            # An unknown scenario name or an unparseable parameter is the
            # caller's mistake, not the server's, and the message names what is
            # available. A 500 here would send an operator looking at logs.
            return Response(
                {"error": str(error).strip("'\""), "type": type(error).__name__}, status=400
            )
        except Exception as error:  # pragma: no cover - defensive
            return Response({"error": str(error), "type": type(error).__name__}, status=500)

    # -- routes ----------------------------------------------------------

    def _register(self) -> None:
        engine = self.engine

        @self.route("GET", "/api/status")
        def status(_request: Request) -> Any:
            return engine.status()

        @self.route("GET", "/api/routes")
        def routes(_request: Request) -> Any:
            return {"routes": [f"{m} {p}" for m, p in self.routes]}

        @self.route("GET", "/api/manifest")
        def manifest(_request: Request) -> Any:
            return engine.manifest.to_dict() if engine.manifest else {}

        @self.route("GET", "/api/station")
        def station(request: Request) -> Any:
            if element := request.param("element"):
                return engine.queries.ask(StationStatus(element=element)).value
            return engine.station.to_dict() if engine.station else {}

        @self.route("GET", "/api/graph")
        def graph(request: Request) -> Any:
            limit = request.integer("limit", 200)
            nodes = engine.graph.nodes(limit=limit)
            return {
                "counts": engine.graph.counts(),
                "by_kind": engine.graph.by_kind(),
                "confidence": engine.graph.confidence_distribution(),
                "nodes": [node.to_dict() for node in nodes],
                "edges": [edge.to_dict() for edge in engine.graph.edges(limit=limit)],
            }

        @self.route("GET", "/api/node")
        def node(request: Request) -> Any:
            found = engine.graph.node(request.param("id"))
            if found is None:
                return {"error": "no such node"}
            return {
                "node": found.to_dict(include_evidence=True),
                "out": [edge.to_dict() for edge in engine.graph.edges_from(found.id)],
                "in": [edge.to_dict() for edge in engine.graph.edges_to(found.id)],
            }

        @self.route("GET", "/api/search")
        def search(request: Request) -> Any:
            found = engine.graph.search(request.param("q"), limit=request.integer("limit", 20))
            return {"results": [node.to_dict() for node in found]}

        @self.route("GET", "/api/impact")
        def impact(request: Request) -> Any:
            from ..graph.traversal import Traverser

            result = Traverser(engine.graph).impact(
                request.param("id"),
                max_depth=request.integer("depth", 10),
                min_confidence=float(request.param("min_confidence", "0") or 0),
            )
            return result.to_dict()

        @self.route("GET", "/api/cycles")
        def cycles(_request: Request) -> Any:
            from ..graph.traversal import Traverser

            return {"cycles": [c.to_dict() for c in Traverser(engine.graph).cycles()]}

        @self.route("GET", "/api/reconcile")
        def reconcile(_request: Request) -> Any:
            return engine.reconcile().to_dict()

        @self.route("GET", "/api/findings")
        def findings(request: Request) -> Any:
            return {
                "findings": engine.queries.ask(
                    OpenFindings(severity=request.param("severity"))
                ).value
            }

        @self.route("GET", "/api/history")
        def history(request: Request) -> Any:
            return {
                "records": engine.queries.ask(History(
                    subject=request.param("subject"),
                    since=request.integer("since"),
                    limit=request.integer("limit", 100),
                )).value
            }

        @self.route("GET", "/api/causation")
        def causation(request: Request) -> Any:
            return {
                "chain": engine.queries.ask(
                    Causation(event_id=request.param("event"))
                ).value
            }

        @self.route("GET", "/api/integrity")
        def integrity(_request: Request) -> Any:
            return engine.queries.ask(LedgerIntegrity()).value

        @self.route("GET", "/api/projections")
        def projections(_request: Request) -> Any:
            return engine.queries.ask(ProjectionStatus()).value

        @self.route("GET", "/api/scenarios")
        def scenarios(_request: Request) -> Any:
            from ..simulator import available

            return {"scenarios": list(available())}

        @self.route("POST", "/api/ask")
        def ask(request: Request) -> Any:
            question = str(request.body.get("question", ""))
            engine.commands.dispatch(AskQuestion(question=question, actor="ui"))
            return engine.ask(question).to_dict()

        @self.route("POST", "/api/scenario")
        def scenario(request: Request) -> Any:
            name = str(request.body.get("scenario", ""))
            parameters = dict(request.body.get("parameters", {}))
            outcome = engine.fire(name, **parameters)
            engine.commands.dispatch(FireScenario(
                scenario=name, parameters=parameters, actor="ui",
            ))
            return outcome.to_dict()

        @self.route("POST", "/api/target")
        def target(request: Request) -> Any:
            # Dispatched, not decided here. The guard refuses an unconfirmed
            # live flip at the write side, where an API client cannot skip it.
            engine.commands.dispatch(ChangeTarget(
                environment=engine.manifest.environment if engine.manifest else "",
                target=str(request.body.get("target", "simulated")),
                confirmed=bool(request.body.get("confirmed", False)),
                reason=str(request.body.get("reason", "")),
                actor="ui",
            ))
            return {"target": request.body.get("target"), "changed": True}

        @self.route("POST", "/api/snapshot")
        def snapshot(request: Request) -> Any:
            label = str(request.body.get("label", ""))
            sealed = engine.seal(label=label)
            engine.commands.dispatch(SealSnapshot(label=label, actor="ui"))
            return sealed.to_dict()

        @self.route("POST", "/api/scan")
        def scan(_request: Request) -> Any:
            return engine.scan()
