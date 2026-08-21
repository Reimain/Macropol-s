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


def _vendored(name="three"):
    """Skip when a vendored engine is not in this checkout, and say why.

    The seam's headline gate deletes `engine/vendor/` and runs this whole tier.
    Tests *about* a vendored engine cannot pass then and must not fail then
    either — the gate is about the console surviving, not about tests for a
    deleted thing still running. So they skip, loudly.

    Guarding the guard: the **default** suite asserts the vendored files are
    present and match their digests (`tools/vendor.py --check`), so this can
    never turn into a permanent silent skip. A missing `vendor/` is a failure
    there and a skip here, which is exactly the split that makes both honest.
    """
    from slpie.ui import APP_ROOT

    if not (APP_ROOT / "engine" / "vendor" / f"{name}.js").is_file():
        pytest.skip(
            f"{name} is not vendored in this checkout — this is the seam's "
            f"deleted-directory gate, and the console is what it tests",
        )


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

    The handedness is the specific thing this catches, and it caught a real
    one. `forward x up` and `up x forward` are both "the right vector" in some
    convention, and picking the wrong one mirrors the entire scene horizontally
    — a bug that renders perfectly and is invisible unless you know which node
    belongs on the left.

    The counter-intuitive part, pinned here on purpose: **looking along +Z,
    world +X lands on the LEFT.** You have turned to face the opposite way from
    the one the axes were drawn for. An earlier revision "fixed" that surprise
    by swapping the operands, which matched the intuition and disagreed with
    every other renderer; `test_both_engines_put_a_point_in_the_same_place`
    found it 252 pixels out.
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
    # Right-handed, and this camera faces +Z: world +X is therefore on the left.
    assert result["right"]["x"] == 100
    assert result["left"]["x"] == 700
    # +Y is up, even though screen Y grows downward — the projection flips it
    # exactly once so no caller has to remember.
    assert result["above"]["y"] == 0
    # Twice the distance, half the offset. That is the perspective divide doing
    # its job, and it is the assertion an orthographic mistake fails.
    assert result["twiceAsFar"]["x"] == 250
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
        // A name nothing will ever vendor. Naming a real engine here worked
        // only while `vendor/` was empty, and went green-then-red the moment
        // one landed — an absence test has to ask for something absent.
        const missing = await contract.resolve('unobtainium');
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
    assert "unobtainium" in result["reason"] and "canvas2d" in result["reason"], (
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


def test_no_two_adjacent_regions_share_a_hue(page, served):
    """Welsh-Powell over region adjacency, checked as the property it is.

    The assertion is on `clashes()` rather than on the algorithm's internals: a
    colouring is correct exactly when no two touching regions wear the same
    hue, and how it got there is not the promise.
    """
    _open(page, served)

    result = page.evaluate("""
      Promise.all([import('/engine/palette.js'), import('/engine/layout.js')])
        .then(([palette, layout]) => {
          // A wheel: one hub touching five spokes, and the spokes touching each
          // other around the rim. Six regions, plenty of adjacency, and a naive
          // first-fit that ignored the ordering would get it wrong.
          const names = ['hub', 'a', 'b', 'c', 'd', 'e'];
          const rim = ['a', 'b', 'c', 'd', 'e'];
          const adjacency = new Map(names.map((n) => [n, new Set()]));
          const join = (l, r) => { adjacency.get(l).add(r); adjacency.get(r).add(l); };
          rim.forEach((spoke, index) => {
            join('hub', spoke);
            join(spoke, rim[(index + 1) % rim.length]);
          });

          const first = palette.colour(names, adjacency);
          const again = palette.colour([...names].reverse(), adjacency);
          const key = (r) => [...r.assigned].sort().map((p) => p.join('=')).join('|');

          return {
            clashes: palette.clashes(first.assigned, adjacency),
            overflow: first.overflow,
            shortfall: first.shortfall,
            used: first.used,
            deterministic: key(first) === key(again),
            coloured: [...first.assigned].length,
          };
        })
    """)

    assert result["clashes"] == []
    assert result["overflow"] == []
    assert result["shortfall"] == ""
    assert result["coloured"] == 6
    assert result["deterministic"], "two runs coloured the same map differently"
    # An odd wheel needs four; anything above that would mean the ordering step
    # is not doing its job.
    assert result["used"] <= 4


def test_the_estate_is_left_out_of_the_colouring(page, served):
    _open(page, served)

    result = page.evaluate("""
      import('/engine/palette.js').then((palette) => {
        const adjacency = new Map([
          ['cardholder-data', new Set(['estate'])],
          ['estate', new Set(['cardholder-data'])],
        ]);
        const answer = palette.colour(['cardholder-data', 'estate'], adjacency);
        return {
          assigned: [...answer.assigned],
          estate: palette.tokenFor('estate', answer.assigned),
          unknown: palette.tokenFor('never-declared', answer.assigned),
        };
      })
    """)

    assert [name for name, _hue in result["assigned"]] == ["cardholder-data"]
    assert result["estate"] == "--flight-estate"
    # An uncoloured region falls to the neutral, which is the honest answer —
    # not to hue 1, which would claim a distinction that was never made.
    assert result["unknown"] == "--flight-estate"


def test_running_out_of_hues_is_counted_and_reported_not_wrapped(page, served):
    """The bug this replaced, reproduced against the fix.

    `index % HUES.length` put one hue on two *adjacent* regions and said
    nothing: the picture was wrong and looked fine, which is the worst failure
    mode a visualisation has. Now the palette runs out honestly — the regions it
    could not reach are named, and they wear the neutral rather than a colour
    that means something else.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/palette.js').then((palette) => {
        // Eleven mutually adjacent regions against a ten-hue palette. Possible
        // because a dependency graph is not planar, so the four-colour theorem
        // does not bound it.
        const names = Array.from({length: 11}, (_, i) => 'r' + i);
        const adjacency = new Map(names.map(
          (n) => [n, new Set(names.filter((other) => other !== n))],
        ));
        const answer = palette.colour(names, adjacency);
        return {
          clashes: palette.clashes(answer.assigned, adjacency),
          overflow: answer.overflow,
          shortfall: answer.shortfall,
          fallsBackTo: palette.tokenFor(answer.overflow[0], answer.assigned),
        };
      })
    """)

    # The uncoloured region is left uncoloured. What must never happen is a
    # clash: two regions that touch wearing the same hue.
    assert result["clashes"] == []
    assert len(result["overflow"]) == 1
    assert "10 hues were not enough" in result["shortfall"]
    assert result["overflow"][0] in result["shortfall"]
    assert result["fallsBackTo"] == "--flight-estate"


def test_shape_says_kind_and_falls_back_to_a_dot_when_it_is_too_small(page, served):
    """Three channels, three meanings: hue is region, saturation is severity,
    shape is kind. Below a few pixels a glyph is a dot with extra vertices, and
    drawing one is the correct answer for something too far away to identify."""
    _open(page, served)

    result = page.evaluate("""
      import('/engine/glyph.js').then((glyph) => ({
        families: [
          glyph.familyOf('package'), glyph.familyOf('service'),
          glyph.familyOf('table'), glyph.familyOf('deployment'),
          glyph.familyOf('team'), glyph.familyOf('nonsense'),
        ],
        shapes: [
          glyph.shapeOf('package'), glyph.shapeOf('service'),
          glyph.shapeOf('table'), glyph.shapeOf('deployment'),
          glyph.shapeOf('team'),
        ],
        corners: {
          service: glyph.outline('service', 10).points.length,
          package: glyph.outline('package', 10).points.length,
          team: glyph.outline('team', 10).points.length,
        },
        tiny: glyph.outline('service', glyph.FLOOR - 0.1).shape,
        big: glyph.outline('service', glyph.FLOOR + 0.1).shape,
        unknown: glyph.outline('nonsense', 10).shape,
        severity: [
          glyph.severityToken('critical'), glyph.severityToken('high'),
          glyph.severityToken('medium'), glyph.severityToken('low'),
          glyph.severityToken(undefined),
        ],
      }))
    """)

    assert result["families"] == [
        "code", "runtime", "data", "delivery", "organisation", "unknown",
    ]
    # Five distinguishable silhouettes for five families, told apart by corner
    # count rather than by proportion — a square and a wide rectangle are the
    # same shape at ten pixels.
    assert len(set(result["shapes"])) == 5
    assert result["corners"] == {"service": 3, "package": 4, "team": 6}
    assert result["tiny"] == "dot"
    assert result["big"] == "triangle"
    # A kind nobody mapped draws a circle, which is this canvas saying it does
    # not know what that is rather than guessing a family.
    assert result["unknown"] == "circle"
    assert result["severity"] == ["--crit", "--bad", "--warn", "--ok", ""]


def test_a_lane_too_small_on_screen_becomes_one_mark_that_carries_its_count(page, served):
    """Maps draws cities at country zoom, not buildings.

    Not because buildings are slow — because at that distance a building is not
    the unit of anything. Forty overlapping circles carry nothing; one mark
    saying *forty* carries the fact.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/aggregate.js').then((aggregate) => {
        // Forty nodes of one kind, one region, all inside eight pixels.
        const tight = Array.from({length: 40}, (_, i) => ({
          id: 'tight' + i, x: 100 + (i % 8), y: 100 + (i % 5),
          depth: 50 + i, radius: 3, region: 'estate', kind: 'package', severity: '',
        }));
        // And one kind spread far enough that its members are still worth drawing.
        const spread = Array.from({length: 5}, (_, i) => ({
          id: 'spread' + i, x: 400 + i * 60, y: 300, depth: 40,
          radius: 4, region: 'estate', kind: 'service', severity: '',
        }));

        const field = aggregate.aggregate([...tight, ...spread]);
        const lane = field.marks.find((m) => m.tier === 'lane');
        return {
          marks: field.marks.length,
          represented: field.represented,
          tiers: field.tiers,
          laneCount: lane ? lane.count : 0,
          laneKind: lane ? lane.kind : '',
          everyMarkCounts: field.marks.every((m) => m.count >= 1),
          total: field.marks.reduce((sum, m) => sum + m.count, 0),
          assigned: field.assignment.size,
        };
      })
    """)

    assert result["represented"] == 45
    # Nothing is dropped: the marks account for every node that was projected.
    assert result["total"] == 45
    assert result["assigned"] == 45
    assert result["laneCount"] == 40
    assert result["laneKind"] == "package"
    assert result["tiers"]["lane"] == 1
    assert result["marks"] < 45, "aggregation drew a mark per node"


def test_marks_that_still_overlap_merge_into_a_coarser_tier(page, served):
    """The problem the prototype exposed and had no answer for.

    Two small lanes at the same distance land on top of each other. Piled up
    they look exactly like one dark mark and are not one, and nothing on screen
    says so. The second pass merges them and the merged mark carries the sum.
    """
    _open(page, served)

    result = page.evaluate("""
      import('/engine/aggregate.js').then((aggregate) => {
        const at = (id, x, y, kind, region, severity) => ({
          id, x, y, depth: 50, radius: 3, kind, region, severity: severity || '',
        });
        // Two different lanes, three pixels apart. Each is one mark after pass
        // one; both fall in one cell in pass two.
        const points = [
          at('a1', 200, 200, 'package', 'estate'),
          at('a2', 202, 201, 'package', 'estate'),
          at('b1', 203, 202, 'service', 'cardholder-data'),
          at('b2', 201, 203, 'service', 'cardholder-data'),
          at('far', 600, 400, 'table', 'estate'),
        ];

        const field = aggregate.aggregate(points);
        const cluster = field.marks.find((m) => m.tier === 'cluster');
        return {
          marks: field.marks.length,
          tiers: field.tiers,
          clusterCount: cluster ? cluster.count : 0,
          clusterRegion: cluster ? cluster.region : 'absent',
          clusterKind: cluster ? cluster.kind : 'absent',
          sameMark: field.assignment.get('a1') === field.assignment.get('b1'),
          total: field.marks.reduce((sum, m) => sum + m.count, 0),
        };
      })
    """)

    assert result["tiers"]["cluster"] == 1
    assert result["clusterCount"] == 4
    assert result["sameMark"] is True
    assert result["total"] == 5, "the merge lost or duplicated a node"
    # An aggregate spanning two regions has no region and must not borrow one.
    assert result["clusterRegion"] == ""
    assert result["clusterKind"] == ""


def test_a_cluster_can_never_swallow_a_finding(page, served):
    """Severity is the one attribute that propagates upward, as the worst
    contained. Reserving saturation for severity is worthless if the mark that
    absorbed a critical draws as though nothing were wrong."""
    _open(page, served)

    result = page.evaluate("""
      import('/engine/aggregate.js').then((aggregate) => {
        const points = [
          {id: 'q', x: 100, y: 100, depth: 9, radius: 3, kind: 'package',
           region: 'estate', severity: ''},
          {id: 'l', x: 101, y: 100, depth: 8, radius: 3, kind: 'package',
           region: 'estate', severity: 'low'},
          {id: 'c', x: 102, y: 101, depth: 7, radius: 3, kind: 'package',
           region: 'estate', severity: 'critical'},
          {id: 'm', x: 103, y: 101, depth: 6, radius: 3, kind: 'package',
           region: 'estate', severity: 'medium'},
        ];
        const field = aggregate.aggregate(points);
        return {
          marks: field.marks.length,
          severity: field.marks[0].severity,
          // The nearest member's depth, so a cluster cannot sink behind
          // something it visibly overlaps.
          depth: field.marks[0].depth,
        };
      })
    """)

    assert result["marks"] == 1
    assert result["severity"] == "critical"
    assert result["depth"] == 6


def test_an_edge_inside_one_mark_is_not_drawn_and_parallels_collapse(page, served):
    """At twenty thousand nodes most edges are internal, which is where the edge
    pass stops being the expensive half. Forty overlapping lines and one line
    look identical; only the second is honest about being one."""
    _open(page, served)

    result = page.evaluate("""
      import('/engine/aggregate.js').then((aggregate) => {
        const assignment = new Map([
          ['a', 0], ['b', 0], ['c', 1], ['d', 1], ['e', 2],
        ]);
        const drawn = aggregate.bundle([
          {src: 'a', dst: 'b'},            // inside mark 0
          {src: 'a', dst: 'c'},            // 0 -> 1
          {src: 'b', dst: 'd'},            // 0 -> 1 again
          {src: 'c', dst: 'a'},            // 1 -> 0, the same pair reversed
          {src: 'e', dst: 'c'},            // 2 -> 1
          {src: 'e', dst: 'missing'},      // an endpoint that was clipped
        ], assignment);
        return {
          internal: drawn.internal,
          edges: drawn.edges.map((e) => [e.from, e.to, e.count]),
        };
      })
    """)

    assert result["internal"] == 1
    # Direction does not make a second edge between the same two marks.
    assert sorted(result["edges"]) == [[0, 1, 3], [1, 2, 1]]


def test_aggregation_is_deterministic(page, served):
    """Two runs over one scene produce one picture, as the layout and the
    colouring already do. Without it the marks reshuffle between frames and the
    canvas crawls while nothing is moving."""
    _open(page, served)

    result = page.evaluate("""
      import('/engine/aggregate.js').then((aggregate) => {
        // Dense enough that both passes actually bite: two hundred points in a
        // 200x150 box, against 14-pixel cells.
        const points = Array.from({length: 200}, (_, i) => ({
          id: 'n' + i,
          x: (i * 37) % 200, y: (i * 53) % 150,
          depth: 20 + (i % 17), radius: 3,
          kind: ['package', 'service', 'table'][i % 3],
          region: i % 4 ? 'estate' : 'cardholder-data',
          severity: i % 25 ? '' : 'high',
        }));
        const key = (field) => field.marks
          .map((m) => [m.tier, Math.round(m.x), Math.round(m.y), m.count].join(':'))
          .join('|');
        return {
          same: key(aggregate.aggregate(points))
             === key(aggregate.aggregate([...points].reverse())),
          marks: aggregate.aggregate(points).marks.length,
        };
      })
    """)

    assert result["same"], "two runs over one scene produced different marks"
    assert result["marks"] < 200


# --- the vendored engine -------------------------------------------------------


def test_three_loads_from_the_seam_and_declares_what_it_is(page, served):
    """A third party registering through the identical path a built-in uses.

    That is invariant 6's argument applied to rendering, and it is the whole
    reason the seam was built before anything was vendored: the plug is proven
    by its own use rather than asserted.
    """
    _vendored()
    _open(page, served)

    result = page.evaluate("""
      import('/engine/contract.js').then(async (contract) => {
        const chosen = await contract.resolve('three');
        return {
          name: chosen.engine.name,
          native: chosen.engine.native,
          fallback: chosen.fallback,
          reason: chosen.reason,
          label: contract.describe(chosen.engine).label,
          listed: contract.engines().map((e) => e.name).sort(),
        };
      })
    """)

    if result["fallback"]:
        # A machine without WebGL is a legitimate answer, and the point is that
        # it *says so* rather than drawing nothing. Assert the honesty, then
        # stop — the rest of this test needs a GPU.
        assert "cannot run here" in result["reason"] or "unusable" in result["reason"]
        pytest.skip(f"no WebGL in this browser: {result['reason']}")

    assert result["name"] == "three"
    assert result["native"] is False
    assert result["label"] == "not air-gapped native"
    assert result["listed"] == ["canvas2d", "three"]


def test_both_engines_put_a_point_in_the_same_place(page, served):
    """Different renderers must not be different answers.

    §24's seventh acceptance — one composition, several surfaces, one answer —
    applied to rendering. Three's camera looks down its own local -Z and ours is
    a hand-rolled basis; if the two disagree on handedness the whole scene
    mirrors, renders perfectly, and is a different picture of the same query.

    So the projection is compared directly: our `project()` against Three's own
    `Vector3.project()` through a camera built from the same numbers.
    """
    _vendored()
    _open(page, served)

    result = page.evaluate("""
      Promise.all([
        import('/engine/camera.js'),
        import('/engine/vendor/three.module.min.js'),
      ]).then(([ours, THREE]) => {
        const width = 800;
        const height = 600;
        const fov = Math.PI / 3;
        const eye = ours.vector(120, 90, 300);
        const target = ours.vector(0, 0, 0);

        const mine = ours.look(eye, target, {width, height, fov, near: 0.5});

        const theirs = new THREE.PerspectiveCamera(
          (fov * 180) / Math.PI, width / height, 0.5, 4000,
        );
        theirs.position.set(eye.x, eye.y, eye.z);
        theirs.up.set(0, 1, 0);
        theirs.lookAt(target.x, target.y, target.z);
        theirs.updateProjectionMatrix();
        theirs.updateMatrixWorld(true);

        const samples = [[0, 0, 0], [80, 0, 0], [-80, 0, 0], [0, 60, 0], [0, 0, -140]];
        return samples.map((point) => {
          const A = ours.project(ours.vector(...point), mine);
          const ndc = new THREE.Vector3(...point).project(theirs);
          return {
            point,
            ours: [Math.round(A.x), Math.round(A.y)],
            theirs: [
              Math.round((ndc.x + 1) / 2 * width),
              Math.round((1 - ndc.y) / 2 * height),
            ],
          };
        });
      })
    """)

    for sample in result:
        assert abs(sample["ours"][0] - sample["theirs"][0]) <= 1, (
            f"the two engines disagree on x for {sample['point']}: {sample}"
        )
        assert abs(sample["ours"][1] - sample["theirs"][1]) <= 1, (
            f"the two engines disagree on y for {sample['point']}: {sample}"
        )


def test_the_vendored_engine_draws_the_same_marks_as_the_native_one(page, served):
    """The seam's central promise, checked rather than asserted.

    WebGL could happily push twenty thousand instances, and drawing them would
    make this engine show a *different picture of the same query* — which is a
    worse failure than being slow. So the vendored engine runs the identical
    `aggregate()` over the identical projection, and the two tallies must match
    on everything that is about the answer: how many marks, what they stand
    for, how many carry a finding, how they tier.
    """
    _vendored()
    _open(page, served)

    result = page.evaluate("""
      (async () => {
        const [contract, layout, palette] = await Promise.all([
          import('/engine/contract.js'),
          import('/engine/layout.js'),
          import('/engine/palette.js'),
        ]);
        const camera = await import('/engine/camera.js');

        const kinds = ['package', 'service', 'table', 'deployment', 'team'];
        const nodes = Array.from({length: 900}, (_, i) => ({
          id: 'n' + i, name: 'node-' + i, kind: kinds[i % kinds.length],
          severity: i % 90 === 0 ? 'critical' : '',
        }));
        const edges = Array.from({length: 1400}, (_, i) => ({
          src: 'n' + (i % 900), dst: 'n' + ((i * 7 + 13) % 900),
        }));
        const inside = (node) => Number(node.id.slice(1)) % 5 === 0
          ? 'cardholder-data' : '';

        const scene = layout.place(nodes, edges, {regionOf: inside});
        for (const [id, point] of scene.placed) {
          point.severity = nodes[Number(id.slice(1))].severity;
        }
        scene.colouring = palette.colour(
          scene.regions.map((r) => r.name),
          layout.adjacency(scene.placed, edges),
        );

        const view = camera.frame(scene.extent, {width: 900, height: 620});

        const run = async (name) => {
          const holder = document.createElement('canvas');
          holder.width = 900; holder.height = 620;
          document.body.appendChild(holder);
          const chosen = await contract.resolve(name);
          if (chosen.fallback) return {fallback: true, reason: chosen.reason};
          const drawing = Object.create(chosen.engine);
          drawing.mount(holder, scene);
          const first = drawing.draw(view);
          const started = performance.now();
          let frames = 0;
          while (performance.now() - started < 260) { drawing.draw(view); frames += 1; }
          const fps = frames / ((performance.now() - started) / 1000);
          drawing.dispose();
          holder.remove();
          return {
            fallback: false, name: chosen.engine.name, fps: Math.round(fps),
            marks: first.marks, represented: first.represented,
            severe: first.severe, edges: first.edges, tiers: first.tiers,
          };
        };

        return {native: await run('canvas2d'), vendored: await run('three'),
                nodes: nodes.length};
      })()
    """)

    native = result["native"]
    vendored = result["vendored"]
    assert not native["fallback"], native
    if vendored["fallback"]:
        pytest.skip(f"no WebGL in this browser: {vendored['reason']}")

    # Same answer. Not "similar" — the same numbers, because both engines ran
    # the same aggregation over the same projection.
    for field in ("marks", "represented", "severe", "edges", "tiers"):
        assert native[field] == vendored[field], (
            f"the two engines disagree on {field}: "
            f"canvas2d={native[field]} three={vendored[field]}"
        )
    assert native["represented"] == result["nodes"]
    # And the aggregation actually bit, or this proves two engines agree about
    # drawing everything, which is not the property under test.
    assert native["marks"] < result["nodes"]

    print(
        f"\\n  {result['nodes']} nodes, {native['marks']} marks:"
        f"  canvas2d {native['fps']}fps   three {vendored['fps']}fps",
    )


# --- what this shell cannot draw, and what it says about it --------------------


def test_a_screen_this_shell_cannot_draw_says_which_capability_it_needs(page, served):
    """The honest half of the block-manifest model.

    Screens-as-data has a real ceiling — direct manipulation, arranged panes and
    a scrubbable axis are code, not a declaration — and the failure mode is a
    console that quietly omits the screens above that line. A screen the
    interface silently left out would be a capability the platform has and one
    surface cannot reach, which is the drift §24 exists to prevent; hidden by
    the interface rather than by the registry, but hidden all the same.

    So it is *reported*, with the missing capability named, and rendered as a
    refusal — accent, never the danger colour, because needing a different
    shell is a policy fact and not a fault.
    """
    _open(page, served, "#/flight")
    page.wait_for_timeout(300)

    body = page.inner_text("#outlet")
    assert "Flight" in body
    for capability in ("split-pane", "timeline", "drag"):
        assert capability in body, f"the refusal does not name {capability}"

    kind = page.eval_on_selector_all(
        "#outlet .refusal, #outlet .fault", "els => els.map(e => e.className)",
    )
    assert kind and all("refusal" in name for name in kind), (
        f"a shell mismatch rendered as a fault rather than a refusal: {kind}"
    )
    assert not _faults(page)


def test_the_rail_does_not_offer_what_this_shell_cannot_draw(page, served):
    """Hiding is a convenience, never the control.

    The same rule the RBAC-filtered nav follows: the screen stays in the
    manifest, stays reachable by its hash, and answers with a reason. What the
    rail does is decline to advertise a door that opens onto an explanation.
    """
    _open(page, served)

    listed = page.eval_on_selector_all(
        ".rail nav a", "els => els.map(e => e.textContent.trim())",
    )
    assert listed, "the rail rendered nothing"
    assert "Flight" not in listed

    shells = page.evaluate("""
      import('/data/client.js').then((client) => ({
        shell: client.SHELL,
        flight: client.missingFor(
          client.SCREENS.find((s) => s.key === 'flight'), client.SHELL),
        console: client.missingFor(
          client.SCREENS.find((s) => s.key === 'console'), client.SHELL),
        shells: client.SHELLS.map((s) => s.name),
      }))
    """)
    assert shells["shell"] == "stdlib"
    assert sorted(shells["flight"]) == ["drag", "split-pane", "timeline"]
    assert shells["console"] == []
    assert shells["shells"] == ["stdlib", "web"]
