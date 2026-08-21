"""Where rendered files land, and the one rule about landing them.

Separated from the emitters because *they* return text and know nothing about
disks, which is what makes them testable without one. This module is the only
thing in the package that writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

#: The conventional destination. Named once so the manual, the CLI help and the
#: verb's default cannot disagree about where files go.
DEFAULT_OUTPUT = "./deploy"


def write(files: Mapping[str, str], destination: str | Path) -> tuple[str, ...]:
    """Write rendered files under `destination`, returning what was written.

    Parent directories are created; existing files are overwritten, because a
    render is a projection of the manifest and refusing to overwrite would make
    the second render a manual merge. That is safe *because* nothing here is
    hand-edited — every emitted file says so in its first three lines.
    """
    root = Path(destination)
    written = []
    for name, text in sorted(files.items()):
        target = root / name
        # A rendered path is emitter-controlled, not user-controlled, and every
        # one of them is a literal in this package. Resolving anyway costs
        # nothing and means a future emitter cannot write outside the tree by
        # accident.
        resolved = target.resolve()
        if not str(resolved).startswith(str(root.resolve())):
            raise ValueError(f"emitted path {name!r} escapes {destination}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(str(target))
    return tuple(written)
