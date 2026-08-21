"""Emit the landing page — as a linked page for the site, or self-contained.

    python -m tools.ui.landing --out docs/_build/html/start/index.html
    python -m tools.ui.landing --out /tmp/page.html --inline

The source lives in `docs/_landing/` with ordinary relative image paths, which
is what the published site wants. `--inline` rewrites those to data URIs so the
page is a single file with no companions — needed anywhere it travels alone.

One source, two outputs. The alternative was a second copy of the markup for the
standalone case, which would be wrong in a different way within a release.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "_landing"
#: The screenshots live in one place — where the capture tool writes them.
#: `docs/_landing/` used to hold its own copies, which meant regenerating
#: the screenshots left the front page quietly showing the old ones.
SHOTS = ROOT / "docs" / "_static" / "ui"
DEFAULT_OUT = ROOT / "docs" / "_build" / "html" / "start" / "index.html"


def inline(html: str, base: Path) -> str:
    """Every local image, as a data URI."""

    def swap(match: re.Match) -> str:
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        path = (SHOTS / Path(src).name).resolve() if src.startswith("assets/") \
            else (base / src).resolve()
        if not path.is_file():
            raise SystemExit(f"the page references {src}, which is not there")
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:{kind};base64,{payload}"'

    return re.sub(r'src="([^"]+)"', swap, html)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--inline", action="store_true",
                        help="embed images as data URIs; emit one file")
    args = parser.parse_args(argv)

    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.inline:
        args.out.write_text(inline(html, SOURCE), encoding="utf-8")
    else:
        args.out.write_text(html, encoding="utf-8")
        # Copy only what the page actually references, from the canonical
        # store. Copying the whole folder would ship every screenshot the
        # documentation uses, including the ones this page does not.
        target = args.out.parent / "assets"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for name in sorted(set(re.findall(r'src="assets/([^"]+)"', html))):
            shutil.copy2(SHOTS / name, target / name)

    print(f"wrote {args.out}  ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
