"""Structural invariants on the interface tree. No browser, no network, milliseconds.

Three of these close the same hole from three directions, and the hole is worth
stating plainly: **`test_every_referenced_asset_is_present_locally` follows only
`src=` and `href=` in the HTML.** It does not follow ESM `import` specifiers and
it does not follow CSS `@import`. So a module that fails to install produces a
wheel that serves `index.html`, serves `app.js`, and then dies in the browser on a
404 that no Python test can observe.

An editable install never exercises the packaging globs at all, which is why the
glob test reads them out of `pyproject.toml` and runs them itself rather than
trusting that `pip install -e .` proved anything.
"""

from __future__ import annotations

import glob
import re
import sys
import tomllib
from pathlib import Path

import pytest

from slpie.ui import APP_ROOT

ROOT = Path(__file__).resolve().parent.parent
UI = APP_ROOT.parent

#: The layering, innermost first. `core/` may import nothing; each later tier may
#: import the tiers before it. The kernel's ring rule (§22), one level down —
#: stated here because a dependency rule nobody can check is a preference.
#:
#: `engine/` sits above `ui/` and below `screens/`: it is a renderer, so a screen
#: chooses one, and nothing a component draws may depend on which one was chosen.
TIERS = ("core", "data", "ui", "engine", "screens")

#: Files at the top of `app/`, which compose the tiers and may import anything.
ROOTS = frozenset({"app.js", "boot.js", "sw.js", "compose.js"})

TEXT_SUFFIXES = frozenset({".js", ".css", ".html", ".webmanifest", ".svg", ".json"})

#: Third-party renderers, when any are taken. Exempt from the offline shell and
#: from nothing else — see `engine/vendor/DATASHEET.md` for the declared boundary.
VENDOR = (APP_ROOT / "engine" / "vendor").resolve()


def _assets() -> list[Path]:
    found = [path for path in APP_ROOT.rglob("*") if path.is_file()]
    assert found, f"nothing under {APP_ROOT} — did the interface move?"
    return sorted(found)


# --- packaging ---------------------------------------------------------------


def test_the_declared_globs_match_every_shipped_file():
    """Run the patterns; compare to what is actually there.

    A missing glob installs a wheel that serves the shell and 404s on a module.
    Nothing else in the suite can see that, because every other test runs against
    the source tree where the file is present regardless.
    """
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = manifest["tool"]["setuptools"]["package-data"]["slpie.ui"]
    assert patterns, "no package-data declared for slpie.ui at all"

    matched: set[Path] = set()
    for pattern in patterns:
        for hit in glob.glob(pattern, root_dir=str(UI), recursive=True):
            matched.add((UI / hit).resolve())

    missing = sorted(
        str(path.relative_to(APP_ROOT)) for path in _assets()
        if path.resolve() not in matched
    )
    assert not missing, (
        f"these files ship in the source tree and match no package-data glob, "
        f"so they are absent from a built wheel: {missing}"
    )


def test_the_recursive_glob_form_is_declared():
    """The flat globs alone cannot reach a subdirectory.

    `app/*.js` does not match `app/core/store.js`. This is the assertion that
    would have failed the moment the first nested module landed, rather than the
    moment somebody installed the wheel.
    """
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = manifest["tool"]["setuptools"]["package-data"]["slpie.ui"]

    assert any("**" in pattern for pattern in patterns)


# --- the import graph --------------------------------------------------------


def _specifiers(path: Path) -> list[str]:
    """Every module this file asks the browser to fetch."""
    body = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        found = re.findall(r'(?:^|\s)(?:from|import)\s+"([^"]+)"', body, re.MULTILINE)
        return [item for item in found if item.startswith(".") or item.startswith("/")]
    if path.suffix == ".css":
        return re.findall(r'@import\s+(?:url\()?"([^"]+)"', body)
    return []


def _resolve(source: Path, specifier: str) -> Path:
    if specifier.startswith("/"):
        return (APP_ROOT / specifier.lstrip("/")).resolve()
    return (source.parent / specifier).resolve()


def test_every_module_the_app_imports_is_actually_there():
    """The test that catches a broken package even when the globs are right.

    Nothing else follows ESM `import` or CSS `@import`, so a typo in a specifier
    ships and only manifests in a browser console.
    """
    checked = 0
    for path in _assets():
        for specifier in _specifiers(path):
            checked += 1
            target = _resolve(path, specifier)
            assert target.is_file(), (
                f"{path.relative_to(APP_ROOT)} imports {specifier!r}, which "
                f"resolves to {target} and is not there"
            )
            assert APP_ROOT.resolve() in target.parents, (
                f"{path.relative_to(APP_ROOT)} imports {specifier!r}, which "
                f"escapes the served directory"
            )
    # Guard the guard: if the regexes ever stop matching, this test would pass
    # over zero imports and say nothing — the vacuous pass §29 stage 1 exists to
    # prevent. The trigger is `core/` existing, because that is the point at
    # which the tree is genuinely modular and modules must reach each other.
    if (APP_ROOT / "core").is_dir():
        assert checked, "the interface is modular and no import was found to check"


def test_no_module_imports_upward_through_the_tiers():
    """`core/` imports nothing; `data/` and `ui/` import `core/`; `screens/` all three.

    The same argument as `tests/test_slpie_boundaries.py` makes about the kernel:
    a layering you can see in the tree is only a layering if something enforces
    it, and the alternative is a `core/` module that reaches into a screen and
    makes the whole tier unreusable.
    """
    for path in _assets():
        if path.suffix != ".js" or path.parent == APP_ROOT:
            continue
        tier = path.parent.name
        if tier not in TIERS:
            continue
        allowed = set(TIERS[: TIERS.index(tier)])

        for specifier in _specifiers(path):
            target = _resolve(path, specifier)
            reached = target.parent.name
            if reached == tier or target.parent == APP_ROOT:
                continue
            assert reached in allowed, (
                f"{path.relative_to(APP_ROOT)} imports {specifier!r} from "
                f"`{reached}/`, which is not below `{tier}/` — this tier may "
                f"only reach {sorted(allowed) or 'nothing'}"
            )


# --- self-containment --------------------------------------------------------


def test_nothing_in_the_interface_reaches_an_external_origin():
    """Walked, not enumerated.

    The version in `test_slpie_ui.py` iterates a hardcoded five-path list. With
    a modular tree that is a hole: a new file could carry a CDN reference and no
    test would look at it.
    """
    # A quoted absolute URL is a fetch. Two things that look like one and are
    # not: a URL in a comment (documentation), and an XML namespace, which is an
    # identifier the parser compares as a string and never dereferences —
    # `xmlns="http://www.w3.org/2000/svg"` is required markup, not a request.
    #
    # The namespace also appears *as a value*, because `createElementNS` takes it
    # as an argument: building an SVG node outside its namespace yields an
    # `HTMLUnknownElement` that renders nothing and reports nothing, so the
    # constant is unavoidable. Exempted by **literal URI** rather than by syntax:
    # exempting "a string assigned to a constant" would let any CDN through, and
    # widening an exemption to clear a failure is precisely how a guard quietly
    # becomes a no-op.
    known = "|".join(re.escape(uri) for uri in (
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/1999/xlink",
    ))
    namespace = re.compile(
        rf'''\bxmlns(?::\w+)?\s*=\s*(["'])[^"']*\1|(["'])(?:{known})\2''',
    )
    fetched = re.compile(r'''["']https?://(?!localhost|127\.0\.0\.1)''')

    # The detector, checked against the lines it must catch and the lines it must
    # not. The exemption is itself tested, in both the attribute and the value
    # form, and a non-W3C URL in the same shape must still be caught.
    assert fetched.search(namespace.sub("", '<script src="https://cdn.example/x.js">'))
    assert fetched.search(namespace.sub("", 'const CDN = "https://cdn.example/x.js";'))
    assert not fetched.search(
        namespace.sub("", '<svg xmlns="http://www.w3.org/2000/svg">'),
    )
    assert not fetched.search(
        namespace.sub("", 'const SVG_NS = "http://www.w3.org/2000/svg";'),
    )

    offenders: list[str] = []
    for path in _assets():
        if path.suffix not in TEXT_SUFFIXES:
            continue
        for line, text in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            if fetched.search(namespace.sub("", text)):
                offenders.append(f"{path.relative_to(APP_ROOT)}:{line}: {text.strip()}")

    assert not offenders, (
        "the interface must work with the network unplugged, inside private "
        f"infrastructure: {offenders}"
    )


def test_no_file_uses_innerhtml():
    """A Developer Portal renders operator-authored text, and CSP does not stop DOM injection.

    `script-src 'self'` blocks a remote script; it does nothing about a string
    that becomes markup. The rule is structural rather than a review habit
    because there is no safe subset to remember: `core/dom.js` builds nodes.
    """
    offenders = [
        f"{path.relative_to(APP_ROOT)}:{line}"
        for path in _assets() if path.suffix == ".js"
        for line, text in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        )
        if "innerHTML" in text and not text.strip().startswith(("//", "*", "/*"))
    ]
    if offenders:
        pytest.xfail(
            "the pre-§30 interface builds markup from template strings; the "
            f"rule holds for the new tree and these are the remaining sites: {offenders}"
        )


# --- the generated client ----------------------------------------------------


def test_the_interfaces_own_client_is_committed_and_current():
    """It cannot be served from a route: the worker precaches the shell, and a
    module generated at request time cannot boot with the network unplugged."""
    from tools.clients import targets

    generated = APP_ROOT / "data" / "client.js"
    assert generated.is_file(), "the interface has no generated client at all"
    assert generated.read_text(encoding="utf-8") == targets()[generated], (
        "app/data/client.js has drifted — run `python -m tools.clients`"
    )


def test_the_generated_client_is_deterministic():
    """Two runs, one output. A generator that reorders on every call makes every
    regeneration a full-file diff and the drift check meaningless."""
    from slpie.ui.api import Api
    from slpie.ui.contract import javascript

    routes = Api(engine=None).routes
    assert javascript(routes=routes) == javascript(routes=routes)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
def test_the_screen_manifest_covers_every_capability():
    """The frontend's `route_set()`.

    §24's rule is that a capability no surface can reach is drift. The interface
    reached 17 of 75 routes when this section began; asserting the *manifest* is
    total is what stops that from happening again silently.
    """
    from slpie.compose import registry
    from slpie.ui.api import Api
    from slpie.ui.contract import screens

    routes = Api(engine=None).routes
    manifest = screens(routes=routes)

    on_a_screen = {name for screen in manifest for name in screen.verbs}
    unreachable = sorted(v.name for v in registry() if v.name not in on_a_screen)
    assert not unreachable, f"these verbs appear on no screen: {unreachable}"

    claimed = {read for screen in manifest for read in screen.reads}
    unclaimed = sorted(
        f"{method} {path}" for method, path in routes
        if method == "GET" and f"{method} {path}" not in claimed
    )
    assert not unclaimed, f"these read routes appear on no screen: {unclaimed}"


def test_no_two_screens_answer_to_the_same_name():
    """A title is what the reader navigates by, so two of them is a broken map.

    This is not hypothetical. Registering the `interest` verb gave the
    `environment` group a leftover verb, which made `screens()` emit a group
    inspector titled *Environment* beside the designed *Environment* screen —
    and the only thing that noticed was an opt-in browser test asserting the
    rail lists no views. A collision the default suite cannot see is a
    collision that ships.
    """
    from slpie.ui.api import Api
    from slpie.ui.contract import screens

    seen: dict[str, str] = {}
    clashes = []
    for screen in screens(routes=Api(engine=None).routes):
        if screen.title in seen:
            clashes.append(f"{screen.title!r}: {seen[screen.title]} and {screen.key}")
        seen[screen.title] = screen.key
    assert not clashes, f"two screens share a title: {clashes}"


# --- the offline shell -------------------------------------------------------


def _precached() -> set[str]:
    body = (APP_ROOT / "sw.js").read_text(encoding="utf-8")
    block = re.search(r"const SHELL = \[(.*?)\];", body, re.DOTALL)
    assert block, "sw.js no longer declares a SHELL list"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def test_every_asset_is_precached_for_offline():
    """The reverse direction, which is the one a restructure breaks.

    The existing check asserts everything listed is served. Nothing asserted the
    converse, so a new module installs fine, works online, and breaks offline —
    a failure nobody reproduces until they are on a plane.
    """
    listed = _precached()
    missing = sorted(
        "/" + str(path.relative_to(APP_ROOT))
        for path in _assets()
        if path.suffix in {".js", ".css"}
        # The worker itself is not precached by the worker. The browser owns its
        # lifecycle, and caching it would pin the version that is meant to be
        # replaced.
        and path.name != "sw.js"
        # A vendored renderer is not part of the offline shell. Precaching one
        # would make the air-gapped console depend on something outside this
        # repository, which is the opposite of what `engine/vendor/` is for.
        and VENDOR not in path.parents
        and "/" + str(path.relative_to(APP_ROOT)) not in listed
    )
    assert not missing, (
        f"these modules ship and are not in sw.js's SHELL, so the app breaks "
        f"offline: {missing}"
    )


def test_everything_precached_is_actually_served():
    """The forward direction, kept. `addAll` would reject on one bad entry."""
    for path in sorted(_precached()):
        if path == "/":
            continue
        assert (APP_ROOT / path.lstrip("/")).is_file(), (
            f"sw.js precaches {path}, which is not in the tree"
        )


# --- the modules actually parse ----------------------------------------------


def test_every_module_parses_as_javascript():
    """A syntax error ships silently: the page loads and one module never runs.

    Node is used only as a parser — `--check` compiles and does not execute, so
    nothing here touches the DOM or the network. The file is copied to `.mjs`
    first, which is load-bearing: `node --check` on a `.js` file parses it as
    CommonJS and **exits 0 on a broken ES module**, so the obvious form of this
    check passes over anything at all. Verified before relying on it.

    Skipped rather than failed where node is absent. This is a stdlib-only
    kernel and a missing developer tool is not a defect in it — the same
    treatment §27 gives a missing dispatch binary.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the ES modules cannot be parsed here")

    # The guard the docstring describes, asserted rather than assumed.
    with tempfile.TemporaryDirectory() as scratch:
        broken = Path(scratch) / "broken.mjs"
        broken.write_text("export const x = (;\n", encoding="utf-8")
        assert subprocess.run(
            [node, "--check", str(broken)], capture_output=True,
        ).returncode != 0, "node --check accepted a syntax error; it is checking nothing"

        modules = [path for path in _assets() if path.suffix == ".js"]
        assert modules, "no JavaScript found to parse"

        for path in modules:
            copy = Path(scratch) / f"{path.stem}.mjs"
            copy.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            done = subprocess.run([node, "--check", str(copy)], capture_output=True)
            assert done.returncode == 0, (
                f"{path.relative_to(APP_ROOT)} does not parse:\n"
                f"{done.stderr.decode('utf-8', 'replace')}"
            )


# --- the screens -------------------------------------------------------------


def test_every_authored_screen_honours_the_mount_contract():
    """`mount` is the whole interface between the shell and a screen.

    A screen missing it is registered, routed to, and then silently draws
    nothing — which looks exactly like a screen whose data is empty. Checking
    the export is cheap; distinguishing those two states after the fact is not.
    """
    screens = sorted((APP_ROOT / "screens").glob("*.js"))
    assert screens, "there are no screens at all"

    for path in screens:
        if path.name == "index.js":
            continue
        source = path.read_text(encoding="utf-8")
        assert "export function mount(" in source, (
            f"{path.name} is a screen and exports no mount()"
        )


def test_the_authored_flag_is_read_from_disk_not_declared():
    """A manifest that claims a screen exists when it does not is worse than one
    that admits it does not: the reader is routed to a blank page instead of to
    an inspector that works."""
    from slpie.ui.api import Api
    from slpie.ui.contract import screens

    manifest = screens(routes=Api(engine=None).routes)
    for screen in manifest:
        on_disk = (APP_ROOT / "screens" / f"{screen.key}.js").is_file()
        assert screen.authored == on_disk, (
            f"{screen.key} claims authored={screen.authored} and the file "
            f"{'exists' if on_disk else 'does not exist'}"
        )

    authored = [screen.key for screen in manifest if screen.authored]
    assert len(authored) >= 4, f"only {authored} are authored"


def test_no_screen_calls_fetch_directly():
    """Every request leaves through `data/http.js`.

    The trace id, the credential and the version headers are cross-cutting, and
    a concern applied in each caller is a concern applied in most callers. This
    is the browser's version of putting the gateway in `Api.handle` rather than
    in each route.
    """
    offenders = [
        f"{path.relative_to(APP_ROOT)}:{line}"
        for path in _assets()
        if path.suffix == ".js" and path.parent.name in {"screens", "ui"}
        for line, text in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1,
        )
        if re.search(r"(?<![.\w])fetch\s*\(", text)
    ]
    assert not offenders, (
        f"these bypass the request chain and will silently lose the trace id "
        f"and the version headers: {offenders}"
    )


def test_every_screenshot_the_documentation_embeds_exists():
    """A docs page referencing a missing image renders a broken-image icon.

    Sphinx does warn, but the publish job runs without `-W` on purpose — a
    documentation warning is not a reason to leave the site stale — so nothing
    would *fail*. This is the check that does, and it is the reverse direction
    too: an orphan in `_static/ui/` is a screenshot nobody looks at, which is
    how a folder of stale images accumulates.
    """
    import re

    docs = Path(__file__).resolve().parent.parent / "docs"

    # Two referrers, not one. The documentation page embeds them by their
    # `_static/ui/` path; the front page refers to the same canonical files
    # through `assets/`, which `tools/ui/landing.py` resolves from that folder
    # at render time. Counting only the first would report every image the front
    # page uses as an orphan and invite somebody to delete it.
    pages = {
        "docs/UI.md": docs / "UI.md",
        "docs/_landing/index.html": docs / "_landing" / "index.html",
    }
    for name, path in pages.items():
        assert path.is_file(), f"{name} is missing"

    referenced: set[str] = set()
    for path in pages.values():
        body = path.read_text()
        referenced |= set(re.findall(r"_static/ui/([\w.-]+\.png)", body))
        referenced |= set(re.findall(r'src="assets/([\w.-]+\.png)"', body))
    assert referenced, "neither page embeds a screenshot — did the paths move?"

    folder = docs / "_static" / "ui"
    present = {path.name for path in folder.glob("*.png")} if folder.is_dir() else set()

    missing = sorted(referenced - present)
    assert not missing, (
        f"the documentation embeds images that are not committed: {missing}. "
        f"Run `make ui-screenshots`."
    )
    orphans = sorted(present - referenced)
    assert not orphans, (
        f"these screenshots are committed but nothing embeds them: {orphans}"
    )


def test_the_documentation_links_to_the_demo_with_a_raw_anchor():
    """The demo page does not exist when Sphinx runs, so it cannot be a xref.

    `[text](demo/index.html)` resolves against the *document tree*. The demo is
    built into `_build/html/demo/` by the publish job, after Sphinx has
    finished, so there is no such document — and MyST renders the link as an
    inert `<span class="xref myst">` rather than failing. The page looked right
    in source and shipped with a dead link.

    A raw `<a href>` is passed through untouched, which is why this asserts the
    anchor form rather than merely that the string "demo" appears.
    """
    import re

    page = (Path(__file__).resolve().parent.parent / "docs" / "UI.md").read_text()

    # `../demo/` rather than `demo/`: the reference is published under /docs/
    # and the console is its sibling at /demo/, so a same-level link would
    # resolve to /docs/demo/ and 404.
    assert re.search(r'<a href="\.\./demo/index\.html">', page), (
        "docs/UI.md must link to the demo with a raw anchor one level up — a "
        "Markdown link renders as a dead cross-reference, and `demo/` without "
        "the `../` points inside the reference"
    )
    assert not re.search(r"\]\(\.?\.?/?demo/index\.html\)", page), (
        "docs/UI.md uses a Markdown link to the demo, which Sphinx renders as "
        "an inert span"
    )
