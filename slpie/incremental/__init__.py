"""Incremental recomputation — read only what moved.

A full rescan of a large tree is minutes; a change to one file should not cost
them. The chain is four lookups the platform already pays for, and the
`evidence(uri)` index exists for exactly this:

    changed uri  →  evidence drawn from it
                 →  nodes and edges resting on that evidence
                 →  those whose evidence is now *entirely* stale
                 →  enrichments derived from any of them, transitively

===============  ==========================================================
`fingerprint`    what a tree looked like: uri → content digest, and the delta
`invalidation`   what a changed file stops justifying
`watcher`        a baseline that survives the process, and the plan to act on
`errors`         the two modes, and what each refuses to do quietly
===============  ==========================================================

Three decisions carry it. **Content, never mtime**: a `git checkout` rewrites
mtimes on identical files, and a restored build cache writes older ones than are
recorded — the first wastes a full rescan, the second silently skips a file that
really changed. And **a node cited by three files does not die because one
changed**: it is weakened and recomputed, not retired, or every scan would churn
nodes out and straight back in.

And the third, which the engine shipped without: **it never says anything about
a file it did not read.** A fingerprint that quietly dropped an unreadable, an
oversized or an unreached file made `compare()` report it as *removed*, because
absent from a fingerprint is indistinguishable from absent from disk — and a
rescan acting on that retires the graph nodes drawn from files that are still
there. Measured on a ten-file tree: a walk limit of four reported six live files
as removed, and a size limit of zero reported all ten.

So there are two modes, after rope's `force_errors` switch:

* **strict**, the default and what production runs — a tree that could not be
  read in full raises `IncompleteFingerprint` (with the files and reasons) or
  `TruncatedWalk` (with the limit), rather than handing back a delta that looks
  complete and is not. `SLPIE_STRICT=0` turns it off.
* **lenient**, for development — the same facts are recorded and explained in
  detail, and the affected files land in `Delta.unknown`, which is neither
  refreshed nor retired. The graph keeps what it last believed about them, which
  is the only honest answer when nobody looked.

The honest limit is reported rather than discovered: `Plan.proportion` says how
much of the tree moved, and past half of it a full rescan is the cheaper answer.
"""

from __future__ import annotations

from .errors import (
    IncompleteFingerprint,
    IncrementalError,
    Skip,
    SkipReason,
    TruncatedWalk,
    UnreadableFile,
    audit,
    explain,
)
from .fingerprint import Delta, Fingerprint, default_strict, digest_file, excluded
from .invalidation import Invalidation, evidence_for_uris, invalidate
from .watcher import BASELINE, FULL_RESCAN_ABOVE, Plan, Watcher

__all__ = [
    "BASELINE",
    "FULL_RESCAN_ABOVE",
    "Delta",
    "Fingerprint",
    "IncompleteFingerprint",
    "IncrementalError",
    "Invalidation",
    "Plan",
    "Skip",
    "SkipReason",
    "TruncatedWalk",
    "UnreadableFile",
    "Watcher",
    "audit",
    "default_strict",
    "digest_file",
    "evidence_for_uris",
    "excluded",
    "explain",
    "invalidate",
]
