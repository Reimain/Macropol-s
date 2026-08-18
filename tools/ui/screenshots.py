"""Regenerate the interface screenshots the documentation embeds.

    python -m tools.ui.screenshots            # into docs/_static/ui/

**Generated, not pasted.** A screenshot dragged into a documentation folder is
correct on the day it is taken and silently wrong afterwards — and unlike prose,
nobody rereads an image to check. These are produced from a real scan through a
real browser, so regenerating them is one command and the drift is visible in the
diff.

They are nevertheless **committed**. The documentation builds from a kernel-only
install with no browser, which is the same constraint invariant 4 puts on
everything else; requiring Chromium to render the docs would make the published
site depend on a toolchain the kernel refuses. `make ui-screenshots` is the
regeneration path, and `tests/test_slpie_ui_assets.py` asserts every image the
documentation references is present.

Needs the `e2e` extra and a Chromium: `pip install -e '.[e2e]'`.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "docs" / "_static" / "ui"
CHROMIUM = Path("/opt/pw-browsers")

#: Each shot names what it is *for*. A folder of `screen-1.png` is a folder
#: nobody can maintain, because deciding whether one is stale means opening it.
#:
#: Three of these are captured in **both themes**, because the front page shows
#: them and the front page itself is themed. A dark screenshot on a light page
#: is as wrong as a light one on a dark page — so the page carries both and
#: swaps them with its own tokens, which means both have to exist.
SHOTS = [
    ("console", "The console with nothing open — the way in, and the "
                "composition it will run, shown before it runs."),
    ("compose", "The verb palette, generated from the registry."),
    ("graph", "The estate, with the evidence in the stroke."),
    ("graph-selected", "A node picked: everything it does not touch dims."),
    ("graph-dark", "The dark theme, selected for its ground rather than flipped."),
    ("verbs-dense", "The dense register — a rich-client grid."),
    ("verbs-calm", "The same screen in the calm register."),
    ("graph-selected-dark", "The selected node, in the dark theme."),
    ("verbs-dense-dark", "The dense grid, in the dark theme."),
    ("verbs-calm-dark", "The calm register, in the dark theme."),
]


def _executable() -> str | None:
    found = sorted(CHROMIUM.glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else None


def capture(out: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    from slpie.ui import UiServer
    from tools.ui.world import build

    out.mkdir(parents=True, exist_ok=True)
    engine = build(tempfile.mkdtemp())
    server = UiServer(engine=engine, port=0).start()
    written: list[str] = []
    problems: list[str] = []

    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(
                headless=True, executable_path=_executable(),
            )
            # A fixed viewport and a fixed scale factor, so a rerun on another
            # machine produces a comparable image rather than a whole-file diff.
            page = browser.new_page(
                viewport={"width": 1500, "height": 950}, device_scale_factor=1,
            )
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.on(
                "console",
                lambda message: message.type == "error"
                and problems.append(message.text),
            )

            # Switching is done by *pressing the control*, never by setting the
            # attribute. Poking `dataset.theme` applies the tokens but skips
            # `setTheme()`, so no event fires, nothing redraws, and the button
            # keeps the label it had — producing a captured frame where the
            # toggle reads "Dark" on a page that is already dark. That state is
            # unreachable in the running app, and publishing a picture of it
            # would document a control defect that does not exist.
            def press(index: int) -> None:
                page.eval_on_selector_all(
                    "#appearance button",
                    f"els => els[{index}].dispatchEvent("
                    f"new MouseEvent('click', {{bubbles: true}}))",
                )
                page.wait_for_timeout(320)

            def want(theme: str = "", density: str = "") -> None:
                for _ in range(2):
                    if theme and page.evaluate(
                        "document.documentElement.dataset.theme") != theme:
                        press(1)
                    if density and page.evaluate(
                        "document.documentElement.dataset.density") != density:
                        press(0)
                applied = page.evaluate(
                    "[document.documentElement.dataset.theme,"
                    " document.documentElement.dataset.density]")
                if theme and applied[0] != theme:
                    raise SystemExit(f"could not reach theme {theme}: {applied}")
                if density and applied[1] != density:
                    raise SystemExit(f"could not reach density {density}: {applied}")

            def shoot(name: str, fragment: str, *, full: bool = True) -> None:
                page.goto(server.url + fragment, wait_until="networkidle")
                page.wait_for_timeout(900)
                path = out / f"{name}.png"
                page.screenshot(path=str(path), full_page=full)
                written.append(path.name)

            shoot("console", "#/")
            shoot("compose", "#/compose")
            shoot("graph", "#/graph")

            # Selection: click the hit circle rather than the group, whose
            # bounding box includes the label and therefore centres on a wire.
            page.eval_on_selector(
                ".node .hit",
                "e => e.dispatchEvent(new MouseEvent('click', {bubbles: true}))",
            )
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / "graph-selected.png"), full_page=True)
            written.append("graph-selected.png")

            want(theme="dark")
            page.screenshot(path=str(out / "graph-dark.png"), full_page=True)
            written.append("graph-dark.png")
            want(theme="light")

            # The registers, on the screen built to show the difference. Sorted
            # and with a row selected, because an unsorted grid with no cursor
            # shows none of what makes the dense register a register.
            page.goto(server.url + "#/verbs", wait_until="networkidle")
            page.wait_for_timeout(700)
            page.eval_on_selector_all(
                "table.datagrid th.sortable",
                "els => els[1].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
            )
            page.eval_on_selector_all(
                "table.datagrid tbody tr",
                "els => els[3].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
            )
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / "verbs-dense.png"))
            written.append("verbs-dense.png")

            want(density="reading")
            page.screenshot(path=str(out / "verbs-calm.png"))
            written.append("verbs-calm.png")

            # The dark counterparts of everything the front page embeds. The
            # register is switched back to dense first: `verbs-calm-dark` has to
            # differ from `verbs-dense-dark` by the *register*, and capturing
            # both from whatever state the previous shot left behind is how two
            # images end up identical and nobody notices for a release.
            def dark(name: str, fragment: str, prepare=None) -> None:
                page.goto(server.url + fragment, wait_until="networkidle")
                page.wait_for_timeout(400)
                want(theme="dark", density="bench")
                if prepare:
                    prepare()
                    page.wait_for_timeout(350)
                page.screenshot(path=str(out / f"{name}.png"), full_page=name.startswith("graph"))
                written.append(f"{name}.png")

            dark("graph-selected-dark", "#/graph", lambda: page.eval_on_selector(
                ".node .hit",
                "e => e.dispatchEvent(new MouseEvent('click', {bubbles: true}))",
            ))

            def sorted_and_picked() -> None:
                page.eval_on_selector_all(
                    "table.datagrid th.sortable",
                    "els => els[1].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
                )
                page.eval_on_selector_all(
                    "table.datagrid tbody tr",
                    "els => els[3].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
                )

            dark("verbs-dense-dark", "#/verbs", sorted_and_picked)

            dark("verbs-calm-dark", "#/verbs", lambda: want(density="reading"))

            browser.close()
    finally:
        server.stop()

    real = [problem for problem in problems if "Failed to load resource" not in problem]
    if real:
        # A screenshot of a broken page is worse than no screenshot: it is a
        # published picture of a defect, presented as the product.
        raise SystemExit(f"the interface logged errors while capturing: {real}")

    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    written = capture(args.out)
    print(f"wrote {len(written)} screenshots to {args.out}")
    for name in written:
        print(f"  {name}")


if __name__ == "__main__":
    main()
