"""The built shell, in a real browser, over the bundle that actually ships.

`tests/test_slpie_ui_browser.py` makes four claims about the stdlib console.
This module makes the same four about the enterprise one, because a second
shell that is never rendered is a second shell nobody can trust:

1. it **boots** — the bundle loads and runs with no console error;
2. it draws **only what ring 0 declines**, from the manifest rather than from a
   list it carries;
3. **refusals read as refusals** — a 403 renders with the accent, never the
   danger colour;
4. the **workbench is reachable** — the pane divider and the scrubber are
   labelled and operable, which is where a hand-rolled console usually fails.

And one more that belongs only here: **the estate on screen is the estate the
API returned.** The screen this replaced built nine hundred synthetic nodes, so
the test that matters most is the one that counts what was actually drawn
against what was actually served.

Everything is asserted against `clients/web/dist` — the built output, not the
source — because a bundler is a program that can be wrong, and the artifact the
customer runs is the one worth proving. It is served by a plain stdlib static
server and the API is answered by Playwright's own routing, so the test needs no
kernel process and no database: what is under test is the shell.
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "clients" / "web" / "dist"

#: Chromium ships with this image; Playwright is told where it is rather than
#: downloading its own, which would need a network the suite does not have.
CHROMIUM = Path("/opt/pw-browsers")


def _executable() -> str | None:
    found = sorted(CHROMIUM.glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="playwright is not installed; run `pip install -e '.[e2e]'`",
    )
    if not (DIST / "index.html").is_file():
        pytest.skip(
            "clients/web is not built — run `npm --prefix clients/web run build`"
        )
    with playwright.sync_playwright() as driver:
        launched = driver.chromium.launch(headless=True, executable_path=_executable())
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def bundle():
    """The built output, on a port, exactly as a static host would serve it."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(DIST))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def page(browser):
    opened = browser.new_page()
    opened.errors = []
    opened.on("console", lambda m: m.type == "error" and opened.errors.append(m.text))
    opened.on("pageerror", lambda e: opened.errors.append(f"pageerror: {e}"))
    yield opened
    opened.close()


# --- what the platform answers ------------------------------------------------

#: Deliberately small and deliberately *counted*. Every assertion below compares
#: what the screen says against these numbers, which is what makes "the estate on
#: screen is the estate that was served" checkable rather than asserted.
NODES = [
    {"id": "n-payments", "display": "payments", "kind": "service"},
    {"id": "n-vault", "display": "vault", "kind": "service"},
    {"id": "n-lodash", "display": "lodash", "kind": "package"},
    {"id": "n-orders", "display": "orders", "kind": "table"},
]
EDGES = [
    {"src": "n-payments", "dst": "n-lodash"},
    {"src": "n-vault", "dst": "n-lodash"},
    # Names a node the node half of the answer did not include: the screen must
    # drop it rather than draw a line to a mark that is not there.
    {"src": "n-payments", "dst": "n-absent"},
]

SHELLS = {
    "capabilities": {
        "webgl": "a hardware-accelerated 3D surface",
        "split-pane": "resizable panes the reader arranges",
        "timeline": "a scrubbable time axis over the ledger",
        "drag": "direct manipulation",
    },
    "shells": [
        {
            "name": "stdlib", "title": "The console", "native": True, "built": False,
            "provides": ["blocks", "table", "webgl"],
            "renders": ["console", "findings", "graph"],
            "cannot": {"flight": ["split-pane", "timeline", "drag"]},
        },
        {
            "name": "web", "title": "The enterprise console",
            "native": False, "built": True,
            "provides": ["webgl", "split-pane", "timeline", "drag"],
            "renders": ["flight"], "cannot": {},
        },
    ],
}

IMPACT = {
    "root": "n-lodash",
    "total": 5,
    "summary": "5 impacted",
    "impacted": [
        {"node_id": "n-payments", "distance": 1, "confidence": 0.90,
         "display": "payments", "kind": "service"},
        {"node_id": "n-vault", "distance": 1, "confidence": 0.40,
         "display": "vault", "kind": "service"},
        {"node_id": "n-orders", "distance": 2, "confidence": 0.90,
         "display": "orders", "kind": "table"},
        {"node_id": "n-billing", "distance": 2, "confidence": 0.85,
         "display": "billing", "kind": "service"},
        {"node_id": "n-ledger", "distance": 3, "confidence": 0.85,
         "display": "ledger", "kind": "service"},
    ],
}

FINDINGS = {"findings": [
    {"id": "f1", "subject": "n-lodash", "severity": "critical",
     "detail": "known vulnerable"},
    # A lower severity on the same subject, to prove the join keeps the worst
    # rather than the last one it happened to read.
    {"id": "f2", "subject": "n-lodash", "severity": "low", "detail": "unmaintained"},
]}


def _answers(page, *, shells=None, status=200):
    """Answer the platform's routes from these fixtures, and nothing else.

    A route the screen asks for that is not listed here fails the request rather
    than falling through to the static server, so an unexpected call shows up as
    a console error instead of a silent 404 rendered as an empty answer.
    """
    payloads = {
        "/api/shells": shells if shells is not None else SHELLS,
        "/api/graph": {"nodes": NODES, "edges": EDGES,
                       "counts": {"nodes": len(NODES)}, "by_kind": {}},
        "/api/findings": FINDINGS,
        "/api/impact": IMPACT,
    }

    def handle(route):
        path = route.request.url.split("?")[0].split("127.0.0.1:")[1]
        path = path[path.index("/"):]
        body = payloads.get(path)
        if body is None:
            route.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"error": f"no stub for {path}"}))
            return
        route.fulfill(
            status=status if path == "/api/shells" else 200,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/api/**", handle)


def _open(page, bundle, **kwargs):
    _answers(page, **kwargs)
    page.goto(bundle, wait_until="networkidle")
    return page


# --- 1. it boots --------------------------------------------------------------


def test_the_built_shell_boots_with_no_console_error(page, bundle):
    """The bundle loads and runs. Everything below assumes it; nothing else does.

    A module that fails to load says so in the console and nowhere else, and a
    test that did not read it would watch a blank page and call it a pass.
    """
    _open(page, bundle)
    assert page.locator("h1").inner_text().startswith("SLPIE")
    assert not page.errors, page.errors


# --- 2. only what ring 0 declines --------------------------------------------


def test_it_draws_the_screen_ring_zero_declines_and_no_other(page, bundle):
    """The manifest decides, not a list in the shell.

    `flight` is what the stdlib console cannot draw, so `flight` is what appears.
    The screens it *can* draw — console, findings, graph — must be absent, or
    this shell has quietly become a second console maintaining a second copy of
    every screen.
    """
    _open(page, bundle)
    assert page.locator(".workbench").count() == 1, "the flight workbench is missing"

    body = page.locator("body").inner_text().lower()
    for elsewhere in ("findings", "catalog", "workspaces"):
        assert f"\\n{elsewhere}" not in body, (
            f"the built shell drew {elsewhere}, which the stdlib console already draws"
        )


def test_a_screen_neither_shell_can_draw_says_so_rather_than_vanishing(page, bundle):
    """The refusal card in reverse, and the reason the `cannot` map is read twice.

    A capability the platform has and no surface can reach is drift (§24). The
    honest rendering is to name it and name what it needs — omitting it would
    hide the gap from the only people who could close it.
    """
    stranded = json.loads(json.dumps(SHELLS))
    stranded["shells"][0]["cannot"]["hologram"] = ["holography"]
    stranded["shells"][1]["cannot"]["hologram"] = ["holography"]
    stranded["capabilities"]["holography"] = "a volumetric display"

    _open(page, bundle, shells=stranded)
    text = page.locator("body").inner_text()
    assert "hologram" in text
    assert "Neither console can draw this screen" in text
    assert "a volumetric display" in text


# --- 3. the estate is the estate that was served ------------------------------


def test_the_screen_draws_what_the_api_returned_and_nothing_it_invented(page, bundle):
    """The assertion the deleted generator makes necessary.

    Four nodes were served, so four nodes are read. The screen this replaced
    would have reported nine hundred with the API answering exactly the same
    way, and it would have looked entirely convincing.

    The edge count is the second half: three edges were served and one of them
    names a node the answer did not include, so two survive. A screen drawing
    three would be drawing a line to a mark that is not on the surface.
    """
    _open(page, bundle)
    numbers = _readings(page)
    assert numbers["Nodes read"] == str(len(NODES))
    assert numbers["Edges between them"] == "2"


def test_the_severity_join_keeps_the_worst_finding_against_a_subject(page, bundle):
    """Two findings on one node, and the graph must wear the worse of them.

    Severity is not a property of a node — it is what governance raised against
    one — so the screen joins them, and the join has to be a maximum. Keeping
    whichever arrived last would make the same estate look different depending
    on the order a query returned.
    """
    _open(page, bundle)
    options = page.locator("select option").all_inner_texts()
    assert "[critical] lodash" in options, options
    assert "[low] lodash" not in options


# --- 4. refusals read as refusals ---------------------------------------------


def test_a_refusal_uses_the_accent_and_never_the_danger_colour(page, bundle):
    """Rendering policy in red teaches people that policy is a bug.

    Asserted on the stylesheet as the browser computed it rather than on the
    source, so a later override cannot quietly undo it — and asserted on the
    built bundle, where a bundler could have dropped the import that carries the
    tokens at all.
    """
    _open(page, bundle)
    shades = page.evaluate(
        """() => {
            const probe = (cls) => {
                const node = document.createElement('div');
                node.className = cls;
                document.body.appendChild(node);
                const shade = getComputedStyle(node).borderLeftColor;
                node.remove();
                return shade;
            };
            return {refusal: probe('refusal'), fault: probe('fault')};
        }""",
    )
    assert shades["refusal"] != shades["fault"]
    # And neither is the default, which is what a dropped stylesheet would give.
    assert shades["refusal"] not in ("", "rgb(0, 0, 0)")


def test_a_refused_read_renders_the_way_out_rather_than_a_stack_trace(page, bundle):
    """A refusal is an answer with a reason, and the reason includes the remedy.

    The gateway computed `stage` and `obligation`; a shell that kept only the
    status would leave the reader asking an operator for something the platform
    already said.
    """
    def refuse(route):
        route.fulfill(
            status=403,
            content_type="application/json",
            body=json.dumps({
                "error": "no grant for platform.discover on api:shells",
                "refused": True,
                "stage": "authorize",
                "obligation": "subscribe at #/portal/platform",
            }),
        )

    page.route("**/api/**", refuse)
    page.goto(bundle, wait_until="networkidle")

    text = page.locator("body").inner_text()
    assert "no grant for platform.discover" in text
    assert "authorize" in text
    assert "subscribe at #/portal/platform" in text
    # A refusal is not a fault, and the shell must not claim the platform broke.
    assert "platform fault" not in text
    assert page.locator(".refusal").count() >= 1
    assert page.locator(".fault").count() == 0


def test_a_transport_failure_reads_as_a_fault_rather_than_as_policy(page, bundle):
    """The other half of the same rule, and the one that is easy to get wrong.

    A 500 or a dropped connection is nobody's decision. Rendering it in the
    accent — the colour reserved for policy — would tell a reader their
    permissions were the problem while the server was on fire.
    """
    page.route("**/api/**", lambda route: route.fulfill(
        status=500, content_type="application/json",
        body=json.dumps({"error": "the projection could not be rebuilt"}),
    ))
    page.goto(bundle, wait_until="networkidle")

    assert page.locator(".fault").count() >= 1
    assert "platform fault" in page.locator("body").inner_text()


# --- 5. the workbench is operable ---------------------------------------------


def test_the_pane_divider_announces_itself(page, bundle):
    """The capability this shell exists for, and the one nobody labels.

    `split-pane` is one of the three capabilities that put this screen here at
    all. A divider that a screen reader cannot find makes the screen unusable
    for exactly the reader who most needs the panel beside the canvas.
    """
    _open(page, bundle)
    handle = page.locator('[role="separator"]')
    assert handle.count() == 1
    assert handle.get_attribute("aria-label")
    assert handle.get_attribute("aria-orientation") == "vertical"


def test_choosing_a_target_offers_the_ride_with_its_hops_counted(page, bundle):
    """The rail is the `impact` answer, and the timeline scrubs it.

    Five hops were served, so five ticks are drawn — not four, not a smooth bar
    with a number beside it. A hop you can *see* is the whole argument for
    drawing the rail instead of printing the count.
    """
    _open(page, bundle)
    page.select_option("select", "n-lodash")
    page.wait_for_selector(".rail .tick")

    assert page.locator(".rail .tick").count() == len(IMPACT["impacted"])
    # The floor is the weakest hop and the summary says so, rather than an
    # average that would flatter the answer.
    assert "bounded at 0.40" in page.locator("body").inner_text()

    scrubber = page.locator('input[type="range"]')
    assert scrubber.count() == 1
    assert scrubber.get_attribute("aria-label")


def test_scrubbing_takes_the_controls_rather_than_fighting_the_reader(page, bundle):
    """Manual input always wins, and the condition says so.

    A camera that wrestles the reader for the wheel is the specific way this
    class of interface becomes unusable, and the rule that prevents it is a
    transition: any touch during a traverse yields `held`.
    """
    _open(page, bundle)
    page.select_option("select", "n-lodash")
    page.wait_for_selector(".transport button")

    page.click(".transport button")            # fly it
    page.wait_for_timeout(120)
    page.locator('input[type="range"]').fill("1.2")

    assert "held" in page.locator("body").inner_text()


def test_nothing_spatial_is_drawn_before_a_selection(page, bundle):
    """§32's rule, which three prototypes broke by opening into a point cloud.

    A condition that renders nothing is the mechanism that enforces it, so the
    canvas must genuinely not be in the document — not merely be empty.
    """
    _open(page, bundle)
    assert page.locator(".stage canvas").count() == 0

    page.select_option("select", "n-lodash")
    page.wait_for_selector(".stage canvas")
    assert page.locator(".stage canvas").count() == 1


def _readings(page) -> dict[str, str]:
    """The panel's definition list, as a mapping."""
    return page.evaluate(
        """() => {
            const out = {};
            document.querySelectorAll('dl').forEach((list) => {
                const terms = [...list.querySelectorAll('dt')];
                const values = [...list.querySelectorAll('dd')];
                terms.forEach((term, index) => {
                    out[term.textContent.trim()] = (values[index]?.textContent || '').trim();
                });
            });
            return out;
        }""",
    )
