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
===============  ==========================================================

Two decisions carry it. **Content, never mtime**: a `git checkout` rewrites
mtimes on identical files, and a restored build cache writes older ones than are
recorded — the first wastes a full rescan, the second silently skips a file that
really changed. And **a node cited by three files does not die because one
changed**: it is weakened and recomputed, not retired, or every scan would churn
nodes out and straight back in.

The honest limit is reported rather than discovered: `Plan.proportion` says how
much of the tree moved, and past half of it a full rescan is the cheaper answer.
"""

from __future__ import annotations

from .fingerprint import Delta, Fingerprint, digest_file
from .invalidation import Invalidation, evidence_for_uris, invalidate
from .watcher import BASELINE, FULL_RESCAN_ABOVE, Plan, Watcher

__all__ = [
    "BASELINE",
    "FULL_RESCAN_ABOVE",
    "Delta",
    "Fingerprint",
    "Invalidation",
    "Plan",
    "Watcher",
    "digest_file",
    "evidence_for_uris",
    "invalidate",
]
