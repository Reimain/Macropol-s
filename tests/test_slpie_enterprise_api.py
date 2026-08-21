"""Phase 16 — the transport, the runner, and the routes read out of source.

The claim under test throughout is that ring 1 adds no API, no execution model
and no second vocabulary. It adds a *transport* for the route table ring 0
already has, an implementation of a protocol ring 0 already declares, and a
second source of evidence for endpoints the manifest already names.

Skipped loudly where the extras are absent: the kernel suite must pass with zero
third-party packages, and that is invariant 4 rather than a preference.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    "fastapi", reason="the api adapter needs `pip install -e '.[enterprise]'`",
)
pytest.importorskip("httpx", reason="the FastAPI test client needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from slpie.ui.api import Api, Request  # noqa: E402
from slpie_enterprise.api import create_app, route_set  # noqa: E402


@pytest.fixture
def api():
    return Api(engine=None)


@pytest.fixture
def client(api):
    return TestClient(create_app(api=api), raise_server_exceptions=False)


# --- one route table ----------------------------------------------------------


def test_the_two_servers_expose_one_route_set(api):
    """§24's fifth acceptance, between the stdlib server and FastAPI.

    Compared as a set of `"METHOD /path"` strings rather than by count: two
    servers with 93 routes each and one of them different is exactly the drift
    a count would miss.
    """
    app = create_app(api=api)

    # `/docs`, `/redoc` and `/openapi.json` are FastAPI documenting the API
    # rather than part of it, and they are excluded by name rather than by a
    # prefix rule so a fourth one appearing has to be looked at.
    documentation = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    served = {
        f"{method} {route.path}"
        for route in app.routes
        if getattr(route, "methods", None) and route.path not in documentation
        for method in route.methods
        if method != "HEAD"
    }
    declared = route_set(api)

    assert served == declared, (
        f"only FastAPI: {sorted(served - declared)}\n"
        f"only ring 0:  {sorted(declared - served)}"
    )
    assert len(declared) > 80, "the route table shrank — this proves little at 3"


def test_the_documented_contract_is_ring_zero_s_own(api):
    """`/openapi.json` must not be FastAPI's guess.

    Every route is bound to one generic handler, so introspection emits 93
    operations with no parameters and no schemas — a document that looks
    authoritative, generates a broken client, and sits at the URL everybody
    trusts. Ring 0 emits the real one; this asserts they are the same bytes.
    """
    from slpie.ui.contract import openapi

    app = create_app(api=api)
    served = app.openapi()
    expected = openapi(verbs=api.verbs, routes=api.routes)

    assert served == expected
    # And it is the real one rather than a stub that happens to match.
    operations = sum(len(item) for item in served["paths"].values())
    assert operations > 80
    assert any(
        "parameters" in operation or "requestBody" in operation
        for item in served["paths"].values() for operation in item.values()
    ), "the contract describes no parameters — that is FastAPI's guess, not ours"


def test_the_adapter_declares_no_route_of_its_own(api):
    """A route added here is a capability the CLI, the manual and the console
    cannot reach — §24's drift, arriving through the transport."""
    app = create_app(api=api)
    extra = {
        route.path for route in app.routes
        if getattr(route, "methods", None)
        and route.path.startswith("/api")
        and not any(route.path == path for _method, path in api.routes)
    }
    assert not extra, f"the adapter invented routes: {sorted(extra)}"


def test_every_route_dispatches_to_its_own_path(client, api):
    """The late-binding bug, which produces a server that starts perfectly and
    answers everything as whichever route was registered last."""
    answers = {}
    for method, path in sorted(api.routes):
        if method != "GET" or ":" in path or path == "/api/stream":
            continue
        answers[path] = client.get(path)

    assert len(answers) > 15, "too few routes exercised to catch a mis-binding"

    # `/api/routes` and `/api/verbs` are open discovery routes with distinct
    # bodies. If every endpoint were bound to the same closure they would match.
    routes = client.get("/api/routes")
    verbs = client.get("/api/verbs")
    assert routes.status_code == 200 and verbs.status_code == 200
    assert routes.json() != verbs.json(), "two routes returned one body"


# --- one answer ---------------------------------------------------------------


def test_both_servers_give_the_same_answer(client, api):
    """Different surfaces must not be different answers.

    The adapter calls `Api.handle` and so does the stdlib server, so this is
    close to a tautology by construction — which is the point. It is asserted
    anyway because "by construction" stops being true the first time somebody
    adds a shortcut to the transport.
    """
    for path in ("/api/routes", "/api/verbs", "/api/screens", "/api/shells"):
        over_http = client.get(path)
        direct = api.handle(Request(method="GET", path=path, query={}, body={}))
        assert over_http.status_code == direct.status
        assert over_http.json() == direct.body, path


def test_every_header_survives_the_transport(client, api):
    """A client cannot order its cells without them, and a transport that drops
    a header produces a console that silently paints stale answers.

    Compared against what ring 0 actually returns rather than against a fixed
    list. With no environment open there is no ledger to version, so
    `X-Slpie-Ledger-Version` is legitimately absent — asserting it
    unconditionally would have been the test demanding something the platform
    correctly declines to invent.
    """
    for path in ("/api/routes", "/api/verbs"):
        direct = api.handle(Request(method="GET", path=path, query={}, body={}))
        over_http = client.get(path)
        carried = {key.lower(): value for key, value in over_http.headers.items()}
        for name, value in direct.headers:
            assert carried.get(name.lower()) == value, f"{path} dropped {name}"

    # Guard the guard: a transport that dropped *everything* would pass a loop
    # over an empty list.
    assert api.handle(
        Request(method="GET", path="/api/routes", query={}, body={}),
    ).headers, "ring 0 returned no headers, so this proves nothing"


def test_the_version_header_appears_once_an_environment_is_open(client, api):
    """The half that needs an engine, so the absence above is not mistaken for
    the adapter losing it."""
    from slpie.engine import Engine

    engine = Engine.from_text(
        "apiVersion: slpie/v1\nenvironment: header-check\ntarget: simulated\n",
    )
    engine.declare()

    served = Api(engine=engine)
    with TestClient(create_app(api=served)) as opened:
        answer = opened.get("/api/routes")
    assert "x-slpie-ledger-version" in {key.lower() for key in answer.headers}


def test_a_query_string_reaches_the_handler(client):
    answer = client.get("/api/verbs", params={"group": "environment"})
    assert answer.status_code == 200


def test_a_malformed_body_is_not_a_transport_error(client):
    """The verbs refuse what they cannot use, naming the parameter. A
    transport-level schema error would be a second refusal vocabulary the CLI
    does not share."""
    answer = client.post(
        "/api/v/status", content=b"{not json", headers={"content-type": "application/json"},
    )
    assert answer.status_code != 422, (
        "FastAPI validated the body — that is a second validator, and it "
        "refuses things the CLI accepts"
    )


def test_the_stream_route_declines_rather_than_pretending(client):
    """It is an open connection, not an answer. Both servers say so."""
    answer = client.get("/api/stream")
    assert answer.status_code == 400
    assert "EventSource" in answer.json()["error"]


def test_the_adapter_does_not_reimplement_the_gateway():
    """The live gate must have exactly one implementation.

    A second one is a second place to forget the confirmation, and the
    forgetting is invisible until somebody points it at production.
    """
    import re
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent / "slpie_enterprise" / "api"
    for path in here.glob("*.py"):
        body = re.sub(r'"""(?:.|\n)*?"""', "", path.read_text(encoding="utf-8"))
        body = re.sub(r"^\s*#.*$", "", body, flags=re.MULTILINE)
        for forbidden in ("AccessEngine", "check(", "admit(", "confirmed"):
            assert forbidden not in body, (
                f"{path.name} contains {forbidden!r} — enforcement belongs in "
                f"`Api.handle`, where both servers already share it"
            )
