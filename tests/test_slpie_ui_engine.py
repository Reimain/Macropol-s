"""The renderer seam, and the boundary that keeps "air-gapped" literally true.

§32 allows third-party rendering engines and refuses to let one become load
bearing. The difference between those two positions is entirely a matter of what
is checked, so this module checks it:

* every engine declares whether it is native, and the default declares `true`;
* nothing in the shipped tree statically imports from `engine/vendor/`;
* nothing in `engine/vendor/` is precached, so the offline shell is the native
  path and only the native path;
* the projection is pure — no DOM anywhere in `camera.js` — because a camera bug
  and a layout bug look identical on a canvas and telling them apart means
  testing the maths with no browser in the way.

The browser tier proves the projection is *correct* against known points. This
module proves the tier is *shaped* the way the boundary requires, which is the
half that a deleted directory has to survive.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from slpie.ui import APP_ROOT

ROOT = Path(__file__).resolve().parent.parent
ENGINE = APP_ROOT / "engine"
VENDOR = ENGINE / "vendor"


def _modules() -> list[Path]:
    """The native tier: everything under `engine/` that is ours."""
    found = sorted(
        path for path in ENGINE.rglob("*.js")
        if path.is_file() and VENDOR not in path.parents
    )
    assert found, f"nothing under {ENGINE} — did the renderer tier move?"
    return found


def _libraries() -> set[Path]:
    """Third-party files, read from the vendoring declaration rather than guessed.

    `tools/vendored.json` is the statement of what was fetched from where. A
    file under `vendor/` that it does not name is ours — a wrapper — and the two
    are held to different rules: a wrapper must declare `native: false`, and a
    library must not be edited at all.
    """
    import json

    document = json.loads(
        (Path(__file__).resolve().parent.parent / "tools" / "vendored.json")
        .read_text(encoding="utf-8"),
    )
    named: set[Path] = set()
    for package in document["packages"]:
        for entry in package["files"]:
            named.add((ROOT / package["destination"] / entry["to"]).resolve())
    return named


def _wrappers() -> list[Path]:
    libraries = _libraries()
    return sorted(
        path for path in VENDOR.glob("*.js") if path.resolve() not in libraries
    )


def _shipped() -> list[Path]:
    return sorted(path for path in APP_ROOT.rglob("*.js") if path.is_file())


# --- the tier exists and is self-contained -----------------------------------


def test_the_tier_ships_a_protocol_a_camera_a_layout_and_a_native_default():
    for name in ("contract.js", "camera.js", "layout.js", "canvas2d.js"):
        assert (ENGINE / name).is_file(), f"engine/{name} is missing"


def test_the_default_engine_declares_itself_native():
    body = (ENGINE / "canvas2d.js").read_text(encoding="utf-8")
    assert re.search(r"\bnative:\s*true\b", body), (
        "the default renderer must declare `native: true` — it is the one that "
        "runs with nothing outside this repository present"
    )


def test_every_vendored_engine_declares_that_it_is_not_native():
    """The honesty lives in the metadata, which is where this product puts it.

    Only *wrappers* are held to this — a vendored library is not an engine and
    has no opinion about air gaps. Which is which comes from
    `tools/vendored.json` rather than from a filename convention, because the
    declaration is the thing that actually knows.
    """
    for path in _wrappers():
        body = path.read_text(encoding="utf-8")
        assert re.search(r"\bnative:\s*false\b", body), (
            f"{path.name} is a vendored engine and does not declare `native: false`"
        )


def test_every_vendored_library_matches_its_recorded_digest():
    """A third party's source, unedited, and provably so.

    An edited vendored file is a fork nobody declared: it works, it blocks the
    next upgrade, and there is no other way to notice. `tools/vendor.py --check`
    is the same `--check` discipline the four contract emitters already use.
    """
    from tools.vendor import check, load

    problems = check(load())
    assert not problems, "\n".join(problems)


def test_every_vendored_package_records_its_licence_and_where_it_came_from():
    import json

    document = json.loads(
        (ROOT / "tools" / "vendored.json").read_text(encoding="utf-8"),
    )
    for package in document["packages"]:
        for field in ("name", "version", "registry", "license", "why"):
            assert package.get(field), f"{package.get('name')} declares no {field}"
        for entry in package["files"]:
            assert entry["sha256"] and entry["bytes"], (
                f"{package['name']}/{entry['to']} has no digest recorded"
            )


def test_the_vendor_directory_records_its_boundary():
    datasheet = VENDOR / "DATASHEET.md"
    assert datasheet.is_file(), "engine/vendor/ ships without a DATASHEET.md"
    body = datasheet.read_text(encoding="utf-8")
    for required in ("sha256", "licence", "upstream URL", "native"):
        assert required in body, f"the datasheet does not mention {required!r}"


# --- the boundary --------------------------------------------------------------


def test_nothing_outside_vendor_statically_imports_into_it():
    """The assertion that keeps the air-gapped claim literal.

    A static `import "./vendor/x.js"` in a build that ships without `vendor/`
    takes the whole screen down, and a build that *needs* one is no longer
    air-gapped whatever the prose says. `contract.js` reaches for an engine by
    name at runtime instead, and falls back with a stated reason.

    The rule is directional and stated as such: files **outside** `vendor/` may
    not statically import files **inside** it. A wrapper importing the library
    it wraps, or reaching up to the shared scene modules, is fine — both
    disappear together when the directory does. Matching on the substring
    `vendor/` instead would have got this right by accident and been wrong the
    first time a wrapper imported `./three.module.min.js`.
    """
    static = re.compile(r"""^\s*import\s[^;]*?["']([^"']+)["']""", re.MULTILINE)
    offenders = []
    checked = 0
    for path in _shipped():
        if VENDOR in path.parents:
            continue
        for specifier in static.findall(path.read_text(encoding="utf-8")):
            checked += 1
            if not specifier.startswith("."):
                continue
            target = (path.parent / specifier).resolve()
            if VENDOR == target.parent or VENDOR in target.parents:
                offenders.append(f"{path.relative_to(APP_ROOT)} imports {specifier}")
    assert checked, "no imports were examined — did the specifier regex stop matching?"
    assert not offenders, (
        f"these modules would not load with engine/vendor/ deleted: {offenders}"
    )


def test_a_wrapper_may_import_the_library_it_wraps():
    """The converse, so the rule above is not accidentally absolute.

    Vacuous until something is vendored, and the guard says so rather than
    passing silently over an empty directory.
    """
    wrappers = [
        path for path in sorted(VENDOR.glob("*.js"))
        if not path.name.endswith(".min.js")
    ]
    if not wrappers:
        pytest.skip("nothing is vendored yet, so there is no wrapper to check")

    static = re.compile(r"""^\s*import\s[^;]*?["']([^"']+)["']""", re.MULTILINE)
    for path in wrappers:
        for specifier in static.findall(path.read_text(encoding="utf-8")):
            target = (path.parent / specifier).resolve()
            assert target.is_file(), f"{path.name} imports {specifier}, which is not there"


def test_the_dynamic_specifier_is_the_only_way_in():
    """One reachable path to a vendored engine, and it is legible.

    Kept in a named function rather than inlined at the call site so a reader
    can find every route into `vendor/` by reading one screen of code — and so
    this test has something specific to point at.
    """
    body = (ENGINE / "contract.js").read_text(encoding="utf-8")
    assert "function importer(" in body
    assert re.search(r"import\(`\./vendor/\$\{name\}\.js`\)", body), (
        "contract.js no longer resolves a vendored engine through one dynamic "
        "specifier — if that moved, so did the boundary"
    )


def test_no_vendored_file_is_precached():
    block = re.search(
        r"const SHELL = \[(.*?)\];",
        (APP_ROOT / "sw.js").read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert block, "sw.js no longer declares a SHELL list"
    listed = re.findall(r'"([^"]+)"', block.group(1))
    assert not [path for path in listed if "/engine/vendor/" in path], (
        "a vendored engine is in the offline shell, which makes the air-gapped "
        "console depend on something outside this repository"
    )


def test_the_native_tier_is_precached_in_full():
    """The converse. The default renderer must survive the network being gone."""
    block = re.search(
        r"const SHELL = \[(.*?)\];",
        (APP_ROOT / "sw.js").read_text(encoding="utf-8"),
        re.DOTALL,
    )
    listed = set(re.findall(r'"([^"]+)"', block.group(1)))
    missing = [
        "/" + str(path.relative_to(APP_ROOT))
        for path in _modules()
        if VENDOR not in path.parents
        and "/" + str(path.relative_to(APP_ROOT)) not in listed
    ]
    assert not missing, f"these ship and break offline: {missing}"


# --- the split that makes the maths testable -----------------------------------


def test_the_camera_touches_no_dom():
    """A camera bug looks exactly like a layout bug on a canvas.

    Keeping the projection free of the DOM is what lets it be exercised against
    known points, so a wrong number names which half is wrong instead of
    inviting somebody to squint at a picture.
    """
    body = (ENGINE / "camera.js").read_text(encoding="utf-8")
    for forbidden in ("document", "window", "getContext", "getComputedStyle", "canvas"):
        assert not re.search(rf"\b{forbidden}\b", _uncommented(body)), (
            f"camera.js reaches {forbidden} — the projection must stay pure"
        )


def test_only_the_native_renderer_touches_a_rendering_context():
    """One module in the shared tier draws; the rest decide what to draw.

    Vendored engines are renderers by definition and hold their own contexts —
    that is what they are for. The rule is about the *shared* modules: layout,
    projection, colouring, glyphs and aggregation must stay free of a context,
    or the scene stops being something two engines can both draw.
    """
    holders = [
        str(path.relative_to(APP_ROOT))
        for path in _modules()
        if "getContext" in _uncommented(path.read_text(encoding="utf-8"))
    ]
    assert holders == ["engine/canvas2d.js"], (
        f"a rendering context is held outside the renderer: {holders}"
    )


def test_the_shared_scene_modules_are_engine_agnostic():
    """No engine name appears in the modules both engines consume.

    This is what makes "different renderers, same answer" structural rather
    than aspirational: if `aggregate.js` ever learns which engine is asking, the
    two pictures can diverge and nothing would catch it.
    """
    shared = ("layout.js", "camera.js", "palette.js", "glyph.js", "aggregate.js")
    for name in shared:
        body = _uncommented((ENGINE / name).read_text(encoding="utf-8"))
        for engine in ("three", "THREE", "canvas2d", "WebGL", "webgl"):
            assert engine not in body, (
                f"{name} mentions {engine!r} — a shared scene module must not "
                f"know which engine is drawing it"
            )


def test_the_palette_is_resolved_once_and_never_inside_a_frame():
    """The measured lesson, pinned.

    The prototype called `getComputedStyle` per node per frame: 16fps at twenty
    thousand nodes, and very nearly the conclusion that a 600KB engine was
    needed. Hoisting it took the same scene to 60. This test is what stops the
    call drifting back into the loop, because the symptom is a frame rate rather
    than a failure and nobody bisects a frame rate.
    """
    body = _uncommented((ENGINE / "canvas2d.js").read_text(encoding="utf-8"))
    reads = [line for line in body.splitlines() if "getComputedStyle" in line]
    assert len(reads) == 1, f"style is read from {len(reads)} places, expected 1"

    draw = body[body.index("  draw("):]
    assert "getComputedStyle" not in draw, (
        "draw() reads computed style — that is the per-frame cost that produced "
        "a false case for a third-party engine"
    )


def _uncommented(body: str) -> str:
    """Strip block and line comments, so prose about a hazard is not the hazard.

    Every one of these modules documents what it must not do. A grep that could
    not tell the warning from the offence would make writing the warning fail
    the test, which teaches people to stop writing them.
    """
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", body, flags=re.MULTILINE)


def test_the_comment_stripper_does_not_strip_code():
    assert "getContext" in _uncommented("const a = 1;\nx.getContext('2d');")
    assert "getContext" not in _uncommented("/* never call getContext here */")
    assert "getContext" not in _uncommented("// never call getContext here")


# --- the contract refuses what it cannot draw with -----------------------------


@pytest.mark.parametrize("method", ["mount", "draw", "dispose"])
def test_the_protocol_names_every_method_an_engine_must_have(method):
    body = (ENGINE / "contract.js").read_text(encoding="utf-8")
    assert f'"{method}"' in body, (
        f"contract.js no longer checks for {method}() at registration"
    )
    assert f"{method}(" in (ENGINE / "canvas2d.js").read_text(encoding="utf-8")


# --- colour and shape: three channels, three meanings --------------------------


def _hex(name: str, source: str) -> str | None:
    found = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", source)
    return found.group(1) if found else None


def _saturation(colour: str) -> float:
    import colorsys

    red, green, blue = (int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5))
    return colorsys.rgb_to_hls(red, green, blue)[2]


def test_saturation_is_reserved_for_severity():
    """The reservation is a fact, not an intention.

    Region hue and finding severity are two meanings on adjacent channels, and
    the only thing keeping them apart is that one is muted and the other is
    not. Left as a note in a docstring that holds until somebody picks a
    prettier region colour; measured here, it holds.
    """
    tokens = (APP_ROOT / "styles" / "tokens.css").read_text(encoding="utf-8")

    regions = {
        f"--flight-hue-{index}": _hex(f"--flight-hue-{index}", tokens)
        for index in range(1, 11)
    }
    assert all(regions.values()), f"a region hue is not declared: {regions}"

    loudest = max(_saturation(colour) for colour in regions.values())
    quietest = min(
        _saturation(_hex(name, tokens))
        for name in ("--ok", "--warn", "--bad", "--crit")
    )
    assert loudest < quietest, (
        f"the most saturated region hue ({loudest:.2f}) is at least as vivid as "
        f"the least saturated severity ({quietest:.2f}) — the one vivid thing on "
        f"a flight canvas has to be a finding"
    )


def test_the_region_hues_are_all_different():
    tokens = (APP_ROOT / "styles" / "tokens.css").read_text(encoding="utf-8")
    hues = [_hex(f"--flight-hue-{index}", tokens) for index in range(1, 11)]
    assert len(set(hues)) == len(hues), f"two region hues are the same colour: {hues}"


def test_the_estate_is_not_given_a_hue():
    """The backdrop must not compete with what somebody took the trouble to declare."""
    palette = (ENGINE / "palette.js").read_text(encoding="utf-8")
    assert 'export const ESTATE = "--flight-estate"' in palette
    assert "--flight-estate" not in re.search(
        r"export const HUES = \[(.*?)\];", palette, re.DOTALL,
    ).group(1)


def test_the_palette_never_wraps_by_modulo():
    """The bug this replaced, pinned so it cannot come back.

    `index % HUES.length` put one hue on two *adjacent* regions and said
    nothing. The picture was wrong and looked fine, which is the worst failure
    a visualisation has.
    """
    body = _uncommented((ENGINE / "palette.js").read_text(encoding="utf-8"))
    assert "%" not in body, (
        "palette.js contains a modulo — if the hue assignment wraps, an "
        "overflowing colouring is silently wrong"
    )
    assert "overflow" in body and "shortfall" in body


def test_every_node_kind_has_a_family_and_a_shape():
    """Read off the domain enum, so a new kind cannot slip through unshaped."""
    from slpie.domain.node import NodeKind

    body = (ENGINE / "glyph.js").read_text(encoding="utf-8")
    mapping = re.search(r"export const FAMILY_OF = \{(.*?)\};", body, re.DOTALL).group(1)
    named = set(re.findall(r"(\w+):\s*\"", mapping))
    missing = sorted(kind.value for kind in NodeKind if kind.value not in named)
    assert not missing, f"these node kinds have no glyph family: {missing}"

    shapes = re.search(r"export const SHAPE_OF = \{(.*?)\};", body, re.DOTALL).group(1)
    families = set(re.findall(r"(\w+):\s*\"", shapes))
    assert set(re.findall(r':\s*"(\w+)"', mapping)) <= families, (
        "a family is used in FAMILY_OF and has no shape"
    )


def test_shape_and_colour_are_separate_channels():
    """`glyph.js` must not reach for a hue, and `palette.js` must not reach for a shape."""
    glyph = _uncommented((ENGINE / "glyph.js").read_text(encoding="utf-8"))
    palette = _uncommented((ENGINE / "palette.js").read_text(encoding="utf-8"))
    assert "flight-hue" not in glyph, "glyph.js assigns a region hue"
    assert "SHAPE" not in palette and "outline" not in palette, (
        "palette.js decides a shape"
    )
