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
            outcome = handler(request)
            # A handler that already chose its status says so by returning a
            # Response. Re-wrapping it produced a Response whose body was another
            # Response, which serialised as a string and lost the status the
            # handler had deliberately set.
            return outcome if isinstance(outcome, Response) else Response(outcome)
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

        # --- the composition surface, generated from the verb registry ------
        #
        # Every verb gets a route automatically. That is what makes the registry
        # *authoritative* rather than merely adjacent: there is no file to edit and
        # therefore nowhere to forget to wire a capability. A verb added once
        # appears in the CLI, this API, the manual, the planner and every client.
        self._register_composition()

    def _register_composition(self) -> None:
        from ..compose import Composition, Context, VerbError, registry as verb_registry
        from ..compose.pipeline import CompositionError
        from ..compose.parse import ParseError

        verbs = verb_registry()
        self.verbs = verbs

        def context(body: Mapping[str, Any]) -> Any:
            return Context(
                engine=self.engine, actor="ui",
                confirmed=bool(body.get("confirmed", False)),
                root=str(body.get("root", ".")),
            )

        @self.route("GET", "/api/verbs")
        def api_verbs(_request: Request) -> Any:
            return verbs.to_dict()

        @self.route("GET", "/api/manual")
        def api_manual(_request: Request) -> Any:
            from ..manual import as_dict

            return as_dict(verbs=verbs)

        @self.route("GET", "/api/contract")
        def api_contract(_request: Request) -> Any:
            from .contract import openapi

            return openapi(verbs=verbs, routes=self.routes)

        @self.route("POST", "/api/compose/validate")
        def api_validate(request: Request) -> Any:
            """Check a composition without running any of it."""
            try:
                composition = Composition.read(
                    str(request.body.get("pipeline", "")), verbs=verbs,
                )
            except ParseError as error:
                return {"ok": False, "explanation": str(error)}
            body = composition.validate().to_dict()
            body["explain"] = composition.explain()
            return body

        @self.route("POST", "/api/run")
        def api_run(request: Request) -> Any:
            """Run a whole composition. The primary entry point for a client."""
            text = str(request.body.get("pipeline", ""))
            composition = Composition.read(text, verbs=verbs)
            result = composition.run(context(request.body))
            body = result.to_dict()
            if not result.ok:
                # A stage failing is a 400 with the partial flow attached, not a
                # 500: the caller's composition did not work out, and they need
                # the partial answer and the reason rather than a server error.
                return Response(body, status=400)
            return body

        @self.route("POST", "/api/plan")
        def api_plan(request: Request) -> Any:
            from ..planner import plan_for

            return plan_for(
                str(request.body.get("question", "")), verbs=verbs,
            ).to_dict()

        # One route per verb, generated. `_verb_route` is a closure factory so
        # each route captures its own verb rather than the loop variable.
        for verb in verbs:
            self.route("POST", f"/api/v/{verb.name}")(self._verb_route(verb, context))

    def _verb_route(self, verb: Any, context: Any) -> Handler:
        from ..compose import Flow
        from ..compose.wire import decode

        def run(request: Request) -> Any:
            body = dict(request.body)
            upstream = body.pop("upstream", None)
            body.pop("confirmed", None)
            body.pop("root", None)

            flow = Flow.start()
            if isinstance(upstream, Mapping) and upstream:
                flow = decode(json.dumps(dict(upstream)))

            if not verb.accepts(flow.kind):
                return Response({
                    "error": (
                        f"{verb.name} consumes {verb.consumes.label}, but it was "
                        f"given {flow.kind.label}"
                    ),
                    "type": "TypeMismatch",
                }, status=400)

            active = context({"confirmed": request.body.get("confirmed", False),
                              "root": request.body.get("root", ".")})
            if verb.mutates and not active.confirmed:
                return Response({
                    "error": (
                        f"{verb.name} changes the environment and was not "
                        f"confirmed; the same guard refuses it here as at the CLI"
                    ),
                    "refused": True,
                }, status=403)

            return verb.run(flow, verb.bind(body), active).to_dict()

        run.__name__ = f"verb_{verb.name.replace('-', '_')}"
        return run
