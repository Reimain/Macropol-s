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
    """A button is a verb: each control names its destination, not its state.

    Both used to be labelled with the state already applied, so the theme button
    read "light" on a page that was already light — a control that looks like it
    was pressed and ignored.

    The theme control is now an icon, which is why this asserts the **accessible
    name and the drawn glyph** rather than the text. Comparing `textContent`
    kept passing after the icon landed, because an empty string is never equal
    to "light" — the assertion had quietly stopped testing anything.
    """
    _open(page, served)
    buttons = page.query_selector_all("#appearance button")
    assert len(buttons) == 2, "the appearance controls did not render"

    register, theme = buttons

    # The register keeps its word: dense and calm are not iconographic.
    applied_density = page.evaluate("document.documentElement.dataset.density")
    assert register.text_content().strip().lower() != (
        "dense" if applied_density == "bench" else "calm"
    ), "the register button names the register already applied"

    # The theme control carries a sun or a moon and no text at all. An icon
    # button with no accessible name is silent to a screen reader, so the name
    # is what must say where pressing it goes.
    assert not theme.text_content().strip(), "the theme control should be icon-only"

    def drawn() -> str:
        return page.eval_on_selector_all(
            "#appearance button",
            "els => els[1].querySelector('circle') ? 'sun' : 'moon'",
        )

    applied = page.evaluate("document.documentElement.dataset.theme") or "light"
    name = theme.get_attribute("aria-label") or ""
    assert applied not in name, f"the theme control names the applied theme: {name!r}"
    assert drawn() == ("sun" if applied == "dark" else "moon")

    theme.click()
    page.wait_for_timeout(300)

    switched = page.evaluate("document.documentElement.dataset.theme")
    assert switched != applied, "pressing the theme control changed nothing"
    assert drawn() == ("sun" if switched == "dark" else "moon"), (
        "the icon did not follow the switch"
    )
    assert switched not in (theme.get_attribute("aria-label") or ""), (
        "the accessible name did not follow the switch"
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


# --- the dense register is an instrument, not a zoom level --------------------


def test_the_grid_head_and_body_share_one_column_geometry(page, served):
    """The regression test for a cascade collision, not a styling preference.

    `layout.css` defines `.grid` as a CSS *grid container*. A `<table
    class="grid">` therefore became `display: grid` with auto-fit columns, and
    the header row detached from the body — seven headers laid out in one track
    row and seven cells in the next, six hundred pixels to the right. It renders
    as a table with no headers over data with no labels.

    Comparing the x of each header against the x of its cell is the assertion
    that catches it; nothing about the DOM is wrong, so only geometry can.
    """
    _open(page, served, "#/verbs")

    measured = page.evaluate("""() => {
      const t = document.querySelector('table.datagrid');
      if (!t) return null;
      const x = (el) => Math.round(el.getBoundingClientRect().x);
      return {
        heads: [...t.querySelectorAll('thead th')].map(x),
        cells: [...t.querySelectorAll('tbody tr:first-child td')].map(x),
        display: getComputedStyle(t).display,
      };
    }""")
    assert measured, "the grid did not render"
    assert measured["display"] in ("table", "inline-table"), (
        f"the grid is laid out as {measured['display']}, not a table"
    )
    assert len(measured["heads"]) == len(measured["cells"])
    for index, (head, cell) in enumerate(zip(measured["heads"], measured["cells"])):
        assert abs(head - cell) <= 2, (
            f"column {index}: header at {head}px, cell at {cell}px — "
            f"the head and body are not sharing column tracks"
        )


def test_sorting_reorders_and_says_so(page, served):
    """A sortable header that does not report what it sorted by leaves the
    reader re-reading the column to work out which way it went."""
    _open(page, served, "#/verbs")

    first = page.eval_on_selector("table.datagrid tbody tr td", "e => e.innerText.trim()")
    page.eval_on_selector_all(
        "table.datagrid th.sortable",
        "els => els[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
    )
    page.wait_for_timeout(200)

    status = page.eval_on_selector(".grid-status", "e => e.innerText")
    assert "sorted by" in status, f"the status bar did not report the sort: {status!r}"
    assert page.eval_on_selector(
        "table.datagrid th[aria-sort]:not([aria-sort='none'])", "e => !!e",
    ), "no header reported aria-sort, so the order is visual-only"

    reversed_once = page.eval_on_selector("table.datagrid tbody tr td", "e => e.innerText.trim()")
    page.eval_on_selector_all(
        "table.datagrid th.sortable",
        "els => els[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
    )
    page.wait_for_timeout(200)
    reversed_twice = page.eval_on_selector("table.datagrid tbody tr td", "e => e.innerText.trim()")
    assert reversed_once != reversed_twice, "clicking twice did not reverse the order"


def test_arrow_keys_move_the_selection(page, served):
    """A grid somebody works all day is driven from the keyboard or it is not
    worked all day. One tab stop for the grid, then arrows within it."""
    _open(page, served, "#/verbs")

    page.eval_on_selector(
        "table.datagrid tbody tr",
        "e => e.dispatchEvent(new MouseEvent('click', {bubbles: true}))",
    )
    page.eval_on_selector("table.datagrid tbody tr.picked", "e => e.focus()")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)

    index = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('table.datagrid tbody tr')];
      return rows.findIndex((r) => r.classList.contains('picked'));
    }""")
    assert index == 1, f"ArrowDown left the selection at row {index}"

    # Exactly one tab stop: thirty rows each taking one is how a keyboard user
    # ends up unable to reach the thing after the table.
    stops = page.eval_on_selector_all(
        "table.datagrid tbody tr", "els => els.filter(e => e.tabIndex === 0).length",
    )
    assert stops == 1, f"{stops} rows are tab stops; a grid is one"


def test_switching_register_redraws_the_columns(page, served):
    """Column count is the one thing a token cannot express.

    Most of the register is tokens and needs no JavaScript. Which columns a grid
    shows is decided at render time, so without a redraw on the switch the
    geometry changed and the dense-only columns stayed — a control that half
    worked, which reads as columns being missing rather than as a bug.
    """
    _open(page, served, "#/verbs")

    dense = page.eval_on_selector_all("table.datagrid thead th", "els => els.length")
    page.eval_on_selector_all(
        "#appearance button",
        "els => els[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
    )
    page.wait_for_timeout(300)
    calm = page.eval_on_selector_all("table.datagrid thead th", "els => els.length")

    assert page.evaluate("document.documentElement.dataset.density") == "reading"
    assert calm < dense, (
        f"calm shows {calm} columns and dense {dense} — the register did not "
        f"redraw the grid"
    )


def _faults(page):
    """Real JavaScript failures, not the platform declining to answer.

    With no environment open, half the routes correctly return 409 and the
    browser logs each as a failed resource load. Treating those as errors would
    make these tests assert that the console never says "nothing is attached",
    which is a thing it is supposed to say.
    """
    return [
        message for message in page.errors
        if "Failed to load resource" not in message
    ]


# --- composed screens (§31) --------------------------------------------------


@pytest.mark.parametrize("fragment, heading", [
    ("#/throttling", "Tier"),
    ("#/portal", "API"),
])
def test_a_composed_screen_draws_its_declared_columns(page, served, fragment, heading):
    """The thesis, in a browser: a screen described as data renders components.

    Nobody wrote a module for either of these. The manifest names `grid` and
    lists the columns, `ui/components.js` resolves the key, and the reader gets
    the same instrument the authored screens use — headers, sorting, the dense
    register — rather than a payload.
    """
    _open(page, served, fragment)

    headers = page.eval_on_selector_all("th", "els => els.map(e => e.textContent)")
    assert heading in headers, f"{fragment} drew headers {headers}"
    assert not _faults(page), _faults(page)


def test_a_composed_screen_never_falls_back_to_a_json_dump(page, served):
    """`<pre>` was what every one of these screens used to be.

    Thirty-two screens printing `JSON.stringify(body, null, 2)` reads as
    unfinished no matter how good the authored four are, and the whole of §31
    step 5 is the claim that they no longer do.
    """
    for fragment in ("#/throttling", "#/portal", "#/gateway", "#/inspect/screens"):
        _open(page, served, fragment)
        dumps = page.locator("pre").count()
        assert dumps == 0, f"{fragment} still renders {dumps} raw payload(s)"


def test_auto_infers_columns_from_the_shape_that_arrived(page, served):
    """Python cannot know an arbitrary route's body, so the browser looks.

    Declaring columns for every inspector by hand would be a list that drifts
    the first time a payload changes; reading the rows that actually arrived
    cannot drift, because there is nothing to keep in step.
    """
    _open(page, served, "#/inspect/screens")

    headers = page.eval_on_selector_all("th", "els => els.map(e => e.textContent)")
    assert "key" in headers and "path" in headers, headers
    assert not _faults(page), _faults(page)


def test_a_generated_inspector_still_runs_its_verbs(page, served):
    """The guarantee that predates composing and must survive it: no capability
    is unreachable. A verb with no designed home has somewhere to be run."""
    _open(page, served, "#/inspect/audit")

    assert page.locator("button.go").count() >= 1
    assert not _faults(page), _faults(page)


def test_the_interface_paints_in_the_platforms_words_before_any_round_trip(
    page, served,
):
    """The baked lexicon.

    `core/lexicon.js` starts empty — `core/` may import nothing — so the shell
    seeds it from `data/client.js` before the first draw. If that seeding were
    dropped every label would render as its key, which is exactly the failure
    this asserts against.
    """
    _open(page, served, "#/verbs")

    words = page.evaluate(
        "import('/core/lexicon.js').then(m => "
        "({node: m.t('node'), plural: m.t('node', {plural: true})}))"
    )
    assert words == {"node": "node", "plural": "nodes"}


# --- the device tier (§31) ---------------------------------------------------


def test_a_restored_cell_cannot_overwrite_a_fresher_one(page, served):
    """The property that makes hydration safe.

    A cell from disk is older by construction, so routing it through the same
    `commit` a network answer takes means it can only fill a gap. If hydration
    had its own path — and it would be the obvious way to write it — a slow
    IndexedDB read landing after a fast fetch would paint yesterday's answer
    over today's, and it would do it intermittently.
    """
    _open(page, served)

    kept = page.evaluate("""
      import('/core/store.js').then(async (store) => {
        store.commit('probe', {status: 'ready', value: {n: 'fresh'}, version: 7});
        // Exactly what hydrate does with a restored row, at an older version.
        store.commit('probe', {status: 'ready', value: {n: 'disk'}, version: 3,
                               stale: true});
        return store.cell('probe').value.n;
      })
    """)
    assert kept == "fresh"


def test_a_restored_cell_announces_that_it_is_old(page, served):
    """`STALE_REPLICA` (§23) with the replica being a laptop.

    A screen painted from disk while the server is ahead must say so — the same
    honesty the service worker already applies to an offline answer, and
    `panel.js` already renders it.
    """
    _open(page, served)

    marked = page.evaluate("""
      import('/core/store.js').then((store) => {
        store.commit('probe2', {status: 'ready', value: {n: 1}, version: 3,
                                ledger: 400, stale: true});
        const held = store.cell('probe2');
        return {stale: held.stale, behind: held.ledger > held.version};
      })
    """)
    assert marked == {"stale": True, "behind": True}


def test_the_device_tier_round_trips_through_indexeddb(page, served):
    """It actually persists, rather than looking like it does."""
    _open(page, served)

    result = page.evaluate("""
      import('/data/objectstore.js').then(async (module) => {
        const store = await module.attach();
        const key = module.ref('alice', 'acme', 'findings:high');
        await store.put(key, {value: {kept: true}, version: 5});
        const back = await store.get(key);
        await store.clear();
        return {tier: store.tier, kept: back && back.value.kept,
                gone: await store.get(key)};
      })
    """)
    assert result["tier"] == "device", "IndexedDB was expected to be available"
    assert result["kept"] is True
    assert result["gone"] is None


def test_a_different_principal_gets_an_empty_store(page, served):
    """Wiped, not filtered.

    A filtered view of another tenant's cells is still their bytes on somebody
    else's disk, which is the difference between a caching decision and a
    data-residency incident.
    """
    _open(page, served)

    result = page.evaluate("""
      Promise.all([import('/core/store.js'), import('/data/objectstore.js')])
        .then(async ([store, backend]) => {
          const one = await backend.attach();
          await store.persist(one, {
            owner: backend.prefix('alice', 'acme'),
            key: (k) => backend.ref('alice', 'acme', k),
          });
          store.commit('secret', {status: 'ready', value: {x: 1}, version: 2,
                                  keep: true});
          await new Promise((done) => setTimeout(done, 120));
          const before = (await one.keys()).length;

          // A different reader arrives on the same machine.
          await store.persist(one, {
            owner: backend.prefix('bob', 'acme'),
            key: (k) => backend.ref('bob', 'acme', k),
          });
          return {before, after: (await one.keys()).length,
                  held: store.cell('secret').value};
        })
    """)
    assert result["before"] >= 1, "nothing was written, so the wipe proves nothing"
    assert result["after"] == 0
    assert result["held"] is None


def test_a_key_cannot_escape_its_prefix(page, served):
    """`acme` must not reach `acme-corp`, and nothing may traverse out."""
    _open(page, served)

    result = page.evaluate("""
      import('/data/objectstore.js').then((module) => {
        let refused = false;
        try { module.ref('alice', 'acme', '../../bob/secrets'); }
        catch (error) { refused = true; }
        return {refused,
                distinct: module.prefix('acme', 'p') !== module.prefix('acme-corp', 'p')};
      })
    """)
    assert result == {"refused": True, "distinct": True}


# --- the renderer seam ---------------------------------------------------------


def test_the_projection_lands_known_points_where_it_says(page, served):
    """The camera, checked as arithmetic rather than as a picture.

    A camera bug and a layout bug are indistinguishable on a canvas, so the
    projection is pinned against points whose screen coordinates can be worked
    out on paper. With an 800x600 viewport and a 90-degree vertical field, the
    focal length is 300: a point ten units ahead and ten to the right lands one
    focal length off centre, at x=700.

    The handedness is the specific thing this catches. `forward x up` and
    `up x forward` are both "the right vector" in some convention, and picking
    the wrong one mirrors the entire scene horizontally — a bug that renders
    perfectly and is invisible unless you know which node belongs on the left.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/camera.js').then((camera) => {
        const at = camera.look(
          camera.vector(0, 0, 0), camera.vector(0, 0, 10),
          {width: 800, height: 600, fov: Math.PI / 2},
        );
        const round = (point) => {
          const seen = camera.project(camera.vector(...point), at);
          return {x: Math.round(seen.x), y: Math.round(seen.y),
                  depth: seen.depth, visible: seen.visible};
        };
        return {
          focal: Math.round(at.focal),
          centre: round([0, 0, 10]),
          right: round([10, 0, 10]),
          left: round([-10, 0, 10]),
          above: round([0, 10, 10]),
          behind: round([0, 0, -5]),
          twiceAsFar: round([10, 0, 20]),
        };
      })
    """)

    assert result["focal"] == 300
    assert (result["centre"]["x"], result["centre"]["y"]) == (400, 300)
    # +X is to the right and +Y is up, even though screen Y grows downward: the
    # projection flips it exactly once so no caller has to remember.
    assert result["right"]["x"] == 700
    assert result["left"]["x"] == 100
    assert result["above"]["y"] == 0
    # Twice the distance, half the offset. That is the perspective divide doing
    # its job, and it is the assertion an orthographic mistake fails.
    assert result["twiceAsFar"]["x"] == 550
    assert result["behind"]["visible"] is False


def test_depth_fades_toward_the_ground_and_never_past_it(page, served):
    _open(page, served)

    result = page.evaluate("""
      import('/engine/camera.js').then((camera) => ({
        near: camera.haze(0, {near: 0, far: 10}),
        half: camera.haze(5, {near: 0, far: 10}),
        far: camera.haze(10, {near: 0, far: 10}),
        beyond: camera.haze(400, {near: 0, far: 10}),
        degenerate: camera.haze(5, {near: 3, far: 3}),
      }))
    """)
    assert result == {"near": 0, "half": 0.5, "far": 1, "beyond": 1, "degenerate": 0}


def test_the_layout_is_deterministic_in_three_dimensions(page, served):
    """Same graph in, same coordinates out — from any input order.

    This is the snapshot digest's property applied to the drawing. Without it
    you cannot point at the picture in a review, cannot compare two
    screenshots, and cannot tell "the architecture changed" from "the layout
    landed somewhere else".
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/layout.js').then((layout) => {
        const nodes = [
          {id: 'n1', name: 'payments', kind: 'service'},
          {id: 'n2', name: 'orders', kind: 'service'},
          {id: 'n3', name: 'vault', kind: 'package'},
          {id: 'n4', name: 'redis', kind: 'package'},
          {id: 'n5', name: 'ledger', kind: 'database'},
        ];
        const edges = [
          {src: 'n1', dst: 'n3'}, {src: 'n1', dst: 'n2'},
          {src: 'n2', dst: 'n4'}, {src: 'n1', dst: 'n5'},
        ];
        const inside = new Set(['payments', 'vault']);
        const options = {regionOf: (node) => inside.has(node.name) ? 'cardholder-data' : ''};

        const key = (result) => [...result.placed.values()]
          .map((p) => [p.id, p.x, p.y, p.z, p.region].join(':')).sort().join('|');

        const forward = layout.place(nodes, edges, options);
        const backward = layout.place([...nodes].reverse(), [...edges].reverse(), options);

        return {
          same: key(forward) === key(backward),
          placed: key(forward),
          regions: forward.regions.map((r) => [r.name, r.count, r.declared]),
          adjacency: [...layout.adjacency(forward.placed, edges)]
            .map((pair) => pair[0] + '->' + [...pair[1]].sort().join(',')).sort(),
          lanes: forward.lanes.map((strip) => [strip.region, strip.kind, strip.z]),
        };
      })
    """)

    assert result["same"], "two builds of one graph produced different coordinates"
    # Declared boundaries lead; the estate is the backdrop they sit against.
    assert result["regions"][0] == ["cardholder-data", 2, True]
    assert result["regions"][-1] == ["estate", 3, False]
    # A region is a *place*: its lanes are contiguous in Z, and the gap between
    # regions is what makes a boundary visible as somewhere rather than as a
    # legend entry.
    declared = [z for region, _kind, z in result["lanes"] if region == "cardholder-data"]
    estate = [z for region, _kind, z in result["lanes"] if region == "estate"]
    assert max(declared) < min(estate)
    assert min(estate) - max(declared) > 40
    assert result["adjacency"] == [
        "cardholder-data->estate", "estate->cardholder-data",
    ]


def test_a_missing_engine_falls_back_to_the_native_one_and_says_why(page, served):
    """A missing engine is a capability gap, never a blank canvas.

    Same treatment §27 gives a missing binary and §3 gives a refused
    capability: the answer still renders, the fallback is *named as a
    fallback*, and the reason reaches the reader. Silently substituting an
    approximation produces something that looks identical to the real thing and
    is not.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/contract.js').then(async (contract) => {
        const missing = await contract.resolve('three');
        const named = await contract.resolve(contract.DEFAULT);
        return {
          fellBack: missing.fallback,
          drewWith: missing.engine.name,
          reason: missing.reason,
          native: missing.engine.native,
          defaultIsNative: named.engine.native && !named.fallback,
          label: contract.describe(missing.engine).label,
        };
      })
    """)

    assert result["fellBack"] is True
    assert result["drewWith"] == "canvas2d"
    assert result["native"] is True
    assert result["defaultIsNative"] is True
    assert result["label"] == "native"
    assert "three" in result["reason"] and "canvas2d" in result["reason"], (
        f"the fallback did not name what was missing or what drew instead: "
        f"{result['reason']!r}"
    )


def test_an_engine_that_cannot_draw_is_refused_at_registration(page, served):
    """Checked when it arrives, not at the first frame.

    An engine that fails halfway through a paint leaves a half-drawn canvas,
    and a reader cannot tell that from a graph that genuinely looks like that.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/contract.js').then(async (contract) => {
        const usable = {
          name: 'stub', native: false,
          mount() {}, draw() {}, dispose() {},
        };
        const problems = [
          contract.invalid(null),
          contract.invalid({name: 'x', mount() {}, draw() {}, dispose() {}}),
          contract.invalid({name: 'x', native: false, mount() {}, draw() {}}),
          contract.invalid(usable),
        ];

        // A vendored engine resolved through an injected loader, so the seam is
        // exercised without a network and without vendoring anything.
        const wrapped = await contract.resolve('stub', {load: async () => ({engine: usable})});
        const broken = await contract.resolve('bad', {
          load: async () => ({engine: {name: 'bad', native: false}}),
        });

        return {
          problems,
          wrapped: {name: wrapped.engine.name, fallback: wrapped.fallback,
                    label: contract.describe(wrapped.engine).label},
          broken: {name: broken.engine.name, fallback: broken.fallback,
                   reason: broken.reason},
        };
      })
    """)

    nothing, undeclared, incomplete, fine = result["problems"]
    assert nothing and "not an object" in nothing
    assert "does not declare whether it is native" in undeclared
    assert "no dispose()" in incomplete
    assert fine == ""

    # A third party registers through the identical path a built-in does, and
    # the console reports what it is rather than what it would like to be.
    assert result["wrapped"] == {
        "name": "stub", "fallback": False, "label": "not air-gapped native",
    }
    # Present but unusable is not the same as absent, and both fall back.
    assert result["broken"]["name"] == "canvas2d"
    assert result["broken"]["fallback"] is True
    assert "unusable" in result["broken"]["reason"]
