"""The spill tier — bounded memory, isolated sessions, content-addressed blocks.

Data-intensive scans do not fail gracefully by default: observations accumulate
linearly with no ceiling, and every concurrent user multiplies the total. A large
monorepo is an out-of-memory kill, and the kill takes every other request in the
worker with it.

This tier gives that a ceiling without any verb being rewritten:

===============  ===========================================================
`ident`          fixed-length, content-derived, session-keyed block ids
`codec`          lossless encoding, or an explicit refusal
`store`          where blocks live; atomic writes, one directory per session
`budget`         one process-wide ceiling every session draws from
`sequence`       records on disk, addressed like a tuple
`session`        the isolation boundary, and a clean ending
===============  ===========================================================

The load-bearing property is **substitutability**. `SpillSession.keep` returns
either the tuple it was given or a `SpilledSequence`, and both satisfy the
`Sequence` protocol completely — iteration, `len()`, indexing, slicing,
re-iteration. No caller branches on which it got, which is why this was added
without touching a verb.

Three commitments, each the reason for a design decision rather than a slogan:

**It is lossless or it refuses.** An OOM kill is loud and obviously a failure; a
lossy spill produces a complete-looking answer built on records that quietly lost
a field. `codec` refuses what it cannot reconstruct and the value stays in
memory, unbounded and *reported as such*.

**Sessions cannot reach each other.** Block ids are keyed hashes of content, so
the same bytes in two sessions produce different ids: one session cannot derive
another's ids, collide with them, or sweep them.

**Degrading beats dying.** The budget is process-wide, so under load everybody
spills and gets slower rather than one request being killed and taking the worker
with it.
"""

from __future__ import annotations

from .budget import Budget, BudgetReport, DEFAULT_CEILING
from .codec import SpillError, Unspillable, decode, encode, spillable
from .ident import LENGTH, block_id, is_block_id, new_session_key, require_block_id
from .sequence import BlockRef, SpilledSequence, write_block
from .session import (
    STALE_SECONDS,
    SessionReport,
    SpillSession,
    reset_shared_budget,
    shared_budget,
    sweep_stale,
)
from .store import FileStore, SpillStore, StoreReport, require_session

__all__ = [
    "DEFAULT_CEILING",
    "LENGTH",
    "STALE_SECONDS",
    "BlockRef",
    "Budget",
    "BudgetReport",
    "FileStore",
    "SessionReport",
    "SpillError",
    "SpillSession",
    "SpillStore",
    "SpilledSequence",
    "StoreReport",
    "Unspillable",
    "block_id",
    "decode",
    "encode",
    "is_block_id",
    "new_session_key",
    "require_block_id",
    "require_session",
    "reset_shared_budget",
    "shared_budget",
    "spillable",
    "sweep_stale",
    "write_block",
]
