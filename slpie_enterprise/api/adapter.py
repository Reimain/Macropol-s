"""ASGI in, `Request` out; `Response` in, ASGI out.

**The adapter is generic on purpose.** One handler serves all 93 routes rather
than 93 decorated functions, because a per-route function is a per-route place
to diverge — a header stamped here and not there, a body parsed one way and then
another. The route table is data (§30), so the transport can be too.

What this deliberately does **not** do:

* authenticate — the gateway does, inside `Api.handle`;
* check the live-target guard — the write side does, where it already is;
* validate a body against a schema — the verbs do, and a second validator would
  refuse things the CLI accepts, which is exactly the drift §24 exists to stop;
* declare a single route of its own. `test_the_two_servers_expose_one_route_set`
  compares the sets field by field, and a route added here would fail it.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from slpie.ui.api import Api, Request, Response

#: Paths the stdlib server intercepts before its route table. `/api/stream` is
#: registered as metadata so a generated client can discover it, and cannot be
#: *handled* by `Api.handle` — it is an open connection, not an answer.
STREAMED = frozenset({"/api/stream"})


def route_set(api: Api) -> set[str]:
    """`{"GET /api/findings", …}` — the comparison both servers are held to."""
    return {f"{method} {path}" for method, path in api.routes}


def create_app(engine: Any = None, *, gateway: Any = None, api: Api | None = None) -> Any:
    """A FastAPI application serving exactly ring 0's route table.

    `api` is injectable so a test can hand in the same instance the stdlib
    server holds. Two `Api` objects over one engine would answer identically
    today and are still two places for state to appear later.
    """
    from fastapi import FastAPI, Request as HttpRequest
    from fastapi.responses import JSONResponse

    served = api if api is not None else Api(engine=engine, gateway=gateway)

    app = FastAPI(
        title="SLPIE",
        description=(
            "The same route table the stdlib server exposes. This is a "
            "transport, not a second API: every route delegates to "
            "`slpie.ui.api.Api.handle`."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.api = served

    # **The real contract, not FastAPI's guess.**
    #
    # Every route here is bound to one generic handler, so FastAPI's
    # introspection would emit 93 operations with no parameters and no schemas
    # — a document that looks authoritative, generates a broken client, and
    # sits at the URL everybody trusts. Ring 0 already emits the true contract
    # from the verb registry, and `slpie contract --openapi` and this route are
    # now the same bytes.
    def contract() -> dict[str, Any]:
        from slpie.ui.contract import openapi

        return openapi(verbs=served.verbs, routes=served.routes)

    app.openapi = contract      # type: ignore[method-assign]

    async def dispatch(http: HttpRequest, method: str, path: str) -> JSONResponse:
        body = await _body(http)
        answer = served.handle(Request(
            method=method,
            path=path,
            query=dict(http.query_params),
            body=body,
            # Lower-cased, because `Request.header` looks up case-insensitively
            # by lower-casing the *name* and trusting the mapping's keys — a
            # contract the stdlib server meets and an adapter has to meet too.
            headers={key.lower(): value for key, value in http.headers.items()},
        ))
        return JSONResponse(
            content=answer.body,
            status_code=answer.status,
            headers={name: value for name, value in answer.headers},
        )

    for method, path in sorted(served.routes):
        if path in STREAMED:
            _register_stream(app, method, path)
            continue
        _register(app, dispatch, method, path)

    return app


def _register(app: Any, dispatch: Any, method: str, path: str) -> None:
    """One route, bound to the generic handler.

    The default arguments are not a style choice: without them every closure
    would capture the loop variables and all 93 routes would dispatch as
    whichever one was registered last — the classic late-binding bug, and one
    that produces a server which starts perfectly and answers everything wrong.
    """

    from fastapi import Request as HttpRequest

    async def endpoint(http: HttpRequest, _method: str = method, _path: str = path) -> Any:
        return await dispatch(http, _method, _path)

    # The annotation is load-bearing, not decoration. FastAPI decides what to
    # inject from the *type*, and an `Any` parameter is read as something to
    # parse out of the query or the body — which answered 422 for every route
    # that took one. Annotated as `Request`, it is handed the request.
    endpoint.__annotations__["http"] = HttpRequest

    app.add_api_route(
        path, endpoint, methods=[method],
        name=f"{method.lower()}_{path.strip('/').replace('/', '_')}",
    )


def _register_stream(app: Any, method: str, path: str) -> None:
    """The live feed, refused with an explanation rather than mishandled.

    `Api.handle` cannot serve it — it is an open connection rather than an
    answer — and the stdlib server intercepts it before the route table. The
    honest ring-1 behaviour is the same refusal the stdlib route gives, so a
    client discovering the feed from the contract gets one message from both
    servers. Serving it properly is phase 16's SSE work over `notify.py`, and
    pretending here would be worse than declining.
    """
    from fastapi.responses import JSONResponse

    async def endpoint() -> Any:
        return JSONResponse(
            {
                "error": "open this with EventSource; it is a stream, not a request",
                "type": "WrongTransport",
            },
            status_code=400,
        )

    app.add_api_route(path, endpoint, methods=[method], name="stream")


async def _body(http: Any) -> dict[str, Any]:
    """The JSON body, or an empty mapping.

    A malformed body is **not** a 400 here. The verbs already refuse what they
    cannot use, with a message naming the parameter, and a transport-level
    schema error would be a second refusal vocabulary the CLI does not share.
    """
    raw = await http.body()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {"value": decoded}
