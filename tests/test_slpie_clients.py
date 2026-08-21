"""The built shells, and the one property that keeps them one product.

The stdlib console is deliberately minimal, and it has a real ceiling: direct
manipulation, panes the reader arranges and a scrubbable axis over the ledger
are code rather than a block declaration, and pretending a manifest can express
them turns the manifest into a bad framework. So there is a second shell.

Two shells is two products the moment they hold two copies of anything. The
rule that stops that is **verbatim reuse**: `clients/web` imports the scene
modules from `slpie/ui/app/engine/` — the same files — and this module asserts
it, because a copy made to fix something quickly is exactly how the divergence
starts and it never announces itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "clients"
WEB = CLIENTS / "web"
ENGINE = ROOT / "slpie" / "ui" / "app" / "engine"

#: The shared scene: placement, projection, colouring, glyph geometry and
#: aggregation. No framework in any of them, which is what makes them shareable.
SCENE = ("camera.js", "layout.js", "palette.js", "glyph.js", "aggregate.js", "contract.js")


def _sources() -> list[Path]:
    return [
        path for path in CLIENTS.rglob("*")
        if path.is_file()
        and "node_modules" not in path.parts
        and "dist" not in path.parts
    ]


def test_no_scene_module_is_copied_into_a_client():
    """The assertion behind the word "verbatim".

    Compared by content digest rather than by filename, because the copy that
    causes trouble is the one somebody renamed on the way in.
    """
    originals = {
        hashlib.sha256((ENGINE / name).read_bytes()).hexdigest(): name
        for name in SCENE
    }
    assert len(originals) == len(SCENE), "two scene modules have identical content"

    duplicates = []
    for path in _sources():
        if path.suffix not in {".js", ".ts", ".tsx", ".mjs"}:
            continue
        found = originals.get(hashlib.sha256(path.read_bytes()).hexdigest())
        if found:
            duplicates.append(f"{path.relative_to(ROOT)} is a copy of engine/{found}")
    assert not duplicates, (
        "a client holds its own copy of a shared scene module, so the two "
        f"shells can now disagree: {duplicates}"
    )


def test_the_web_shell_reaches_the_scene_through_one_declared_alias():
    """One route in, and it points at ring 0.

    A second alias, or a relative path climbing out of the package, would each
    be a place the reuse could later be pointed somewhere else without anybody
    noticing.
    """
    config = (WEB / "vite.config.ts").read_text(encoding="utf-8")
    assert '"@scene": scene' in config
    assert 'resolve(here, "../../slpie/ui/app/engine")' in config, (
        "the @scene alias no longer points at the ring-0 renderer tier"
    )

    paths = json.loads(
        re.sub(r"//.*", "", (WEB / "tsconfig.json").read_text(encoding="utf-8")),
    )["compilerOptions"]["paths"]
    assert paths["@scene/*"] == ["../../slpie/ui/app/engine/*"], (
        "the type-checker and the bundler disagree about where the scene is"
    )


def test_every_scene_import_in_the_web_shell_resolves_to_a_real_module():
    specifier = re.compile(r"""from\s+["'](@scene/[^"']+)["']""")
    seen = 0
    for path in _sources():
        if path.suffix not in {".ts", ".tsx"}:
            continue
        for found in specifier.findall(path.read_text(encoding="utf-8")):
            seen += 1
            target = ENGINE / found[len("@scene/"):]
            assert target.is_file(), f"{path.name} imports {found}, which is not there"
    assert seen, "no @scene import was found — is the shell still sharing anything?"


def test_the_web_shell_does_not_reach_past_the_scene_into_the_console():
    """It shares the *scene*, not the console.

    `core/`, `components/` and `screens/` are the stdlib shell's own implementation —
    its DOM helpers, its components, its router. A built shell reaching into
    them would be coupling to a rendering strategy rather than to a model, and
    the two would then have to move together forever.
    """
    reaching = re.compile(r"""from\s+["'][^"']*app/(core|components|screens|data)/""")
    offenders = [
        str(path.relative_to(ROOT)) for path in _sources()
        if path.suffix in {".ts", ".tsx"}
        and reaching.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"a client imports the stdlib console's own internals: {offenders}"
    )


# --- scaffolds, declared rather than implied ---------------------------------


def _claimed() -> dict[str, str]:
    """The status table in `clients/README.md`, as a mapping."""
    rows = re.findall(
        r"^\|\s*`(\w+)`\s*\|\s*\*\*(\w+)\*\*\s*\|",
        (CLIENTS / "README.md").read_text(encoding="utf-8"),
        re.M,
    )
    return dict(rows)


def test_the_readme_status_matches_the_tree():
    """A green tick for something never compiled is worse than an honest scaffold.

    Two shells here have never been built — there is no Rust toolchain and no
    mobile simulator in this environment — and the useful thing to do about that
    is to say so in a form that cannot quietly become false. So the README's
    claim is *parsed* and held against the directories in both directions.

    A shell claiming **builds** must have a build script and an entry point; a
    shell claiming **scaffold** must have neither. The second half is the one
    that earns its keep: the day somebody adds a Tauri build, the README stops
    being true and this test says so on that commit.
    """
    claims = _claimed()
    assert claims, "the README's status table no longer parses — did its shape change?"

    directories = {
        path.name for path in CLIENTS.iterdir()
        if path.is_dir() and path.name != "node_modules"
    }
    assert set(claims) == directories, (
        f"the README describes {sorted(claims)} and the tree holds {sorted(directories)}"
    )

    for shell, state in sorted(claims.items()):
        assert state in {"builds", "scaffold"}, f"{shell} claims an unknown state {state!r}"
        package = CLIENTS / shell / "package.json"
        scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}) \
            if package.is_file() else {}
        entries = [
            name for name in ("index.html", "src/main.tsx", "src/index.tsx", "src/App.tsx")
            if (CLIENTS / shell / name).is_file()
        ]

        if state == "builds":
            assert "build" in scripts, f"{shell} claims to build and has no build script"
            assert entries, f"{shell} claims to build and has no entry point"
            assert (CLIENTS / shell / "tsconfig.json").is_file(), (
                f"{shell} claims to build and has no tsconfig"
            )
        else:
            assert "build" not in scripts, (
                f"{shell} is documented as a scaffold and has a build script — "
                f"the README is now wrong, which is the failure this test exists for"
            )
            assert not entries, (
                f"{shell} is documented as a scaffold and has an entry point: {entries}"
            )


def test_every_shell_holds_the_same_generated_client():
    """The scaffolds are real code, not a sketch, and this is what makes that true.

    A scaffold whose client had drifted would be a shell nobody could pick up:
    the first thing anyone does with one is generate a screen against the client,
    and a stale one sends them at routes that no longer exist. `tools/clients.py
    --check` gates all three in CI; this asserts the third property that check
    cannot — that they are the *same* client, byte for byte, rather than three
    that each happen to be current.
    """
    digests = {
        shell: hashlib.sha256(
            (CLIENTS / shell / "src" / "slpie-client.ts").read_bytes()
        ).hexdigest()
        for shell in sorted(_claimed())
    }
    assert len(set(digests.values())) == 1, (
        f"the shells hold different generated clients: {digests}"
    )


# --- identity, and what a refusal looks like ---------------------------------


def test_the_generated_client_carries_a_credential():
    """One header, emitted once, for all three shells.

    The gateway identifies a caller from `Authorization` and nothing else, so a
    shell that minted its own session would be a second identity path with its
    own bugs — the thing §16 refuses to build when it declines to reimplement
    the live guard for FastAPI. Emitting it in the generator is what makes the
    web, desktop and mobile clients agree without any of them being asked to.
    """
    from slpie.ui.contract import typescript

    body = typescript()
    assert "token?: string | (() => string | null | undefined);" in body, (
        "the generated client has no way to be given a credential"
    )
    assert "authorization: `Bearer ${held}`" in body
    # Absent a token, nothing is sent. An empty `Authorization` is not anonymity
    # — it is a malformed credential, and the gateway is right to treat the two
    # differently.
    assert "return held ? {" in body


def test_a_refusal_reaches_the_client_with_its_way_out():
    """`Decision.explain()` and `.obligation` survive the transport.

    The gateway computes what would allow the call. A client that kept only the
    status and the sentence would leave every refused reader asking an operator
    for something the platform already knew — which is how a correct refusal
    becomes a support ticket.
    """
    from slpie.ui.contract import typescript

    body = typescript()
    for field in ("refused:", "stage:", "obligation:", "retryAfter:"):
        assert field in body, f"a refusal loses {field} on the way to the client"


def test_a_refusal_is_never_rendered_as_a_fault():
    """Accent, never danger. The one rule this shell shares with the console.

    A 403 in red teaches people that policy is a bug and then they file tickets
    about the platform working correctly. So the two treatments are different
    classes, drawn in one component, and the stylesheet is where the rule is
    actually enforced — `.refusal` may not reach for a danger token and
    `.fault` may not reach for the accent.
    """
    css = (WEB / "src" / "index.css").read_text(encoding="utf-8")

    refusal = re.search(r"^\.refusal\s*\{([^}]*)\}", css, re.M)
    fault = re.search(r"^\.fault\s*\{([^}]*)\}", css, re.M)
    assert refusal and fault, "the shell no longer draws a refusal and a fault differently"

    assert "--accent" in refusal.group(1), "a refusal is not drawn in the accent"
    for danger in ("--bad", "--crit", "--warn"):
        assert danger not in refusal.group(1), (
            f"a refusal reaches for {danger} — policy rendered as a failure"
        )
    assert "--bad" in fault.group(1), "a fault is not drawn in the danger colour"
    assert "--accent" not in fault.group(1), "a fault is drawn as if it were policy"

    # And one component draws them, so the rule has one place to be broken.
    card = (WEB / "src" / "ui" / "Refusal.tsx").read_text(encoding="utf-8")
    assert 'className="fault"' in card and 'className="refusal"' in card
    elsewhere = [
        str(path.relative_to(ROOT)) for path in _authored()
        if path.name != "Refusal.tsx" and 'className="fault"' in path.read_text(encoding="utf-8")
    ]
    assert not elsewhere, f"a fault is drawn outside the one component: {elsewhere}"


# --- one ride, driven from both shells ---------------------------------------


#: The modules that decide what a ride *is* — the condition model, the rail, the
#: camera along it, and the narration. A shell that reimplemented any one of
#: them would produce a second answer wearing the first one's clothes.
RIDE = ("condition.js", "route.js", "ride.js", "narrate.js")


def test_both_shells_drive_the_ride_from_the_same_modules():
    """The hops, and their order, come from one place or they are two answers.

    `rail()` sorts by distance, then confidence descending, then id — a *total*
    order, so one query produces one ride every time. That guarantee is worth
    exactly as much as the number of implementations of it, which has to be one.

    So the assertion is not "the two orders happen to match today". It is that
    both shells obtain the order from `engine/route.js` and neither sorts hops
    itself — a match asserted between two implementations passes until the day
    somebody touches one of them.
    """
    stdlib = (ROOT / "slpie" / "ui" / "app" / "screens" / "graph.js").read_text(encoding="utf-8")
    web = (WEB_SCREENS / "flight.tsx").read_text(encoding="utf-8")

    assert re.search(r'import\s*\{[^}]*\brail\b[^}]*\}\s*from\s*"\.\./engine/route\.js"', stdlib), (
        "the stdlib flight mode no longer takes its route from engine/route.js"
    )
    assert re.search(r'import\s*\{[^}]*\brail\b[^}]*\}\s*from\s*"@scene/route\.js"', web), (
        "the built shell no longer takes its route from the shared engine"
    )

    # Neither may re-derive the ordering. `.sort(` on the hops is the specific
    # way a second order gets introduced, and it would look entirely reasonable
    # in review.
    for name, body in (("graph.js", stdlib), ("flight.tsx", web)):
        assert not re.search(r"hops\s*\.\s*sort\s*\(", body), (
            f"{name} sorts the hops itself instead of taking `rail`'s order"
        )


def test_the_built_shell_uses_every_module_the_ride_is_made_of():
    """The condition model included, which is the one that is easy to skip.

    A shell could animate a camera without it and look fine — and would then
    have no `CHOOSING`, which is the condition that renders nothing and the rule
    three prototypes broke. The others are the same argument: a scrubber that
    did not go through `at()` would drift from the played ride by a rounding
    error per frame.
    """
    web = (WEB_SCREENS / "flight.tsx").read_text(encoding="utf-8")
    missing = [name for name in RIDE if f'@scene/{name}"' not in web]
    assert not missing, (
        f"the built shell drives the ride without {missing} — those decisions "
        f"are then made twice"
    )


# --- one design system, shared rather than restated --------------------------


#: Sizes that are not sizes. A hairline and a fully-rounded pill must not change
#: with the density register, so both are exempt in ring 0 and both are here.
ALLOWED_SIZES = {"1px", "2px", "3px", "999px"}


def _declarations(css: str) -> str:
    """Everything that can actually style something.

    Comments are stripped because a note explaining *why* something is not
    720px wide is not a 720px declaration, and a media query's breakpoint is a
    device fact rather than a token. Both exclusions are the ones ring 0's
    equivalent guard already makes, for the same reasons.
    """
    return re.sub(r"@media[^{]+\{", "{", re.sub(r"/\*.*?\*/", "", css, flags=re.S))


def test_the_built_shell_declares_no_colour_of_its_own():
    """Tokens come from ring 0, or the two shells drift within a release.

    This is more than consistency. `canvas2d.js` reads `--flight-surface`,
    `--flight-hue-*` and the confidence ramp off the computed style of the
    canvas it was handed, and falls back to constants baked into the module when
    they are missing. A shell restating its own palette would therefore render
    the *fallback* colours while looking perfectly fine — one graph, two
    consoles, different hues, and nothing failing anywhere.

    The exemption is tested rather than trusted: a real literal must still be
    caught in a file that also mentions one inside a comment.
    """
    probe = _declarations("/* not #ff0000 */\n.a { color: #00ff00; }")
    assert "#00ff00" in probe and "#ff0000" not in probe

    literal = re.compile(r"(#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\()")
    offenders = []
    for path in (WEB / "src").rglob("*.css"):
        found = literal.findall(_declarations(path.read_text(encoding="utf-8")))
        if found:
            offenders.append(f"{path.relative_to(ROOT)} declares {sorted(set(found))}")
    assert not offenders, (
        "the built shell states a colour instead of using ring 0's tokens: "
        + "; ".join(offenders)
    )


def test_the_built_shell_declares_no_raw_size():
    """The density axis is a token axis in both shells or in neither.

    One hardcoded `padding: 16px` here and this shell stops responding to the
    register the other one honours, which is the same failure ring 0 already
    guards against in `components.css` — asserted the same way, so the rule is
    one rule rather than two that resemble each other.
    """
    offenders = []
    for path in (WEB / "src").rglob("*.css"):
        sizes = {
            size
            for size in re.findall(r"(?<![\w-])(\d+px)", _declarations(path.read_text(encoding="utf-8")))
            if size not in ALLOWED_SIZES
        }
        if sizes:
            offenders.append(f"{path.relative_to(ROOT)} hardcodes {sorted(sizes)}")
    assert not offenders, (
        "the built shell hardcodes a size instead of using a token: " + "; ".join(offenders)
    )


def test_the_shared_tokens_are_the_same_files_ring_zero_ships():
    """Imported through an alias, not copied in beside the components.

    `@styles` resolves to `slpie/ui/app/styles/`, and the build config is what
    makes that true. Asserting the import rather than the rendered output is
    deliberate: a copied token file would produce an identical stylesheet today
    and diverge on the commit that changes one of them.
    """
    config = (WEB / "vite.config.ts").read_text(encoding="utf-8")
    assert "slpie/ui/app/styles" in config, "@styles no longer points at ring 0"

    css = (WEB / "src" / "index.css").read_text(encoding="utf-8")
    imported = set(re.findall(r'@import\s+"@styles/([\w.-]+)"', css))
    assert {"tokens.css", "density.css"} <= imported, (
        f"the shell does not import ring 0's token axes — found {sorted(imported)}"
    )
    for name in imported:
        assert (ROOT / "slpie" / "ui" / "app" / "styles" / name).is_file(), (
            f"index.css imports @styles/{name}, which ring 0 does not ship"
        )


# --- what belongs in the built shell, and what does not ----------------------


WEB_SCREENS = WEB / "src" / "screens"


def _shell_backlog() -> dict[str, tuple[str, ...]]:
    """What the air-gapped console declines, keyed by screen, from ring 0."""
    from slpie.ui.api import Api
    from slpie.ui.contract import screens as manifest, shell

    stdlib = shell("stdlib")
    assert stdlib is not None, "the stdlib shell is not registered"
    api = Api(engine=None)
    return {
        screen.key: screen.missing_from(stdlib)
        for screen in manifest(verbs=api.verbs, routes=api.routes)
        if not screen.renders_in(stdlib)
    }


def test_the_built_shell_only_holds_screens_the_stdlib_one_declines():
    """The rule that keeps this a companion rather than a second console.

    Porting the thirty-four stdlib screens into React would mean two
    implementations of every one of them, kept in step by discipline — which is
    exactly the drift the capability model was built to avoid. So the backlog is
    not a decision anybody writes down: it is `GET /api/shells`'s `cannot` map,
    and a screen both shells can draw failing here is what enforces it.

    The filename is the key deliberately. A screen added to this directory
    without a matching `Screen.requires` the stdlib console cannot meet is
    caught on the commit that adds it, not on the release that ships two
    diverging consoles.
    """
    backlog = _shell_backlog()
    assert backlog, (
        "the stdlib console declines nothing — either every screen fits in it, "
        "in which case this shell has no reason to exist, or `requires` stopped "
        "being read"
    )

    built = sorted(
        path.stem for path in WEB_SCREENS.glob("*.tsx")
    )
    assert built, f"no screens found under {WEB_SCREENS.relative_to(ROOT)}"

    trespassing = [key for key in built if key not in backlog]
    assert not trespassing, (
        f"the built shell holds screens the stdlib console can already draw: "
        f"{trespassing}. A screen both shells render is two implementations of "
        f"one thing."
    )


def test_the_shell_asks_the_platform_which_screens_it_owns():
    """It reads the manifest; it does not carry a list.

    A hand-written array of screen keys in `App.tsx` would be a second statement
    of what `cannot` already says, and the two would part company the first time
    a screen's `requires` changed. So the assertion is on the mechanism: the
    shell reads `/api/shells`, and every key it knows how to render is looked up
    from that answer rather than hardcoded beside it.
    """
    app = (WEB / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "readShells()" in app, (
        "App.tsx no longer reads the shell manifest — it is deciding for itself "
        "which screens belong here"
    )

    index = (WEB_SCREENS / "index.ts").read_text(encoding="utf-8")
    registered = set(re.findall(r"^\s*([a-z][a-z0-9-]*):\s*\w+,", index, re.M))
    on_disk = {path.stem for path in WEB_SCREENS.glob("*.tsx")}
    assert registered == on_disk, (
        f"the screen registry and the directory disagree: registry {sorted(registered)}, "
        f"files {sorted(on_disk)}"
    )


# --- what a client may draw, and where it may get it -------------------------


#: Files a client may not fabricate data in. The generated client is exempt from
#: nothing here; it simply contains no generator, and this list is what the two
#: tests below walk.
def _authored() -> list[Path]:
    return [
        path for path in _sources()
        if path.suffix in {".ts", ".tsx"} and path.name != "slpie-client.ts"
    ]


def test_no_client_fabricates_the_data_it_draws():
    """The rule the Flight screen broke for two commits, now a test.

    It built nine hundred synthetic nodes and fourteen hundred synthetic edges
    when it had nothing real to draw, with a comment saying so. The comment was
    honest and the screen was not: a reader looking at it saw an estate.

    That is the exact failure this platform exists to prevent — it distinguishes
    what was observed from what was inferred, and a demo generator inside the
    console is that claim being broken by the product that makes it. So an empty
    answer stays empty, an error stays an error, and neither is dressed up.

    Two patterns, both specific: a length-literal array comprehension (the shape
    every synthetic fixture takes) and `Math.random`, which has no honest use in
    a surface whose numbers are all meant to be counted.
    """
    generator = re.compile(r"Array\.from\(\s*\{\s*length:\s*\d")
    randomness = re.compile(r"Math\.random\s*\(")

    offenders = []
    for path in _authored():
        body = path.read_text(encoding="utf-8")
        if generator.search(body):
            offenders.append(f"{path.relative_to(ROOT)} builds an array from a length literal")
        if randomness.search(body):
            offenders.append(f"{path.relative_to(ROOT)} calls Math.random")
    assert not offenders, (
        "a client fabricates what it draws: " + "; ".join(offenders)
    )


def test_every_read_goes_through_the_generated_client():
    """`api.ts` claims nothing calls `fetch` directly. This is that claim.

    It matters beyond tidiness: the generated client is what makes a changed
    route a compile error rather than a runtime 404, and one hand-rolled `fetch`
    is a route nobody type-checks. It is also the only place the refusal shape
    is unwrapped, so a direct call would render a 403 as a generic failure.
    """
    calling = re.compile(r"(?<![.\w])fetch\s*\(")
    offenders = [
        str(path.relative_to(ROOT)) for path in _authored()
        # `api.ts` names `fetch` as a *type* when it hands the client an
        # override; that is a declaration, not a call, and the lookbehind
        # already excludes `this.doFetch(`.
        if calling.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"a client calls fetch directly instead of the generated client: {offenders}"
    )


# --- the generated client has to be valid, not merely reproducible ------------


def test_no_generated_property_name_would_fail_to_parse():
    """A generator can be perfectly deterministic and reproducibly wrong.

    The emitted client carried `max-bytes?: number` in a type literal for as
    long as it has existed, and did not compile at all. Every test around it
    asserted the output was *total* and *identical across runs* and none
    asserted it was *valid*. This is the always-available half of the fix; the
    other half runs `tsc` in CI.
    """
    from slpie.ui.contract import typescript

    body = typescript()
    literal = re.compile(r"params:\s*\{([^}]*)\}")
    checked = 0
    for group in literal.findall(body):
        for field in group.split(";"):
            name = field.split(":")[0].strip().rstrip("?")
            if not name:
                continue
            checked += 1
            assert re.match(r'^([A-Za-z_$][A-Za-z0-9_$]*|"[^"]+")$', name), (
                f"{name!r} is not a property name TypeScript can parse"
            )
    assert checked, "no parameter fields were examined — did the emitter change shape?"


def test_a_hyphenated_parameter_keeps_its_wire_name():
    """Quoted, never camel-cased.

    `changed --max-bytes` really is sent as `max-bytes`, and the client spreads
    this object straight into the request body — so renaming it to `maxBytes`
    would compile and then send a key the server refuses. The fix has to be
    correct on both sides, and only quoting is.
    """
    from slpie.ui.contract import typescript

    body = typescript()
    assert '"max-bytes"?: number' in body
    assert "maxBytes" not in body


@pytest.mark.browser
def test_the_web_shell_type_checks_and_builds():
    """The other half, and it needs a toolchain.

    Marked `browser` so it rides the same opt-in job the Playwright tier does —
    both are "needs something the kernel suite must never require". Skipped
    loudly where Node is absent, so nobody reads "not run" as "passed".
    """
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed; the built shell cannot be checked here")
    if not (WEB / "node_modules").is_dir():
        pytest.skip("clients/web dependencies are not installed — run `npm install`")
    if not (ENGINE / "vendor" / "three.js").is_file():
        # The seam's headline gate deletes `engine/vendor/` and runs this whole
        # tier. This shell *bundles* the engine it resolves, so with the
        # directory gone there is nothing for the bundler to bundle and the
        # build legitimately fails — which says nothing about the thing the gate
        # is testing, which is the console surviving without it.
        #
        # So it skips, loudly, exactly as the console's own engine tests do. It
        # cannot become a permanent silent skip: the default suite asserts those
        # files are present and match their recorded digests, so a missing
        # `vendor/` is a failure there and a skip here.
        pytest.skip(
            "three is not vendored in this checkout — this is the seam's "
            "deleted-directory gate, and the console is what it tests"
        )

    finished = subprocess.run(
        ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True, timeout=600,
    )
    assert finished.returncode == 0, (
        f"the enterprise shell does not build:\n{finished.stdout}\n{finished.stderr}"
    )
