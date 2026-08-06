"""Turn the specs into `.ipynb` files.

Deterministic by construction: no timestamps, no random ids, no execution counts.
Running this twice over an unchanged spec produces byte-identical notebooks, so
`git diff` after a rebuild shows what actually changed rather than churn — the
same property the platform's own snapshot digests have, for the same reason.

    python -m tools.notebooks.build            # write notebooks/
    python -m tools.notebooks.build --check     # fail if they are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .spec import NOTEBOOKS, Cell, Notebook

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "notebooks"

#: Pinned. `nbformat` writes `4.5` by default and bumps the minor version
#: between releases, which would rewrite every notebook on a library upgrade and
#: make the diff meaningless.
NBFORMAT = (4, 5)


def _identifier(notebook: Notebook, position: int) -> str:
    """A stable cell id.

    nbformat 4.5 requires one. Derived from the notebook and the cell's position
    rather than randomly, because a random id would change on every rebuild and
    put a full-file diff in front of a reviewer who changed one word.
    """
    return f"{notebook.slug}-{position:02d}"


def _cell(cell: Cell, notebook: Notebook, position: int) -> dict:
    source = cell.source.splitlines(keepends=True)
    common = {"id": _identifier(notebook, position), "metadata": {}}
    if cell.kind == "markdown":
        return {"cell_type": "markdown", **common, "source": source}
    return {
        "cell_type": "code",
        **common,
        # Null rather than 0: a committed execution count is state, and it
        # changes on every run whether or not the code did.
        "execution_count": None,
        "outputs": [],
        "source": source,
    }


def render(notebook: Notebook) -> dict:
    return {
        "cells": [
            _cell(cell, notebook, index)
            for index, cell in enumerate(notebook.cells)
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            "slpie": {"number": notebook.number, "title": notebook.title},
        },
        "nbformat": NBFORMAT[0],
        "nbformat_minor": NBFORMAT[1],
    }


def serialise(notebook: Notebook) -> str:
    return json.dumps(render(notebook), indent=1, ensure_ascii=False) + "\n"


def build(*, check: bool = False) -> int:
    OUT.mkdir(exist_ok=True)
    stale: list[str] = []

    for notebook in NOTEBOOKS:
        target = OUT / notebook.filename
        body = serialise(notebook)
        if check:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != body:
                stale.append(notebook.filename)
            continue
        target.write_text(body, encoding="utf-8")
        cells = len(notebook.cells)
        code = sum(1 for c in notebook.cells if c.kind == "code")
        print(f"  {notebook.filename:34} {cells:2} cells ({code} runnable)")

    if check and stale:
        print("these notebooks are stale — run `make notebooks`:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        return 1
    if check:
        print(f"{len(NOTEBOOKS)} notebooks are up to date with the spec")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="do not write; exit 1 if any notebook differs from its spec",
    )
    args = parser.parse_args(argv)
    return build(check=args.check)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
