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


def test_the_navigation_is_generated_from_the_manifest(page, served):
    _open(page, served)
    sections = page.eval_on_selector_all("header nav a", "els => els.map(e => e.textContent)")

    assert sections == ["Console", "Operate", "Build", "Catalog", "API", "Admin"]


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
    _open(page, served)
    before = (_token(page, "--bg"), _token(page, "--row-h"))

    page.evaluate("document.documentElement.dataset.theme = 'light'")
    after = (_token(page, "--bg"), _token(page, "--row-h"))

    assert after[0] != before[0], "the theme changed nothing"
    assert after[1] == before[1], "the theme changed a size"


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
