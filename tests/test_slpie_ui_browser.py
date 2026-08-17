"""The interface, in a real browser.

Four things this project needs proven cannot be string-matched, and every one of
them is a claim §30 makes:

1. the app **boots** — the modules load, resolve and run with no console error;
2. the **density axis is real** — switching registers changes geometry and *only*
   geometry, and switching theme changes palette and only palette;
3. a **deep link is a location** — a pasted URL restores the same state a
   click-through produces;
4. **refusals read as refusals** — a 403 renders with the accent, not the danger
   colour.

The structural suite proves the tree is well-formed and the node suite proves the
store's rules. Neither can prove any of the above, because all four are facts
about what a browser does with the files.

Marked `browser` and deselected by default. Playwright is an `e2e` extra, never a
kernel dependency — invariant 4 is about a clean `pip install -e .` with no
extras, and this is one, exactly as `xlsx` and `workspaces` are. Skipped loudly
where it is absent, so nobody mistakes "not run" for "passed".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slpie.ui import UiServer

pytestmark = pytest.mark.browser

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
    with playwright.sync_playwright() as driver:
        launched = driver.chromium.launch(
            headless=True, executable_path=_executable(),
        )
        yield launched
        launched.close()


@pytest.fixture()
def page(browser):
    """A page, with everything it logs collected.

    The console is the point: a module that fails to load says so there and
    nowhere else, and a test that does not read it would watch a blank page and
    call it a pass.
    """
    opened = browser.new_page()
    opened.errors = []
    opened.on("console", lambda m: m.type == "error" and opened.errors.append(m.text))
    opened.on("pageerror", lambda e: opened.errors.append(f"pageerror: {e}"))
    yield opened
    opened.close()


@pytest.fixture()
def served():
    running = UiServer(engine=None, port=0).start()
    yield running
    running.stop()


#: A manifest with enough shape to produce a real graph: several kinds, a
#: boundary, and services that depend on each other.
POPULATED = """
apiVersion: slpie/v1
environment: acme-production
target: simulated
security:
  concerns: [pci-dss]
  boundaries:
    - name: cardholder-data
      contains: [payments, vault]
codebase:
  - root: ./services/payments
    team: payments
  - root: ./services/vault
    team: security
data:
  - uri: postgres://analytics/orders
    kind: database
network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
"""


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    """A server with an environment actually open.

    Every visual test until now ran against `engine=None`, which meant every
    screen was an empty state — the graph in particular renders *nothing*
    without data, so its layout, its hit targets and its selection behaviour
    were entirely unexercised. An empty engine cannot fail those, which is the
    same vacuous pass §29 Stage 1 went hunting for, wearing a different hat.
    """
    from slpie.engine import Engine

    engine = Engine.from_text(POPULATED)
    engine.declare()
    engine.simulate(root=str(tmp_path_factory.mktemp("world")))
    engine.attach()
    engine.scan()

    running = UiServer(engine=engine, port=0).start()
    yield running
    running.stop()


def _open(page, served, fragment=""):
    page.goto(served.url + fragment, wait_until="networkidle")
    page.wait_for_timeout(400)
    return page


# --- it boots ----------------------------------------------------------------


def test_the_app_boots_with_no_console_error(page, served):
    """Thirty modules, four tiers, no bundler. Either they all load or none do."""
    _open(page, served)

    assert page.title() == "SLPIE"
    assert page.evaluate("document.getElementById('outlet').childElementCount") > 0
    # 4xx from routes that need an environment is the *expected* answer here and
    # is not a JavaScript error; anything the console calls an error is.
    real = [
        message for message in page.errors
        if "Failed to load resource" not in message
    ]
    assert not real, f"the interface logged {real}"


def test_the_rail_lists_destinations_and_never_their_views(page, served):
    """The rail is a map of the product, not a table of contents.

    A screen declaring a `parent` is a *view of* something — Node and Impact and
    Cycles are things you look at about a graph — so it belongs on its parent's
    page as a tab and never as a rail row beside its own parent. The manifest has
    always carried the hierarchy (`node` declares `crumbs=("graph",)`); the rail
    ignored it and listed eleven Operate rows, four of which were details of
    another row.

    Both directions are asserted, because only the second one fails when the
    filter is dropped: every destination appears, and no view does.
    """
    _open(page, served)
    listed = page.eval_on_selector_all(
        ".rail nav a", "els => els.map(e => e.textContent.trim())",
    )
    assert listed, "the rail rendered nothing — did the shell markup move?"

    manifest = page.evaluate("window.__screens || []")
    if not manifest:
        manifest = _manifest_from_contract()

    destinations = {screen["title"] for screen in manifest if not screen["parent"]}
    views = {screen["title"] for screen in manifest if screen["parent"]}

    assert set(listed) == destinations, "the rail is not the destination set"
    assert not (set(listed) & views), (
        f"the rail is listing views of other screens: {sorted(set(listed) & views)}"
    )
    # The guard that would have caught the original defect: Node, Impact and
    # Cycles are views of Graph and must not sit beside it.
    assert "Graph" in listed
    for view in ("Node", "Impact", "Cycles", "Reconciliation", "History"):
        assert view not in listed, f"{view} is a view of another screen"


def _manifest_from_contract():
    from slpie.ui.contract import screens

    return [screen.to_dict() for screen in screens()]


def test_the_palette_is_built_from_the_registry(page, served):
    """Fifty-odd chips, one per verb, none of them written in the HTML."""
    _open(page, served, "#/compose")
    assert page.eval_on_selector_all(".chip", "e => e.length") > 40


# --- the two axes are independent --------------------------------------------


def _token(page, name):
    return page.evaluate(
        f"getComputedStyle(document.documentElement).getPropertyValue('{name}').trim()",
    )


def test_switching_register_changes_geometry_and_only_geometry(page, served):
    """The property that makes a second register cost forty declarations.

    One `--bg` in the density block and the two axes are entangled for good, and
    nothing but this would notice.
    """
    _open(page, served)
    before = (_token(page, "--row-h"), _token(page, "--bg"))

    page.evaluate("document.documentElement.dataset.density = 'reading'")
    after = (_token(page, "--row-h"), _token(page, "--bg"))

    assert after[0] != before[0], "the register changed nothing"
    assert after[1] == before[1], "the register changed a colour"


def test_switching_theme_changes_palette_and_only_palette(page, served):
    """The other half of the axis split, and it switches to the theme that is
    not already on.

    Written against a literal `'light'` this passed only while dark was the
    default, and silently became a no-op assertion the day the default flipped —
    "the theme changed nothing" firing on a theme switch that never happened.
    The target is derived from what is actually applied instead.
    """
    _open(page, served)
    current = page.evaluate("document.documentElement.dataset.theme") or "light"
    other = "dark" if current == "light" else "light"

    before = (_token(page, "--bg"), _token(page, "--row-h"))
    page.evaluate(f"document.documentElement.dataset.theme = '{other}'")
    after = (_token(page, "--bg"), _token(page, "--row-h"))

    assert after[0] != before[0], f"switching {current} to {other} changed nothing"
    assert after[1] == before[1], "the theme changed a size"


def test_the_appearance_controls_name_what_they_switch_to(page, served):
    """A button is a verb.

    Both controls used to be labelled with the state already applied, so the
    theme button read "light" on a page that was already light — a control that
    looks like it was pressed and ignored. The label must name the destination,
    and it must flip when pressed.
    """
    _open(page, served)
    buttons = page.query_selector_all("#appearance button")
    assert len(buttons) == 2, "the appearance controls did not render"

    theme_button = buttons[1]
    applied = page.evaluate("document.documentElement.dataset.theme") or "light"
    assert theme_button.text_content().strip().lower() != applied, (
        "the theme button names the theme already applied"
    )

    theme_button.click()
    switched = page.evaluate("document.documentElement.dataset.theme")
    assert switched != applied, "pressing the theme button changed nothing"
    assert theme_button.text_content().strip().lower() != switched, (
        "the label did not follow the switch"
    )


def test_the_register_survives_a_reload(page, served):
    """A choice that does not persist is a choice somebody makes once and then
    stops making."""
    _open(page, served)
    page.evaluate("window.localStorage.setItem('slpie.density', 'reading')")
    _open(page, served)

    assert page.evaluate("document.documentElement.dataset.density") == "reading"


# --- a deep link is a location -----------------------------------------------


def test_a_deep_link_restores_the_same_state_a_click_produces(page, served):
    """Four nested optional parameters, and a breadcrumb from the manifest
    rather than from history — so a pasted URL shows the trail a click-through
    shows, which is the whole reason a deep link is worth having."""
    _open(page, served, "#/catalog/acme/prod")

    crumbs = page.eval_on_selector_all(
        "nav[aria-label=breadcrumb] a, nav[aria-label=breadcrumb] span",
        "els => els.map(e => e.textContent)",
    )
    assert "acme" in crumbs and "prod" in crumbs


def test_an_unknown_route_lands_on_the_console_rather_than_a_blank_page(page, served):
    _open(page, served, "#/no-such-screen")
    assert page.evaluate("document.getElementById('outlet').childElementCount") > 0


# --- refusals read as refusals ------------------------------------------------


def test_a_state_the_platform_cannot_answer_reads_as_empty_not_as_a_fault(
    page, served,
):
    """409 means "ask again once an environment is open", which is a different
    answer from "the server broke" — and the interface has to show that."""
    _open(page, served, "#/admin/workspaces")

    body = page.inner_text("#outlet")
    assert "control plane" in body.lower() or "no " in body.lower()
    assert page.eval_on_selector_all(".fault", "e => e.length") == 0, (
        "a state the platform is entitled to be in rendered as a platform fault"
    )


def test_a_refusal_uses_the_accent_and_never_the_danger_colour(page, served):
    """Rendering policy in red teaches people that policy is a bug, and then
    they file tickets about their own permissions.

    Asserted on the stylesheet as the browser computed it, rather than on the
    source text, so a later override cannot quietly undo it.
    """
    _open(page, served)
    colours = page.evaluate(
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
    assert colours["refusal"] != colours["fault"]


# --- accessibility ------------------------------------------------------------


def test_every_table_labels_its_columns(page, served):
    """The difference between a table a screen reader can navigate and a grid of
    unlabelled cells."""
    _open(page, served, "#/compose")
    unlabelled = page.evaluate(
        """() => Array.from(document.querySelectorAll('table'))
              .filter(t => Array.from(t.querySelectorAll('th'))
                .some(h => !h.getAttribute('scope'))).length""",
    )
    assert unlabelled == 0


def test_the_keyboard_reaches_the_interactive_elements(page, served):
    """A console this size is worked from a keyboard, and keyboard use is not a
    niche: it is how anybody working a list all day actually works it."""
    _open(page, served, "#/compose")

    reached = []
    for _ in range(8):
        page.keyboard.press("Tab")
        reached.append(page.evaluate("document.activeElement.tagName"))

    assert {"A", "BUTTON", "INPUT", "SELECT"} & set(reached), (
        f"eight tabs reached only {sorted(set(reached))}"
    )


# --- the graph, against real data --------------------------------------------


def test_the_graph_draws_the_estate(page, populated):
    """The screen the platform should be judged on, with something in it.

    Asserted against a populated engine because an empty one draws nothing and
    would pass every check here by having no marks to get wrong.
    """
    _open(page, populated, "#/graph")

    nodes = page.eval_on_selector_all(".node", "els => els.length")
    wires = page.eval_on_selector_all(".wire", "els => els.length")
    assert nodes > 5, f"only {nodes} nodes drawn — the diagram is empty"
    assert wires > 5, f"only {wires} edges drawn"

    real = [m for m in page.errors if "Failed to load resource" not in m]
    assert not real, f"the graph logged {real}"


def test_no_column_grows_past_the_wrap_limit(page, populated):
    """A power-law kind distribution must not produce a spike and twelve stubs.

    One kind holds most of the nodes in any real estate. Unwrapped, that drew a
    seventeen-deep column beside twelve single-node ones, leaving most of the
    canvas empty and the tall column's labels crossed by every edge. The wrap is
    what keeps the drawing roughly rectangular whatever the distribution.
    """
    _open(page, populated, "#/graph")

    ys = page.eval_on_selector_all(
        ".node",
        """els => els.map(e => {
             const t = e.getAttribute('transform') || '';
             const m = t.match(/translate\\(([-\\d.]+) ([-\\d.]+)\\)/);
             return m ? [Number(m[1]), Number(m[2])] : null;
           }).filter(Boolean)""",
    )
    assert ys, "no nodes were placed"

    per_column = {}
    for x, _ in ys:
        per_column[x] = per_column.get(x, 0) + 1
    assert max(per_column.values()) <= 11, (
        f"a column holds {max(per_column.values())} nodes; the wrap limit is 11"
    )


def test_picking_a_node_dims_what_it_does_not_touch(page, populated):
    """Selection is the interaction the screen exists for.

    The dimming is the point: an unconnected node stays visible at low opacity
    rather than disappearing, because the shape of what is *not* connected is
    information too.
    """
    _open(page, populated, "#/graph")

    page.eval_on_selector(".node .hit", "e => e.dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    page.wait_for_timeout(300)

    opacities = page.eval_on_selector_all(
        ".node", "els => els.map(e => Number(e.getAttribute('opacity')))",
    )
    assert 1 in opacities, "nothing stayed at full strength"
    assert any(0 < o < 1 for o in opacities), "nothing was dimmed"
    assert 0 not in opacities, "a node was hidden rather than dimmed"


def test_an_edge_never_swallows_a_click_meant_for_a_node(page, populated):
    """Every edge terminates exactly on a dot, so its painted stroke sits over
    the target the reader is aiming at. Found by a click that Playwright refused
    to deliver, reporting the wire as the interception."""
    _open(page, populated, "#/graph")

    events = page.eval_on_selector_all(
        ".wires path", "els => els.map(e => getComputedStyle(e).pointerEvents)",
    )
    assert events, "no wires drawn"
    assert set(events) == {"none"}, f"wires are hit-testable: {set(events)}"
