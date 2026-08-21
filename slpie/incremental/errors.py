"""What the incremental engine refuses to do quietly.

Modelled on `rope/base/exceptions.py`: a flat hierarchy under one base, every
class documented, and **an exception carries its fields rather than only a
message**. `ModuleSyntaxError(filename, lineno, message)` in rope lets a caller
act on `e.lineno` instead of parsing prose out of `str(e)`; the same applies
here, where a caller wants to know *which* files were not read and *why* before
deciding whether to continue.

The reason this module exists at all is a defect the engine shipped with. A
fingerprint silently dropped any file it could not read, was told not to read, or
ran out of budget for — and `compare()` then reported those files as **removed**,
because absent from the fingerprint is indistinguishable from absent from disk.
A rescan acting on that delta retires the nodes drawn from files that are still
there and perfectly fine. Measured on a ten-file tree: a walk limit of four
reported six live files as removed, and a size limit of zero reported all ten,
which would have retired the entire graph.

So a skip is now a **recorded fact with a reason**, and there are two modes,
following rope's `force_errors` switch:

* **strict** — the default, and what a production scan should run. A tree that
  could not be read in full raises, carrying the detail. A graph is not built on
  a partial reading of the tree without somebody saying so.
* **lenient** — for development, where a permission-denied file in somebody's
  home directory should not stop the run. It records every skip with the detail
  needed to fix it, and `Delta` reports those files as *unknown* rather than as
  removed, so a rescan never acts on them either way.

**Two failures, two exceptions, because they scale differently.** A file that is
unreadable or over budget is named individually: real trees hold a handful, and a
developer needs the list. A walk that exhausts its file limit is *not* named
file-by-file — a 200,000-file limit over a two-million-file tree would allocate
1.8 million records to describe a walk that stopped, which is the memory failure
the spill tier exists to prevent. `TruncatedWalk` carries the limit and the count,
and `compare()` degrades honestly instead: after a truncated walk nothing can be
told apart from removal, so nothing is retired.

Note what is *not* a skip here. A path excluded by policy — a `node_modules` the
operator configured away — is the walk doing exactly what it was told, and the
graph holds nothing derived from it, so there is nothing to warn about and no
reason to allocate a record per file. The one case where an exclusion does matter
is a *policy change*, and `Fingerprint.compare()` catches that by testing the
previous fingerprint's uris against the current policy — which costs nothing per
excluded file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from ..errors import SlpieError


class IncrementalError(SlpieError):
    """Base for everything the incremental engine refuses."""


class SkipReason(str, Enum):
    """Why one named file was not read.

    Both reasons mean the same thing to a delta: the engine wanted to read this
    file and could not, so it knows nothing about it — and treating "I did not
    look" as "it is gone" is how a rescan retires nodes for files that are still
    there.
    """

    TOO_LARGE = "too_large"      # over the size budget
    UNREADABLE = "unreadable"    # an OS error stating, opening or reading it


@dataclass(frozen=True, slots=True)
class Skip:
    """One file the fingerprint did not read, and why."""

    uri: str
    reason: SkipReason
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri, "reason": self.reason.value, "detail": self.detail,
        }

    def explain(self) -> str:
        """One line, written for whoever has to decide what to do about it."""
        name = self.uri.rsplit("/", 1)[-1] or self.uri
        return (
            f"{name}: {self.reason.value}"
            + (f" — {self.detail}" if self.detail else "")
        )

    def __str__(self) -> str:
        return self.explain()


class IncompleteFingerprint(IncrementalError):
    """Named files could not be read, so a delta over this tree is not trustworthy.

    Carries `root` and `skipped` so a caller can act on the list rather than
    parse it out of the message: a CI job might raise the size budget, a
    developer might fix a permission, and a batch job might decide the list is
    acceptable and re-run in lenient mode.
    """

    def __init__(self, root: str, skipped: Sequence[Skip]) -> None:
        self.root = root
        self.skipped = tuple(skipped)
        self.reasons = tuple(sorted({item.reason.value for item in self.skipped}))

        shown = "\n".join(f"  - {item.explain()}" for item in self.skipped[:8])
        more = (
            f"\n  … and {len(self.skipped) - 8} more"
            if len(self.skipped) > 8 else ""
        )
        super().__init__(
            f"{len(self.skipped)} file(s) under {root} could not be read "
            f"({', '.join(self.reasons)}), so this fingerprint does not describe "
            f"the whole tree.\n{shown}{more}\n\n"
            f"A delta computed from it would report those files as *removed*, "
            f"and a rescan acting on that would retire the graph nodes drawn "
            f"from files that are still there.\n"
            f"Fix the cause, or run in lenient mode (`--lenient`, "
            f"`SLPIE_STRICT=0`, or `strict=False`) to record them as unknown "
            f"and leave what they justify alone."
        )


class TruncatedWalk(IncrementalError):
    """The walk hit its file limit, so it does not know what it did not reach.

    Separate from `IncompleteFingerprint` because it cannot name the files it
    missed — it stopped rather than continuing to record them, and recording
    them was the alternative that runs a large monorepo out of memory.
    """

    def __init__(self, root: str, limit: int) -> None:
        self.root = root
        self.limit = limit
        super().__init__(
            f"the walk of {root} stopped at its limit of {limit} file(s), so it "
            f"cannot say what lies beyond it.\n\n"
            f"A delta computed from it would report every unreached file as "
            f"*removed*, and a rescan acting on that would retire most of the "
            f"graph.\n"
            f"Raise the limit, narrow the root, or run in lenient mode "
            f"(`--lenient`, `SLPIE_STRICT=0`, or `strict=False`) to treat "
            f"everything it did not reach as unknown."
        )


class UnreadableFile(IncrementalError):
    """One file could not be read, named individually.

    Raised where a caller asked about a specific file rather than a tree, so the
    list form would be noise.
    """

    def __init__(self, uri: str, detail: str = "") -> None:
        self.uri = uri
        self.detail = detail
        super().__init__(
            f"cannot read {uri}" + (f": {detail}" if detail else "")
        )


def audit(root: str, skipped: Iterable[Skip], *, strict: bool) -> tuple[Skip, ...]:
    """Check a fingerprint's skips. Raises in strict mode, records in lenient.

    One place, so the rule cannot be applied differently by two callers — which
    is how a "strict" mode ends up strict in the path somebody tested and lenient
    in the one they did not.
    """
    found = tuple(skipped)
    if found and strict:
        raise IncompleteFingerprint(root, found)
    return found


def explain(skipped: Sequence[Skip]) -> str:
    """Every skip, grouped by reason — what lenient mode shows a developer."""
    if not skipped:
        return "  every file was read"

    grouped: dict[SkipReason, list[Skip]] = {}
    for item in skipped:
        grouped.setdefault(item.reason, []).append(item)

    lines: list[str] = []
    for reason in sorted(grouped, key=lambda item: item.value):
        items = grouped[reason]
        lines.append(f"  {reason.value}: {len(items)} file(s)")
        for item in items[:5]:
            lines.append(f"      {item.explain()}")
        if len(items) > 5:
            lines.append(f"      … and {len(items) - 5} more")
    return "\n".join(lines)
