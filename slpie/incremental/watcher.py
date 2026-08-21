"""Noticing that a tree moved, and recomputing only the part that did.

`Watcher` is the whole incremental engine: it holds a baseline fingerprint,
compares a tree against it, and reports what a rescan would have to do before
doing any of it. That ordering is deliberate — `plan()` costs a fingerprint pass
and nothing else, so a caller can ask "is this worth doing" without paying for
the answer.

**Polling, not inotify.** A filesystem event API would be faster to notice a
change, and it is the wrong tool here for three reasons: it is per-platform, it
silently drops events under load on every platform that has it, and it cannot
answer "what changed while this process was not running" — which is the ordinary
case for a CI job or a worker that just started. A content fingerprint answers
that question the same way whether the process watched or not, and being right
after a restart matters more than noticing within a millisecond.

**A baseline is a file, so it survives the process.** `slpie scan` writes one;
the next `slpie scan` reads it and does a fraction of the work. Without that, an
incremental engine only helps a long-running daemon, which is not how most of
this platform is used.

The honest limit, stated rather than discovered: **a rescan is only as
incremental as the graph lets it be.** Retiring and re-asserting nodes is cheap;
recomputing enrichments whose `derived_from` intersects the retired set is
proportional to how connected the graph is, and a change to a file every module
imports invalidates most of it. `Plan.proportion` reports that up front, so a
caller can see when a full rescan would be cheaper than pretending otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import Skip, explain
from .fingerprint import Delta, Fingerprint
from .invalidation import Invalidation, invalidate

#: Where a baseline lives by default, relative to the tree being watched. Beside
#: the routines and the acceptance ledger, so one directory holds everything
#: this platform keeps about a checkout.
BASELINE = Path(".slpie") / "fingerprint.json"

#: Past this share of files touched, a full rescan is the cheaper answer: the
#: bookkeeping to retire and re-derive most of a graph costs more than building
#: it once. Reported rather than enforced — the caller decides.
FULL_RESCAN_ABOVE = 0.5


@dataclass(frozen=True, slots=True)
class Plan:
    """What a rescan would do, computed before any of it is done."""

    delta: Delta
    invalidation: Invalidation
    total_files: int = 0
    duration: float = 0.0
    #: Why files were not read, in full. Only ever non-empty in lenient mode —
    #: strict raises instead of planning over a tree it could not read.
    skipped: tuple[Skip, ...] = ()
    #: Whether the walk stopped at its file limit, in which case it can vouch
    #: for nothing beyond what it read.
    truncated: bool = False

    @property
    def trustworthy(self) -> bool:
        """Whether this plan accounts for every file it was asked about."""
        return self.delta.trustworthy

    @property
    def proportion(self) -> float:
        """Share of the tree that moved. The number that decides the strategy."""
        if not self.total_files:
            return 0.0
        return round(self.delta.touched / self.total_files, 4)

    @property
    def worth_it(self) -> bool:
        """Whether incremental beats a full rescan.

        False when nothing changed *and* when almost everything did — the first
        because there is no work, the second because the bookkeeping to retire
        and re-derive most of a graph costs more than rebuilding it.
        """
        return not self.delta.empty and self.proportion <= FULL_RESCAN_ABOVE

    @property
    def caveat(self) -> str:
        """What this plan could not see. Empty when it read the whole tree."""
        if self.trustworthy:
            return ""
        cause = (
            "the walk stopped at its file limit"
            if self.truncated else "they could not be read on this pass"
        )
        return (
            f"{len(self.delta.unknown)} file(s) the baseline knew about are "
            f"unaccounted for ({cause}), so this plan leaves them alone rather "
            f"than retiring what they justify — run with SLPIE_STRICT=1 to "
            f"refuse instead of continuing"
        )

    @property
    def unread_detail(self) -> str:
        """Why files were not read, grouped — the development mode's whole point."""
        if self.truncated:
            return (
                f"  walk_limit: stopped after {self.total_files} file(s); "
                f"whatever lies beyond was not reached and cannot be named"
            )
        return explain(self.skipped)

    @property
    def reason(self) -> str:
        if self.delta.empty:
            base = "nothing changed since the baseline; there is no work to do"
        elif self.proportion > FULL_RESCAN_ABOVE:
            base = (
                f"{self.proportion:.0%} of the tree moved; retiring and "
                f"re-deriving that much costs more than one full rescan"
            )
        else:
            base = (
                f"{self.delta.touched} of {self.total_files} file(s) moved "
                f"({self.proportion:.1%}); rescanning only those"
            )
        return f"{base} — {self.caveat}" if self.caveat else base

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta": self.delta.to_dict(),
            "invalidation": self.invalidation.to_dict(),
            "total_files": self.total_files,
            "proportion": self.proportion,
            "worth_it": self.worth_it,
            "reason": self.reason,
            "duration": round(self.duration, 4),
            "trustworthy": self.trustworthy,
            "truncated": self.truncated,
            "skipped": [item.to_dict() for item in self.skipped],
        }

    def render(self) -> str:
        lines = [
            "",
            f"  {self.delta}",
            f"  {self.reason}",
            "",
        ]
        if not self.invalidation.empty:
            lines.append(f"  {self.invalidation}")
            for uri in self.delta.to_read[:8]:
                lines.append(f"    re-read  {uri.rsplit('/', 1)[-1]}")
            for uri in self.delta.removed[:4]:
                lines.append(f"    retire   {uri.rsplit('/', 1)[-1]}")
            lines.append("")
        if self.delta.unknown:
            # Outside the invalidation gate on purpose. A pass that read nothing
            # has no work to show and is exactly the pass whose unread list
            # matters most — gating it there hid the whole point.
            for uri in self.delta.unknown[:4]:
                lines.append(f"    unread   {uri.rsplit('/', 1)[-1]}  (left alone)")
            lines.append("")
        if self.skipped or self.truncated:
            # The development half of the two modes: strict would have raised
            # with this list; lenient prints it, because a developer who chose to
            # continue still needs to know what they are continuing without.
            lines.append("  files not read:")
            lines.append(self.unread_detail)
            lines.append("")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.reason


class Watcher:
    """A tree, its baseline, and what has moved since."""

    def __init__(
        self,
        root: str | Path,
        *,
        baseline: str | Path | None = None,
        strict: bool | None = None,
        limit: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.baseline_path = Path(
            baseline if baseline is not None else self.root / BASELINE
        )
        #: `None` means "ask the environment at walk time" rather than "lenient",
        #: so a watcher constructed before `SLPIE_STRICT` is set still honours it.
        self.strict = strict
        #: The two budgets whose exhaustion produces an unsafe skip. Exposed here
        #: because a `walk_limit` or `too_large` skip is only actionable if the
        #: caller who was told about it can raise the budget that caused it.
        self.limit = limit
        self.max_bytes = max_bytes
        self._current: Fingerprint | None = None

    def _bounds(self) -> dict[str, int]:
        """The overridden budgets only — so `Fingerprint.of` keeps its defaults."""
        given = {"limit": self.limit, "max_bytes": self.max_bytes}
        return {key: value for key, value in given.items() if value is not None}

    # -- reading ---------------------------------------------------------

    def baseline(self) -> Fingerprint:
        """What the tree looked like last time. Empty when there is no baseline."""
        return Fingerprint.load(self.baseline_path)

    def current(self, *, refresh: bool = False) -> Fingerprint:
        """What the tree looks like now. Cached, because a plan reads it twice.

        The baseline is excluded from its own fingerprint. Writing it inside the
        watched tree — which is the default, under `.slpie/` — otherwise made
        every commit dirty the tree it had just recorded as clean, so the run
        after a commit always reported one changed file and never converged.
        """
        if self._current is None or refresh:
            whole = Fingerprint.of(
                self.root, strict=self.strict, **self._bounds()
            )
            baseline = self.baseline_path.resolve().as_uri()
            self._current = Fingerprint(
                digests={
                    uri: value for uri, value in whole.digests.items()
                    if uri != baseline
                },
                root=whole.root,
                # Carried through, not dropped. These are what stop `compare()`
                # calling an unread file removed, and rebuilding the fingerprint
                # without them would reintroduce the defect one level up.
                skipped=whole.skipped, truncated=whole.truncated,
                excluded=whole.excluded,
            )
        return self._current

    def delta(self, *, refresh: bool = False) -> Delta:
        return self.current(refresh=refresh).compare(self.baseline())

    # -- planning --------------------------------------------------------

    def plan(self, graph: Any = None, *, enrichments: Any = None) -> Plan:
        """What a rescan would do. Costs a fingerprint pass and nothing else.

        The graph is optional: without one this reports the file delta, which is
        already the useful half on a checkout nobody has scanned yet. With one it
        also reports exactly which nodes, edges and enrichments stop being true.
        """
        started = time.monotonic()
        current = self.current(refresh=True)
        delta = current.compare(self.baseline())

        found = (
            invalidate(
                graph, delta.changed, removed=delta.removed,
                enrichments=enrichments or {},
            )
            if graph is not None and not delta.empty
            else Invalidation(rescan=delta.to_read)
        )

        return Plan(
            delta=delta, invalidation=found, total_files=len(current),
            duration=time.monotonic() - started, skipped=current.skipped,
            truncated=current.truncated,
        )

    # -- committing ------------------------------------------------------

    def commit(self, fingerprint: Fingerprint | None = None) -> Path:
        """Record the current state as the new baseline.

        Called *after* a rescan succeeds, never before. Committing first would
        mean a scan that died halfway left a baseline claiming work that never
        happened, and the next run would skip exactly the files the failed one
        did not finish.
        """
        return (fingerprint or self.current()).save(self.baseline_path)

    def forget(self) -> bool:
        """Drop the baseline, so the next scan is a full one."""
        try:
            self.baseline_path.unlink()
            return True
        except FileNotFoundError:
            return False

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Watcher {self.root} baseline={self.baseline_path.name}>"
