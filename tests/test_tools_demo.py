"""The published demo — the one artefact nothing was checking.

`tools/ui/demo.py` bakes the console into a single page and the Pages workflow
publishes it at `/demo/`. It carried a hand-written list of thirty modules, and
when the browser's `ui/` tier became `components/` that list went stale: seven
files it named no longer existed, so the published demo could not be built at
all. Nothing said so, because nothing ran it — the workflow only runs on a push
to a publishing branch, which is the worst possible place to discover that a
build is broken.

So the list is derived from the imports now, and these are the tests that keep
the derivation honest. Three of them cost milliseconds; the fourth bakes the
whole page, which takes about nine seconds and is worth it — a demo that builds
is the only kind worth publishing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.ui import demo

APP = Path(demo.APP)


def test_the_bundle_reaches_every_file_that_ships():
    """Bidirectional, which is the half a dependency walk does not give you.

    Walking imports proves everything bundled is reachable. It does not prove
    the reverse — a screen nobody imports is a file that ships, renders nothing
    and is invisible to a walker that starts at the shell. Here both directions
    are asserted, so a new screen that was never registered fails this test
    rather than shipping as a blank route.
    """
    bundled = set(demo.modules())
    shipped = {
        str(path.relative_to(APP))
        for path in APP.rglob("*.js")
        if not str(path.relative_to(APP)).startswith(demo.EXCLUDED)
    }

    assert shipped, "no modules found — has the app directory moved?"
    assert not bundled - shipped, sorted(bundled - shipped)
    # The known orphans are named rather than tolerated as a category, so the
    # next one fails here instead of joining them.
    assert shipped - bundled == set(demo.UNREACHED), (
        f"ships and no screen reaches it: "
        f"{sorted((shipped - bundled) - set(demo.UNREACHED))}"
    )


def test_every_bundled_module_exists_and_the_order_is_a_dependency_order():
    """A module must be defined before anything that imports it runs."""
    ordered = demo.modules()
    seen: set[str] = set()

    for name in ordered:
        source = (APP / name).read_text(encoding="utf-8")
        for spec in demo.IMPORTS.findall(source):
            if not spec.startswith("."):
                continue
            target = demo.resolve(spec, name)
            if target.startswith(demo.EXCLUDED):
                continue
            assert target in seen, f"{name} imports {target}, which comes later"
        seen.add(name)


def test_the_stylesheet_order_is_read_from_the_stylesheet():
    for name in demo.styles():
        assert (APP / name).is_file(), f"styles.css imports {name}, which is absent"


def test_the_transform_leaves_no_module_syntax_behind():
    """A stray `export` is a syntax error inside a function body, and the page
    dies on load with one line in the console nobody is watching."""
    for name in demo.modules():
        out = demo.transform(name, (APP / name).read_text(encoding="utf-8"))
        assert not re.search(r"^\s*export\s", out, re.M), name
        assert not re.search(r'^\s*import\s+.*from\s+["\']\.', out, re.M), name


@pytest.mark.slow
def test_the_page_bakes_and_carries_what_the_screens_read(tmp_path):
    """The build itself, which is the assertion that actually matters."""
    out = tmp_path / "index.html"
    demo.main(["--out", str(out)])

    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")

    # Asserted on booleans, not on the page. `assert "x" not in page` over 1.5MB
    # makes pytest render both operands into the failure report, which takes
    # long enough to look like a hang — the test appears to freeze rather than
    # to fail, which is the least useful way for a test to be wrong.
    # Both operands kept small on purpose: `assert "x" not in page` over 1.5MB
    # makes pytest render the whole page into the failure report, which takes
    # long enough to look like a hang rather than a failure.
    leaked = sorted({
        line[:90] for line in page.split(",") if "/home/" in line or "/tmp/" in line
    })[:3]
    assert not leaked, f"the page carries build-machine paths: {leaked}"

    # Nothing fetched from another origin — asserted over the *code*, with the
    # recording cut out first. The recording is full of URLs and must be: the
    # manifest declares `https://api.acme.com/v1` as an element and npm
    # lockfiles cite `registry.npmjs.org` in every `resolved` field. Those are
    # data the console displays, not requests it makes, and a check that
    # conflated the two would have to be weakened until it caught nothing.
    import re as _re
    from urllib.parse import urlsplit

    head, _, rest = page.partition("const RECORDED = ")
    code = head + rest.partition(";\n")[2]
    origins = {
        urlsplit(found).netloc
        for found in _re.findall(r"https?://[^\s\"'<>)]+", code)
        # The SVG namespace is an XML identifier that is never resolved.
    } - {"www.w3.org"}
    assert not origins, f"the page fetches from {sorted(origins)}"

    recorded = json.loads(page.split("const RECORDED = ", 1)[1].split(";\n", 1)[0])

    from slpie.ui.api import Api

    declared = {
        path for method, path in Api(engine=None).routes
        if method == "GET" and ":" not in path and path != "/api/stream"
    }
    missing = declared - set(recorded["get"])
    assert not missing, f"screens read routes the recording does not carry: {missing}"

    # The dashboard is the one screen whose answer depends on the *body* it
    # posts, so its demands are recorded per pipeline rather than per path.
    runs = recorded["post"]["/api/run"]
    assert any("dashboard" in key for key in runs), "no dashboard run recorded"
    assert recorded["events"], "the live feed would replay nothing"
