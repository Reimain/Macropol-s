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
from dataclasses import dataclass, field
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
    """One parsed HTTP request.

    `headers`, `principal` and `context` all default to empty, so every existing
    construction of this class keeps working unchanged. They exist for the
    gateway: it reads `headers` to identify the caller, and writes `principal`
    and `context` back so the route it admits does not have to authenticate a
    second time.
    """

    method: str
    path: str
    query: Mapping[str, str]
    body: Mapping[str, Any]
    # `default_factory`, not a shared `MappingProxyType({})`: a dataclass field
    # default must be hashable, and a mapping proxy is not.
    headers: Mapping[str, str] = field(default_factory=dict)
    principal: Any = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def param(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)

    def header(self, name: str, default: str = "") -> str:
        """Case-insensitively, because HTTP header names are."""
        return self.headers.get(name.lower(), default)

    def integer(self, name: str, default: int = 0) -> int:
        try:
            return int(self.query.get(name, default))
        except (TypeError, ValueError):
            return default


@dataclass(frozen=True, slots=True)
class Response:
    """One JSON response, with a status the server maps onto HTTP.

    `headers` carries the answer's *position in time* rather than its content.
    A client that redraws on every event receives responses out of order — the
    refetch triggered by event N can land after the one triggered by event N+1 —
    and without a version it has no way to tell which is newer, so it paints the
    older answer and the screen goes backwards.

    Headers rather than a body envelope, deliberately: no existing route's JSON
    shape changes, the service worker already reads `x-slpie-stale`, and a
    generated client can read them uniformly across every route without knowing
    which ones happen to be backed by a `QueryResult`.
    """

    body: Any
    status: int = 200
    headers: tuple[tuple[str, str], ...] = ()

    def encode(self) -> bytes:
        return json.dumps(self.body, default=str).encode("utf-8")

    def with_headers(self, *added: tuple[str, str]) -> "Response":
        """A copy carrying `added` as well, without overwriting what is set."""
        if not added:
            return self
        present = {name.lower() for name, _ in self.headers}
        extra = tuple(item for item in added if item[0].lower() not in present)
        if not extra:
            return self
        return Response(self.body, status=self.status, headers=self.headers + extra)


def _no_plane() -> "Response":
    """No control plane attached — a state to report, not to fake.

    The workspace plane is optional: a single-tenant install has no tenancy to
    administer and attaches none. Answering with an empty tenant list would be
    indistinguishable from a plane with no tenants in it, and the platform's
    whole argument is that "nothing is there" and "I could not look" are
    different answers.
    """
    return Response({
        "error": (
            "no control plane is attached to this engine, so there is no "
            "tenancy to administer"
        ),
        "type": "NoControlPlane",
    }, status=409)


_UNSET = object()


def answered(result: Any, body: Any = _UNSET) -> Response:
    """A `QueryResult` as a response that keeps its version.

    Every route below used to call `.value` and throw the rest away, so the one
    piece of information the read model exists to provide — how current this
    answer is — never left the process. `body` is for the routes that wrap the
    value in an envelope of their own; leaving it out sends the value as-is.
    """
    return Response(result.value if body is _UNSET else body, headers=(
        ("X-Slpie-Version", str(result.version)),
        ("X-Slpie-Projection", result.projection or ""),
        ("X-Slpie-Stale", "1" if result.stale else "0"),
    ))


class Api:
    """Routes HTTP onto the buses. Holds no state of its own."""

    def __init__(self, engine: Any, *, gateway: Any = None) -> None:
        self.engine = engine
        #: The API manager, or nothing. `None` is the default and reproduces the
        #: behaviour this class had before it existed — which is what makes the
        #: hook safe to land before anybody has written a policy, and is proven
        #: by the whole pre-existing suite passing untouched.
        self.gateway = gateway
        self._routes: dict[tuple[str, str], Handler] = {}
        #: Filled on first use by `cacheable()` — the route table does not
        #: change after registration, so asking the contract once is enough.
        self._cacheable: frozenset[str] | None = None
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
        """Admit, then dispatch. One hook, in one place.

        Enforcement goes here rather than inside handlers for the same reason
        the module docstring gives about the live-target gate: a cross-cutting
        rule applied in each handler is a rule applied in most handlers, and the
        one it is missing from is the one somebody finds.
        """
        admitted = self._admit(request)
        if admitted is not None:
            return admitted
        answered = self._dispatch(request).with_headers(*self._position())
        # Whether a client may keep this. Stamped only on answers that worked:
        # a refusal or a fault is never something to hold on a device, and a
        # 409 "no environment open" cached past the moment one is opened is a
        # console insisting the platform is empty.
        if answered.status == 200 and self.cacheable(request.method, request.path):
            answered = answered.with_headers(("X-Slpie-Cacheable", "1"))
        return answered

    def _admit(self, request: Request) -> Response | None:
        """The gateway's refusal, or nothing. Absent gateway, always nothing."""
        if self.gateway is None:
            return None
        try:
            decision = self.gateway.admit(request)
        except Exception as error:  # noqa: BLE001 - a broken gateway is a fault
            # Failing closed. A gateway that cannot decide must not be read as
            # deciding "allow" — that is the one direction where a bug here
            # becomes a security hole rather than an outage.
            return Response({
                "error": f"the API manager could not decide: {error}",
                "type": "GatewayFault",
            }, status=503)

        if decision.allowed:
            return None
        return Response(
            decision.body(), status=decision.status, headers=decision.headers,
        )

    def _position(self) -> tuple[tuple[str, str], ...]:
        """Where the world is, stamped on every response including failures.

        A client holding several cells needs to know it is behind without asking
        a second question per cell, and a failure is exactly when knowing the
        world moved on matters most.
        """
        try:
            return (("X-Slpie-Ledger-Version", str(self.engine.ledger.version)),)
        except Exception:  # noqa: BLE001 - no engine, or a ledger not yet open
            return ()

    def cacheable(self, method: str, path: str) -> bool:
        """Whether an answer to this route may be held on a client's device.

        Read from the contract rather than decided here. `openapi()` already
        marks each operation `x-slpie-cacheable`, the service worker already
        uses that idea, and §26 is explicit that a fourth policy vocabulary must
        not be invented — so the browser's device tier reads the same flag.

        A read that needs an environment is cacheable; a mutation never is, and
        neither is the stream. Computed once and held, because it is a fact
        about the route table rather than about a request.
        """
        if self._cacheable is None:
            from .contract import cacheable_routes

            self._cacheable = cacheable_routes(verbs=self.verbs, routes=self.routes)
        return f"{method} {path}" in self._cacheable

    def _dispatch(self, request: Request) -> Response:
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
        except AttributeError as error:
            # The interface runs without an environment on purpose, so roughly
            # half these routes have nothing to read. Saying so is a different
            # answer from failing: 409 means "ask again once an environment is
            # open", and the client renders it as an empty state rather than as
            # a fault. Without this the same case reached the 500 below and told
            # an operator to go and read server logs about `NoneType`.
            if self.engine is None:
                return Response({
                    "error": (
                        "no environment is open, so this route has nothing to "
                        "read; start the interface from a directory holding "
                        "slpie.environment.yaml, or pass --root to point at one"
                    ),
                    "type": "NoEnvironment",
                }, status=409)
            return Response({"error": str(error), "type": "AttributeError"}, status=500)
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

        @self.route("GET", "/api/stream")
        def stream(_request: Request) -> Any:
            """Declared here so the live feed is discoverable; served elsewhere.

            `server.py` intercepts this path before the route table, because an
            open `text/event-stream` cannot be produced by a function that
            returns one JSON body. But leaving it out of the table entirely is
            what made the feed invisible: it appeared in none of the routes, in
            no OpenAPI document, and therefore in no generated client — the one
            capability of the platform that no contract described.

            Registering it as metadata means `route_set()` covers it and a
            client can find it. Reaching it through `Api.handle` means somebody
            called it with the wrong transport, and saying so beats a 404 that
            implies the feed does not exist.
            """
            return Response({
                "error": (
                    "/api/stream is a text/event-stream; open it with "
                    "EventSource rather than fetching it"
                ),
                "type": "WrongTransport",
                "transport": "sse",
            }, status=400)

        @self.route("GET", "/api/admin/workspaces")
        def admin_workspaces(_request: Request) -> Any:
            """`ControlPlane.status()`, which its own docstring calls what an
            administrator's console renders. Rendered, at last."""
            plane = getattr(engine, "plane", None)
            if plane is None:
                return _no_plane()
            return plane.status()

        @self.route("GET", "/api/admin/quota")
        def admin_quota(request: Request) -> Any:
            """Headroom per tenant, or for one named tenant."""
            plane = getattr(engine, "plane", None)
            if plane is None:
                return _no_plane()
            tenant = request.param("tenant")
            if not tenant:
                return {"tenants": plane.status().get("tenants", [])}
            quota = plane.quota_of(tenant)
            usage = plane.usage_of(tenant)
            return {
                "tenant": tenant,
                "quota": quota.to_dict(),
                "usage": usage.to_dict(),
                "headroom": quota.headroom(usage),
            }

        @self.route("GET", "/api/admin/datasets")
        def admin_datasets(request: Request) -> Any:
            """What the catalogue lists, scoped to one tenant and realm.

            The grants are not filtered by principal here — the gateway has not
            landed, so there is no principal to filter by, and inventing one
            would be a second authorisation model. When it lands this delegates
            to `ControlPlane.datasets_for`, which already asks the RBAC engine.
            """
            plane = getattr(engine, "plane", None)
            if plane is None:
                return _no_plane()
            tenant = request.param("tenant")
            realm = request.param("realm")
            grants = [
                grant.to_dict() for grant in getattr(plane, "grants", ())
                if (not tenant or grant.dataset.scope.tenant == tenant)
                and (not realm or grant.dataset.scope.realm == realm)
            ]
            return {"tenant": tenant, "realm": realm, "datasets": grants}

        @self.route("GET", "/api/apim/apis")
        def apim_apis(_request: Request) -> Any:
            """The catalogue. Derived from the registry, so it cannot drift.

            Answered whether or not a gateway is configured: the catalogue is a
            *description* of what this build serves, and a build with no gateway
            still serves it. Only enforcement needs the gateway.
            """
            from ..apim import ApiCatalog

            catalog = (
                self.gateway.catalog if self.gateway is not None
                and getattr(self.gateway, "catalog", None) is not None
                else ApiCatalog.from_registry(verbs=self.verbs, routes=self.routes)
            )
            return catalog.to_dict()

        @self.route("GET", "/api/apim/gateway")
        def apim_gateway(_request: Request) -> Any:
            """The chain, its hit counts and its refusals — `iptables -L -v`.

            With no gateway attached this says so rather than showing an empty
            chain, which would read as "nothing is being enforced because no
            rule matched" instead of "nothing is being enforced".
            """
            if self.gateway is None:
                return {
                    "attached": False,
                    "detail": (
                        "no API manager is attached, so every route is served "
                        "without lifecycle, subscription, throttling or "
                        "mediation checks"
                    ),
                }
            return {"attached": True, **self.gateway.status()}

        @self.route("GET", "/api/apim/throttles")
        def apim_throttles(_request: Request) -> Any:
            from ..apim.throttle import TIERS

            if self.gateway is not None:
                return self.gateway.throttler.status()
            return {"tiers": [tier.to_dict() for tier in TIERS.values()],
                    "tracked": 0, "calls": 0}

        @self.route("GET", "/api/apim/subscriptions")
        def apim_subscriptions(request: Request) -> Any:
            if self.gateway is None or self.gateway.subscriptions is None:
                return {"subscriptions": [], "attached": False}
            ledger = self.gateway.subscriptions
            application = request.param("application")
            held = ledger.of(application) if application else tuple(ledger)
            return {
                "attached": True,
                "subscriptions": [item.to_dict() for item in held],
                **ledger.status(),
            }

        @self.route("GET", "/api/apim/analytics")
        def apim_analytics(_request: Request) -> Any:
            if self.gateway is None:
                return {"attached": False, "calls": 0}
            return {"attached": True, **self.gateway.analytics.summary()}

        @self.route("GET", "/api/screens")
        def screens(_request: Request) -> Any:
            """The screen manifest, so the shell does not restate the routing.

            Generated from the same registry the routes are, which is what makes
            "every capability has a screen" a test rather than a hope.
            """
            from .contract import screens as manifest

            return {"screens": [
                screen.to_dict()
                for screen in manifest(verbs=self.verbs, routes=self.routes)
            ]}

        @self.route("GET", "/api/lexicon")
        def lexicon(request: Request) -> Any:
            """The words this caller's context uses for the platform's nouns.

            `request.context` is written by the gateway before any route runs,
            so this reads an identity that has already been established rather
            than establishing a second one — the same reason the live guard is
            not reimplemented behind FastAPI.

            A profile naming a term the platform does not define, or trying to
            rename a control, is an authored mistake and is reported as one. The
            console still renders: it falls back to the platform's own words,
            because a reader seeing `finding` where they expected `risk` has a
            cosmetic problem and a reader seeing a blank screen has an outage.
            """
            from ..context.lexicon import LexiconError, default
            from ..context.profile import resolve

            asked = request.param("profile")
            context = dict(request.context or {})
            if asked:
                context["profile"] = asked

            root = getattr(engine, "root", None) or "."
            try:
                words = resolve(context, root=root)
                error = ""
            except LexiconError as failure:
                words = default()
                error = str(failure)

            body = words.to_dict()
            body["requested"] = str(context.get("profile") or "")
            if error:
                body["error"] = error
            return body

        @self.route("GET", "/api/stream/status")
        def stream_status(_request: Request) -> Any:
            """How far behind the feed is, and how far back it can replay."""
            live = getattr(engine, "stream", None)
            if live is None:
                return {"clients": 0, "delivered": 0, "buffered": 0,
                        "dropped": 0, "oldest": 0, "attached": False}
            return {**live.status(), "attached": True}

        @self.route("GET", "/api/manifest")
        def manifest(_request: Request) -> Any:
            return engine.manifest.to_dict() if engine.manifest else {}

        @self.route("GET", "/api/station")
        def station(request: Request) -> Any:
            if element := request.param("element"):
                return answered(engine.queries.ask(StationStatus(element=element)))
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
            wanted = request.param("id")
            if not wanted:
                return Response({
                    "error": "/api/node needs an ?id=; ask /api/search for one",
                    "type": "MissingParameter",
                }, status=400)
            found = engine.graph.node(wanted)
            if found is None:
                # 404, not a 200 carrying an error string. The detail views for
                # the catalogue and for findings both hang off this route, and a
                # client cannot tell "absent" from "present but empty" when both
                # arrive as 200 — so a retired node rendered as a blank panel
                # rather than as the supersession it is.
                return Response({
                    "error": f"no node {wanted!r} in this graph",
                    "type": "NoSuchNode",
                }, status=404)
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
            answer = engine.queries.ask(OpenFindings(severity=request.param("severity")))
            return answered(answer, {"findings": answer.value})

        @self.route("GET", "/api/history")
        def history(request: Request) -> Any:
            answer = engine.queries.ask(History(
                subject=request.param("subject"),
                since=request.integer("since"),
                limit=request.integer("limit", 100),
            ))
            return answered(answer, {"records": answer.value})

        @self.route("GET", "/api/causation")
        def causation(request: Request) -> Any:
            answer = engine.queries.ask(Causation(event_id=request.param("event")))
            return answered(answer, {"chain": answer.value})

        @self.route("GET", "/api/integrity")
        def integrity(_request: Request) -> Any:
            return answered(engine.queries.ask(LedgerIntegrity()))

        @self.route("GET", "/api/projections")
        def projections(_request: Request) -> Any:
            return answered(engine.queries.ask(ProjectionStatus()))

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
        from ..compose.parse import ParseError
        from ..compose.pipeline import CompositionError

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
            # One session per request, swept when the response body is built.
            # Concurrent requests therefore never share blocks, and a request
            # that fails mid-scan does not leave its spill behind — which under
            # load is the difference between a bounded disk and a full one.
            with context(request.body) as active:
                result = composition.run(active)
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

            # Serialised inside the session, for the same reason the whole-
            # composition route is: `to_dict()` reads the flow, and a spilled
            # flow whose blocks were already swept would serialise as empty.
            with context({"confirmed": request.body.get("confirmed", False),
                          "root": request.body.get("root", ".")}) as active:
                if verb.mutates and not active.confirmed:
                    return Response({
                        "error": (
                            f"{verb.name} changes the environment and was not "
                            f"confirmed; the same guard refuses it here as at "
                            f"the CLI"
                        ),
                        "refused": True,
                    }, status=403)

                return verb.run(flow, verb.bind(body), active).to_dict()

        run.__name__ = f"verb_{verb.name.replace('-', '_')}"
        return run
