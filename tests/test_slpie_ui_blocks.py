"""§31 — screens as data, and the component dictionary that draws them.

The thesis: a screen is a JSON dictionary of component keys, each key resolving
to a piece of CSS, HTML and JavaScript, consuming what the kernel provides and
wearing the reader's own words. These tests hold up the two halves that can be
checked from Python — that the manifest never names a component the browser does
not have, and that composing never overrides a screen somebody designed.
"""

from __future__ import annotations

import json
import re

import pytest

from slpie.ui.api import Api
from slpie.ui.contract import (
    COMPONENTS, FORMATS, Block, Column, javascript, screens,
)

from _walk import REPOSITORY

APP = REPOSITORY / "slpie" / "ui" / "app"


@pytest.fixture(scope="module")
def routes() -> tuple[tuple[str, str], ...]:
    return tuple(Api(engine=None).routes)


@pytest.fixture(scope="module")
def manifest(routes):
    return screens(routes=routes)


def _js_components() -> set[str]:
    """The keys of `COMPONENTS` in `app/ui/components.js`.

    Parsed out of the source rather than listed here, for the same reason the
    service-worker test derives the stream path from the `EventSource(...)` call
    instead of pinning the literal: a test that restates what it checks passes
    when both copies are wrong together.
    """
    source = (APP / "ui" / "components.js").read_text(encoding="utf-8")
    body = re.search(r"export const COMPONENTS = \{(.*?)\n\};", source, re.S)
    assert body, "components.js no longer exports a COMPONENTS registry"
    return set(re.findall(r"^\s*(\w+):", body.group(1), re.M))


# -- the dictionary ------------------------------------------------------


def test_the_addressable_components_match_the_browser_registry() -> None:
    """Equal in both directions.

    A name Python knows with no implementation renders a blank area; an
    implementation the manifest cannot name is unreachable code. Both are
    invisible without this, which is why the check is equality rather than
    containment.
    """
    assert _js_components() == set(COMPONENTS)


def test_every_block_names_a_component_that_exists(manifest) -> None:
    """Caught in Python, not found as a blank area in a browser."""
    named = {block.component for screen in manifest for block in screen.blocks}
    assert named, "no screen carries any blocks — the manifest stopped composing"
    assert named <= set(COMPONENTS)


def test_a_block_naming_an_unknown_component_is_refused() -> None:
    with pytest.raises(ValueError) as raised:
        Block("nonesuch")
    assert "nonesuch" in str(raised.value)


def test_a_column_asking_for_an_unknown_format_is_refused() -> None:
    """A block cannot carry a function, so a format is a name — and a name
    nobody implements has to fail where it is written."""
    with pytest.raises(ValueError):
        Column("severity", format="rainbow")
    assert Column("severity", format="severity").format in FORMATS


def test_every_declared_format_is_implemented_in_the_browser() -> None:
    source = (APP / "ui" / "components.js").read_text(encoding="utf-8")
    body = re.search(r"const FORMATS = \{(.*?)\n\};", source, re.S)
    assert body
    implemented = set(re.findall(r'^\s*(\w+|""):', body.group(1), re.M))
    implemented = {"" if name == '""' else name for name in implemented}
    assert implemented == set(FORMATS)


# -- composing -----------------------------------------------------------


def test_every_unauthored_screen_composes_rather_than_dumping(manifest) -> None:
    """The change this step exists to make.

    Thirty-two screens printing `JSON.stringify(body, null, 2)` reads as
    unfinished no matter how good the authored four are. Every screen nobody
    hand-built now carries blocks — derived from what it declares it reads, so a
    screen adding a route composes it with no file edited.
    """
    naked = [
        screen.key for screen in manifest
        if not screen.authored and not screen.blocks
    ]
    assert naked == []


def test_authored_screens_are_untouched_by_this(manifest) -> None:
    """Authored beats composed beats dumped.

    `screens/index.js` resolves a hand-built module first, so a designed screen
    ignores blocks entirely. Asserting the manifest declares none for them keeps
    that unambiguous rather than relying on the resolution order alone.
    """
    authored = [screen for screen in manifest if screen.authored]
    assert len(authored) >= 7
    assert all(not screen.blocks for screen in authored)


def test_a_group_inspector_is_the_verb_runner(manifest) -> None:
    groups = [s for s in manifest if s.key.startswith("group-")]
    assert groups
    for screen in groups:
        assert [block.component for block in screen.blocks] == ["runner"]


def test_a_route_inspector_renders_by_shape(manifest) -> None:
    """Python cannot know an arbitrary route's body, and says so with `auto`
    rather than declaring columns that drift the first time a payload changes."""
    inspectors = [s for s in manifest if s.key.startswith("route-")]
    assert inspectors
    for screen in inspectors:
        assert [block.component for block in screen.blocks] == ["auto"]
        assert screen.blocks[0].source in screen.reads


def test_a_block_only_reads_a_route_its_screen_declares(manifest) -> None:
    """Otherwise the manifest's `reads` stops being the truth about fetching,
    and "which screen reads which route" — a fact three other tests rely on —
    becomes something you learn by grepping again."""
    for screen in manifest:
        for block in screen.blocks:
            if not block.source:
                continue
            assert block.source in screen.reads, f"{screen.key}: {block.source}"


def test_declared_columns_survive_into_the_manifest(manifest) -> None:
    throttling = next(s for s in manifest if s.key == "throttling")
    grid = next(b for b in throttling.blocks if b.component == "grid")
    assert [column.key for column in grid.columns][:3] == [
        "name", "requests", "window_seconds",
    ]
    assert grid.to_dict()["columns"][1]["align"] == "right"


# -- the projection ------------------------------------------------------


def test_blocks_travel_in_the_generated_client(routes) -> None:
    source = javascript(routes=routes)
    body = re.search(r"export const SCREENS = Object\.freeze\((.*?)\);\n",
                     source, re.S)
    assert body
    manifest = json.loads(body.group(1))
    composing = [item for item in manifest if item["blocks"]]
    assert composing
    assert all("component" in block
               for item in composing for block in item["blocks"])


def test_the_default_lexicon_is_baked_for_the_first_frame(routes) -> None:
    """A console must render correct labels before any round trip, and offline
    the round trip never happens at all."""
    source = javascript(routes=routes)
    body = re.search(r"export const LEXICON = Object\.freeze\((.*?)\);\n",
                     source, re.S)
    assert body
    words = json.loads(body.group(1))
    assert words["node"]["word"] == "node"
    assert words["node"]["plural"] == "nodes"
    assert words["severity.critical"]["word"] == "critical"


def test_the_generated_client_is_deterministic(routes) -> None:
    assert javascript(routes=routes) == javascript(routes=routes)


# -- the browser modules -------------------------------------------------


def test_the_new_modules_are_precached(routes) -> None:
    """A module nobody added to the shell list installs fine, works online, and
    breaks offline — the failure nobody reproduces until a plane."""
    worker = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/core/lexicon.js"' in worker
    assert '"/ui/components.js"' in worker


def test_the_component_dictionary_obeys_the_ring_rule() -> None:
    """`ui/` may import `core/` and its siblings, never `screens/`.

    `runner` is the reason this could have been broken: it is a control, and the
    obvious implementation imports the verb forms from `screens/`. It takes them
    as an injected function instead.
    """
    source = (APP / "ui" / "components.js").read_text(encoding="utf-8")
    imports = re.findall(r'from\s+"([^"]+)"', source)
    assert imports
    assert not [item for item in imports if "screens/" in item]


def test_nothing_in_the_dictionary_uses_inner_html() -> None:
    """`script-src 'self'` does not stop DOM injection, and this module renders
    operator-authored labels."""
    for name in ("ui/components.js", "core/lexicon.js", "screens/inspector.js"):
        assert "innerHTML" not in (APP / name).read_text(encoding="utf-8")
