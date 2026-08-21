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

    `core/`, `ui/` and `screens/` are the stdlib shell's own implementation —
    its DOM helpers, its components, its router. A built shell reaching into
    them would be coupling to a rendering strategy rather than to a model, and
    the two would then have to move together forever.
    """
    reaching = re.compile(r"""from\s+["'][^"']*app/(core|ui|screens|data)/""")
    offenders = [
        str(path.relative_to(ROOT)) for path in _sources()
        if path.suffix in {".ts", ".tsx"}
        and reaching.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"a client imports the stdlib console's own internals: {offenders}"
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

    finished = subprocess.run(
        ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True, timeout=600,
    )
    assert finished.returncode == 0, (
        f"the enterprise shell does not build:\n{finished.stdout}\n{finished.stderr}"
    )
