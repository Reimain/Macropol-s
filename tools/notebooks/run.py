"""Execute every notebook, and fail if any cell raises.

This is the reason the notebooks are worth having. A notebook nobody runs is a
blog post that has learned to lie: it looks executable, so a reader assumes it
was checked. Running them in CI is what makes "every cell works" a fact rather
than an intention.

    python -m tools.notebooks.run                  # all of them
    python -m tools.notebooks.run 01 07            # by number
    python -m tools.notebooks.run --write          # keep the outputs

By default outputs are **discarded** after the run. Committed outputs would put
temp-directory paths and timings into the diff of every rebuild, and the thing
worth committing is the source of a page that is known to run — not one run's
transcript. `--write` exists for producing a rendered copy to publish.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
NOTEBOOKS = ROOT / "notebooks"

#: Generous. The discovery notebooks materialise real trees and the graph one
#: builds ten thousand nodes; a CI runner under load is slower than a laptop,
#: and a timeout that fires on a slow machine is a flaky failure that teaches
#: people to re-run rather than to read.
TIMEOUT = 600


def run_one(path: Path, *, write: bool) -> tuple[bool, str, float]:
    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    started = time.monotonic()
    notebook = nbformat.read(path, as_version=4)
    client = NotebookClient(
        notebook,
        timeout=TIMEOUT,
        kernel_name="python3",
        # Run with the notebook's own directory as cwd so a relative path in a
        # cell means the same thing here as it does for a reader who opened it.
        resources={"metadata": {"path": str(path.parent)}},
        allow_errors=False,
    )
    try:
        client.execute()
    except CellExecutionError as error:
        return False, _first_error(error), time.monotonic() - started
    except Exception as error:  # noqa: BLE001 - a kernel can fail in many ways
        return False, f"{type(error).__name__}: {error}", time.monotonic() - started

    if write:
        nbformat.write(notebook, path)
    return True, "", time.monotonic() - started


def _first_error(error: Exception) -> str:
    """The traceback's last line, which is the one naming what actually failed."""
    text = str(error)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if line and not line.startswith(("-", "~", "^")):
            return line[:300]
    return text[:300]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("only", nargs="*", help="notebook numbers, e.g. 01 07")
    parser.add_argument("--write", action="store_true",
                        help="save executed outputs back into the notebooks")
    args = parser.parse_args(argv)

    paths = sorted(NOTEBOOKS.glob("*.ipynb"))
    if args.only:
        wanted = {value.zfill(2) for value in args.only}
        paths = [p for p in paths if p.name[:2] in wanted]

    if not paths:
        print("no notebooks matched — has `make notebooks` been run?", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    print(f"executing {len(paths)} notebook(s)\n")
    for path in paths:
        ok, detail, elapsed = run_one(path, write=args.write)
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {path.name:34} {elapsed:6.1f}s")
        if not ok:
            print(f"       {detail}")
            failures.append((path.name, detail))

    print()
    if failures:
        print(f"{len(failures)} of {len(paths)} notebooks failed:", file=sys.stderr)
        for name, detail in failures:
            print(f"  {name}: {detail}", file=sys.stderr)
        return 1
    print(f"all {len(paths)} notebooks ran, every cell")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
