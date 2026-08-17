"""The interface: API contract, SSE delivery, and offline self-containment.

The server is exercised over real HTTP against a real socket. A test that called
the handlers directly would prove the routing table works and nothing about
whether the thing actually serves.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import pytest

from slpie.engine import Engine
from slpie.ui import APP_ROOT, UiServer
from slpie.ui.api import Api, Request
from slpie.ui.stream import CLIENT_BUFFER, EventStream

MANIFEST = """apiVersion: slpie/v1
environment: acme
target: simulated
security:
  boundaries:
    - name: cardholder-data
      contains: [payments]
codebase:
  - root: ./services/payments
  - root: ./services/orders
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
"""


@pytest.fixture
def engine(tmp_path):
    built = Engine.from_text(MANIFEST)
    built.declare()
    built.simulate(root=tmp_path / "world")
    built.attach(wanted=("file-read", "lockfile-read", "static-analysis", "scm-history"))
    yield built
    built.close()


@pytest.fixture
def server(engine):
    running = UiServer(engine, port=0).start()
    yield running
    running.stop()


def call(server, path, body=None):
    url = server.url.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


# --- assets --------------------------------------------------------------


def test_the_page_is_served_and_reaches_nothing_outside_itself(server):
    with urllib.request.urlopen(server.url, timeout=10) as response:
        html = response.read().decode("utf-8")

    assert response.status == 200
    assert "<title>SLPIE</title>" in html
    # No CDN, no external font, no remote script. The page works with the
    # network unplugged, which matters when it is deployed inside private
    # infrastructure.
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == []


def test_every_referenced_asset_is_present_locally(server):
    with urllib.request.urlopen(server.url, timeout=10) as response:
        html = response.read().decode("utf-8")

    for reference in re.findall(r'(?:src|href)="(/[^"]+)"', html):
        with urllib.request.urlopen(server.url.rstrip("/") + reference, timeout=10) as asset:
            assert asset.status == 200
            assert len(asset.read()) > 0


def test_the_javascript_and_stylesheet_ship_with_the_package():
    assert (APP_ROOT / "index.html").is_file()
    assert (APP_ROOT / "app.js").is_file()
    assert (APP_ROOT / "styles.css").is_file()


def test_an_asset_path_cannot_escape_the_app_directory(server):
    status, _ = call(server, "/../../../etc/passwd")
    assert status == 404

    status, _ = call(server, "/nothing-here.js")
    assert status == 404


def test_a_response_declares_a_policy_that_forbids_external_origins(server):
    with urllib.request.urlopen(server.url, timeout=10) as response:
        policy = response.headers["Content-Security-Policy"]

    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy


# --- the read API --------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/status", "/api/manifest", "/api/station", "/api/graph", "/api/reconcile",
    "/api/findings", "/api/integrity", "/api/projections", "/api/scenarios",
    "/api/cycles", "/api/routes", "/api/history",
])
def test_every_read_route_answers(server, path):
    status, body = call(server, path)
    assert status == 200
    assert isinstance(body, dict)


def test_status_reports_the_environment_and_its_target(server):
    _, body = call(server, "/api/status")

    assert body["environment"] == "acme"
    assert body["target"] == "simulated"
    assert body["declarations"] == 3
    assert body["ledger_version"] > 0


def test_the_graph_route_carries_counts_nodes_and_edges(server):
    _, body = call(server, "/api/graph")

    assert body["counts"]["nodes"] > 0
    assert isinstance(body["nodes"], list)
    assert all("confidence" in node for node in body["nodes"])


def test_a_node_comes_back_with_its_evidence_and_both_edge_directions(server):
    _, graph = call(server, "/api/graph")
    node_id = graph["nodes"][0]["id"]

    status, body = call(server, f"/api/node?id={node_id}")
    assert status == 200
    assert body["node"]["evidence"]
    assert "out" in body and "in" in body


def test_search_finds_declared_elements(server):
    _, body = call(server, "/api/search?q=payments")
    assert {node["name"] for node in body["results"]} >= {"payments"}


def test_reconciliation_is_served_with_all_four_deltas(server):
    _, body = call(server, "/api/reconcile")

    for key in ("declared_not_found", "undeclared", "contradictions", "boundary_violations"):
        assert key in body
    assert "coverage" in body


def test_the_station_route_shows_refusals_alongside_grants(server):
    _, body = call(server, "/api/station")

    assert body["elements"]
    assert body["refusals"]
    assert body["gaps"]


def test_an_unknown_route_lists_the_ones_that_exist(server):
    status, body = call(server, "/api/nope")

    assert status == 404
    assert any(route.endswith("/api/status") for route in body["routes"])


# --- the write API -------------------------------------------------------


def test_asking_a_question_returns_guidance_shaped_output(server):
    status, body = call(server, "/api/ask", {"question": "what breaks if lodash 5 lands?"})

    assert status == 200
    # Never a bare value: an answer, its reasoning, its gaps, what to ask next.
    for key in ("answer", "reasoning", "gaps", "next_questions", "confidence"):
        assert key in body


def test_firing_a_scenario_changes_the_world_and_reports_what_to_expect(server, engine):
    status, body = call(server, "/api/scenario", {"scenario": "cve"})

    assert status == 200
    assert body["expect_findings"] == ["vulnerable_dependency"]
    lock = json.loads(engine.world.read("payments", "package-lock.json"))
    assert lock["packages"]["node_modules/lodash"]["version"] == "4.17.20"


def test_an_unknown_scenario_is_the_callers_mistake_not_a_server_fault(server):
    status, body = call(server, "/api/scenario", {"scenario": "does-not-exist"})

    assert status == 400
    assert "available" in body["error"]


def test_the_ui_cannot_bypass_the_live_target_gate(server):
    """The gate is enforced at the write side. A UI that enforced its own copy
    would be a second rule to keep in sync — and the one an API client skips."""
    status, body = call(server, "/api/target", {"target": "live", "confirmed": False})

    assert status == 403
    assert body["refused"]
    assert "confirmation" in body["error"]


def test_a_confirmed_target_change_is_accepted(server):
    status, body = call(
        server, "/api/target", {"target": "live", "confirmed": True, "reason": "cutover"},
    )
    assert status == 200
    assert body["changed"]


def test_sealing_a_snapshot_returns_its_content_addressed_id(server):
    status, body = call(server, "/api/snapshot", {"label": "baseline"})

    assert status == 200
    assert len(body["id"]) == 40
    assert body["node_count"] > 0


def test_a_body_that_is_not_json_is_refused_clearly(server):
    request = urllib.request.Request(
        server.url.rstrip("/") + "/api/ask", data=b"not json",
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=10)
    assert error.value.code == 400


def test_posting_to_a_non_api_path_is_a_404(server):
    status, _ = call(server, "/index.html", {"anything": True})
    assert status == 404


# --- the live stream -----------------------------------------------------


def test_events_reach_a_connected_client(server):
    """The architectural dividend: a live feed is a subscriber, not a poller."""
    frames: list[str] = []

    def read():
        try:
            with urllib.request.urlopen(
                server.url.rstrip("/") + "/api/stream", timeout=8
            ) as response:
                # Two lines per event now — `id:` then `data:` — so the count is
                # generous rather than exact. A fixed small budget would make
                # this test depend on how many frames precede the interesting
                # one, which is not what it is about.
                for _ in range(24):
                    line = response.readline()
                    if line.strip():
                        frames.append(line.decode("utf-8").strip())
        except Exception:  # pragma: no cover - the socket closing is normal
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(0.5)

    call(server, "/api/scenario", {"scenario": "shadow-dependency"})
    call(server, "/api/snapshot", {"label": "from-stream"})
    reader.join(timeout=6)

    assert any(frame.startswith("data:") for frame in frames)
    payloads = [json.loads(f[5:]) for f in frames if f.startswith("data:")]
    assert any(p["kind"] == "scenario_fired" for p in payloads)

    # The two fields that make a reconnect lossless. Without `id:` the browser
    # has nothing to put in `Last-Event-ID`, so every drop silently loses
    # whatever happened while the socket was down.
    assert any(frame.startswith("retry:") for frame in frames), (
        "the stream never told the client how long to wait before reconnecting"
    )
    identifiers = [int(f.split(":", 1)[1]) for f in frames if f.startswith("id:")]
    assert identifiers, "no data frame carried an id, so a resume point cannot exist"
    assert identifiers == sorted(identifiers), "ids went backwards"


def test_a_client_is_primed_with_recent_history_so_it_is_never_blank():
    from slpie.core.events import EventKind, emit

    stream = EventStream()
    for index in range(5):
        stream.handle(emit(EventKind.NODE_ASSERTED, f"n{index}").sequenced(index + 1))

    client = stream.connect("late-arrival", backlog=3)
    assert client.events.qsize() == 3


def test_a_client_that_falls_behind_is_told_rather_than_silently_diverging():
    from slpie.core.events import EventKind, emit

    stream = EventStream()
    client = stream.connect("slow")
    for index in range(CLIENT_BUFFER + 20):
        stream.handle(emit(EventKind.NODE_ASSERTED, f"n{index}").sequenced(index + 1))

    assert client.dropped > 0
    assert client.events.qsize() <= CLIENT_BUFFER


def test_one_slow_client_cannot_slow_the_platform_down():
    from slpie.core.events import EventKind, emit

    stream = EventStream()
    stream.connect("never-reads")

    started = time.monotonic()
    for index in range(2000):
        stream.handle(emit(EventKind.NODE_ASSERTED, f"n{index}").sequenced(index + 1))
    assert time.monotonic() - started < 2.0


def test_a_feed_line_carries_a_summary_not_a_whole_payload():
    from slpie.core.events import EventKind, emit

    stream = EventStream()
    client = stream.connect("reader")
    stream.handle(emit(
        EventKind.NODE_ASSERTED, "n1",
        {"kind": "package", "properties": {"x": "y" * 5000}, "title": "t"},
    ).sequenced(1))

    # `(stream sequence, payload)`. The sequence rides alongside the payload
    # rather than inside it, because it is the stream's own counter and not the
    # ledger's — operational events never reach the ledger and would all be 0.
    _sequence, raw = client.events.get_nowait()
    payload = json.loads(raw)
    assert payload["payload"]["kind"] == "package"
    # Sending every property bag to every browser would make the feed the most
    # expensive thing in the platform.
    assert "properties" not in payload["payload"]


def test_disconnecting_removes_the_client():
    stream = EventStream()
    stream.connect("a")
    assert stream.clients == 1

    stream.disconnect("a")
    assert stream.clients == 0
    assert stream.status()["clients"] == 0


# --- the api object directly --------------------------------------------


def test_the_route_table_is_inspectable(engine):
    api = Api(engine)
    routes = {f"{method} {path}" for method, path in api.routes}

    assert "GET /api/status" in routes
    assert "POST /api/target" in routes


def test_query_parameters_are_parsed_with_sane_fallbacks():
    request = Request(method="GET", path="/api/graph", query={"limit": "oops"}, body={})

    assert request.integer("limit", 200) == 200
    assert request.param("missing", "default") == "default"


# --- installable, responsive, and self-contained -------------------------
#
# The same stdlib server is the desktop window and the phone screen. That is only
# true if the shell installs, holds at a phone width, and references nothing
# outside itself — inside an air-gapped network this is the only client that runs.


def test_every_shipped_asset_is_served(server):
    """A page that looks fine and silently cannot be installed is the worst kind
    of missing, so each asset is fetched rather than assumed present."""
    for path, expected in (
        ("/", "text/html"),
        ("/app.js", "text/javascript"),
        ("/compose.js", "text/javascript"),
        ("/styles.css", "text/css"),
        ("/sw.js", "text/javascript"),
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/icon.svg", "image/svg+xml"),
    ):
        status, headers, body = _raw(server, path)
        assert status == 200, f"{path} is not served"
        assert expected in headers["Content-Type"], f"{path} has the wrong type"
        assert body, f"{path} is empty"


def test_the_webmanifest_has_what_a_browser_needs_to_install_it(server):
    import json as _json

    _status, _headers, body = _raw(server, "/manifest.webmanifest")
    manifest = _json.loads(body)

    assert manifest["name"] and manifest["short_name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["icons"], "with no icon it cannot be added to a home screen"
    assert manifest["icons"][0]["src"].startswith("/"), "a relative icon breaks in scope"


def test_every_asset_the_worker_precaches_actually_exists(server):
    """`addAll` semantics aside, a shell listing an asset that 404s is a shell
    that installs incompletely and fails offline in a way nobody sees online."""
    import re

    _status, _headers, body = _raw(server, "/sw.js")
    listed = re.findall(r'"(/[^"]*)"', body.decode("utf-8"))
    paths = {path for path in listed if "." in path or path == "/"}

    for path in sorted(paths):
        status, _headers, _body = _raw(server, path)
        assert status == 200, f"the worker precaches {path}, which is not served"


def test_the_worker_never_caches_the_event_stream(server):
    """A cached SSE response would replay history as though it were happening now.

    The path is read out of the client's own `EventSource(...)` call rather than
    written here. The previous version of this test looked for the literal
    `"/events"` in `sw.js` and passed for three phases while the worker excluded
    a path the server has never served and cached `/api/stream` — an open,
    unbounded response — on every connect.

    A test that names the value it is checking can only ever confirm that
    somebody typed it twice. Deriving it from the source of truth is what makes
    the assertion mean something.
    """
    _status, _headers, worker = _raw(server, "/sw.js")
    _status, _headers, client = _raw(server, "/app.js")

    opened = re.search(r'new EventSource\(\s*"([^"]+)"', client.decode("utf-8"))
    assert opened, "the app no longer opens an EventSource; this test is checking nothing"
    path = opened.group(1)

    source = worker.decode("utf-8")
    assert f'"{path}"' in source, (
        f"the app streams from {path} and the worker never mentions it, so the "
        f"stream falls through to the caching branch"
    )
    # And the second line of defence, which catches the next streaming route
    # without anybody remembering to add it to a list.
    assert "text/event-stream" in source
    assert "must never be cached" in source or "not a document" in source


def test_the_stream_path_the_worker_excludes_is_a_route_that_exists(server):
    """The other half of the same defect: the excluded path must be real.

    `/events` was excluded and was never served. Checking that the worker and
    the client agree is not enough on its own — they could agree on a path the
    server does not have.
    """
    _status, _headers, client = _raw(server, "/app.js")
    opened = re.search(r'new EventSource\(\s*"([^"]+)"', client.decode("utf-8"))
    path = opened.group(1)

    _status, body = call(server, "/api/routes")
    assert f"GET {path}" in body["routes"], (
        f"{path} is not in the route table, so no generated client can find the "
        f"live feed even though the interface depends on it"
    )


def test_a_cached_api_answer_is_marked_stale_rather_than_served_as_live(server):
    """The honesty rule does not weaken because the client is a laptop on a plane."""
    _status, _headers, body = _raw(server, "/sw.js")
    source = body.decode("utf-8")

    assert "x-slpie-stale" in source
    assert "networkFirst" in source, "an environment answer must try the network first"


def test_nothing_in_the_interface_reaches_an_external_origin(server):
    """The kernel's zero-dependency rule applies to the UI too: no CDN, no fonts,
    no analytics. Asserted rather than trusted, because one `<link>` would do it."""
    import re

    for path in ("/", "/app.js", "/compose.js", "/styles.css", "/sw.js"):
        _status, _headers, body = _raw(server, path)
        text = body.decode("utf-8")
        external = re.findall(
            r"""https?://(?!127\.0\.0\.1|localhost)[a-z0-9.\-]+""", text,
        )
        assert not external, f"{path} reaches {external}"


def _stylesheet(server, entry="/styles.css", seen=None):
    """The whole stylesheet as the browser assembles it, following `@import`.

    `styles.css` is an import root now, split by axis — palette in one file,
    geometry in another, so two visual registers cost forty declarations rather
    than a second copy of every screen. A test that reads only the root would
    have gone green over six lines of `@import` and checked nothing.
    """
    seen = seen if seen is not None else set()
    if entry in seen:
        return ""
    seen.add(entry)

    _status, _headers, body = _raw(server, entry)
    css = body.decode("utf-8")

    base = entry.rsplit("/", 1)[0]
    for target in re.findall(r'@import\s+(?:url\()?"([^"]+)"', css):
        css += _stylesheet(server, f"{base}/{target}", seen)
    return css


def test_the_stylesheet_collapses_the_layout_for_a_phone(server):
    css = _stylesheet(server)

    assert "@media (max-width: 720px)" in css, "no phone breakpoint"
    assert "grid-template-columns: 1fr" in css, "the grid never collapses"
    assert "prefers-reduced-motion" in css


def test_every_imported_stylesheet_is_actually_served(server):
    """The hole `@import` opens, closed.

    The HTML asset test follows `src=` and `href=` and does not follow
    `@import`, so a stylesheet that failed to install would leave a page that
    loads, renders unstyled, and passes every existing assertion.
    """
    imported = re.findall(
        r'@import\s+(?:url\()?"([^"]+)"',
        _raw(server, "/styles.css")[2].decode("utf-8"),
    )
    assert imported, "styles.css imports nothing; this test is checking nothing"

    for target in imported:
        status, _headers, body = _raw(server, f"/{target}")
        assert status == 200, f"styles.css imports {target}, which is not served"
        assert body, f"{target} is served and empty"


def test_the_two_registers_are_independent_axes(server):
    """Density changes geometry; theme changes palette. Neither touches the other.

    This is the property that makes a second register cheap, and it is exactly
    the property that erodes first — one `--bg` in the density block and the two
    axes are entangled for good.
    """
    palette = _raw(server, "/styles/tokens.css")[2].decode("utf-8")
    geometry = _raw(server, "/styles/density.css")[2].decode("utf-8")

    for token in ("--row-h", "--fs-md", "--sp-1"):
        assert token not in palette, f"{token} is geometry and is declared in the palette"
    for token in ("--bg", "--text", "--accent"):
        assert token not in geometry, f"{token} is a colour and is declared in the geometry"

    assert '[data-density="reading"]' in geometry, "there is only one register"
    assert '[data-theme="light"]' in palette, "there is only one theme"


def test_no_component_declares_a_raw_size(server):
    """The single rule that keeps the density axis real rather than aspirational.

    One hardcoded `padding: 16px` in a component and that component stops
    responding to the register, which is how a token axis quietly becomes
    decoration. Hairlines and fully-rounded pills are exempt: neither is a size
    that should change with the register.
    """
    allowed = {"1px", "2px", "3px", "999px"}

    for sheet in ("/styles/components.css", "/styles/screens.css"):
        css = _raw(server, sheet)[2].decode("utf-8")
        # Ignore media queries: a breakpoint is a device fact, not a token.
        body = re.sub(r"@media[^{]+\{", "{", css)
        sizes = {
            size for size in re.findall(r"(?<![\w-])(\d+px)", body)
            if size not in allowed
        }
        assert not sizes, f"{sheet} hardcodes {sorted(sizes)} instead of using a token"


def test_the_compose_view_is_reachable_from_the_navigation(server):
    _status, _headers, body = _raw(server, "/")
    html = body.decode("utf-8")

    assert 'data-view="compose"' in html
    assert 'id="view-compose"' in html
    assert 'src="/compose.js"' in html
    assert 'rel="manifest"' in html


def test_the_compose_view_builds_itself_from_the_registry_not_from_a_hard_list(
    server,
):
    """A palette listing verbs by hand would drift the moment one was added."""
    _status, _headers, body = _raw(server, "/compose.js")
    source = body.decode("utf-8")

    assert 'api.get("verbs")' in source
    assert 'api.get("manual")' in source
    assert "checkPipeline" in source, "the client type-checks locally"
    assert "api.post(\"run\"" in source


def test_the_client_side_type_check_mirrors_the_servers_rule(server):
    """It must agree with the server, or the builder offers compositions the
    server then refuses — which reads as a bug in the platform."""
    _status, _headers, body = _raw(server, "/compose.js")
    source = body.decode("utf-8")

    assert '"any"' in source, "polymorphic verbs accept anything"
    assert '"same"' in source, "passthrough verbs keep the kind"
    assert '"nothing"' in source, "a source verb starts from nothing"


def _raw(server, path: str):
    """One GET, returning status, headers and body. No JSON assumed."""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{server.port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
