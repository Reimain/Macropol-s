"""One session: a key, a directory, a share of the budget, and a clean ending.

A session is the unit of isolation between concurrent users, and it holds all
four things that have to agree for that isolation to be real:

* its **key**, so its block ids cannot be derived by anyone else;
* its **directory**, so its blocks cannot be read or swept by anyone else;
* its **share of the process budget**, so it cannot starve anyone else;
* its **lifetime**, so its disk is reclaimed whether it ended well or badly.

`hold()` is the whole interface most callers need:

    with session.hold(observations) as records:
        ...                       # `records` is a Sequence, spilled or not

It decides. Under the ceiling the records stay in memory and `records` is the
tuple that was passed in — no encoding, no I/O, no behaviour change at all. Over
the ceiling they are streamed to disk and `records` is a `SpilledSequence` that
still answers `len()`, iteration and indexing. **The caller never branches on
which happened**, which is the property that let this be added without touching a
single verb.

**Cleanup is by context manager, not by finaliser.** `__del__` runs at an
interpreter's convenience, and under load "eventually" means a disk filling up
while a worker is still serving. `close()` is idempotent and the context manager
calls it on the way out of a failure as readily as a success.

The failure mode worth naming: a process killed hard leaves its directory behind.
That is why `sweep_stale` exists and why a store is a directory of dated
directories rather than one flat pile — an operator, or the next process to
start, can reclaim what a dead one left without knowing anything about it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterable, Iterator, Sequence

from .budget import DEFAULT_CEILING, Budget
from .codec import SpillError, Unspillable, spillable
from .ident import new_session_key
from .sequence import WINDOW, SpilledSequence
from .store import FileStore, SpillStore

#: Sessions idle longer than this are fair game for `sweep_stale`. Long enough
#: that a slow scan is never swept from under itself; short enough that a
#: crashed worker's disk comes back the same day.
STALE_SECONDS = 6 * 3600


def _default_name() -> str:
    """A session name unique across processes on one machine.

    Process id *and* a monotonic counter *and* random hex: pids are reused, two
    threads can ask in the same microsecond, and a collision here would put two
    sessions in one directory — which is precisely the isolation failure this
    package exists to prevent.
    """
    return f"s{os.getpid():d}-{time.monotonic_ns():x}-{os.urandom(4).hex()}"


@dataclass(slots=True)
class SessionReport:
    """What one session is holding."""

    name: str
    held: int
    spilled_records: int
    spilled_bytes: int
    blocks: int
    closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.name, "held": self.held,
            "spilled_records": self.spilled_records,
            "spilled_bytes": self.spilled_bytes,
            "blocks": self.blocks, "closed": self.closed,
        }

    def __str__(self) -> str:
        return (
            f"{self.name}: {self.spilled_records:,} record(s) spilled across "
            f"{self.blocks} block(s), {self.spilled_bytes / 1e6:.1f} MB"
        )


class SpillSession:
    """One caller's isolated share of memory and disk."""

    def __init__(
        self,
        *,
        name: str = "",
        store: SpillStore | None = None,
        budget: Budget | None = None,
        chunk: int = WINDOW,
    ) -> None:
        self.name = name or _default_name()
        self.store = store if store is not None else FileStore()
        # Shared by default. A private budget per session is not a limit: twenty
        # sessions each promised 128 MB is a 2.5 GB promise on a machine that
        # was never asked.
        self.budget = budget if budget is not None else shared_budget()
        self.chunk = max(1, int(chunk))
        self._key = new_session_key()
        self._closed = False
        self._held = 0
        self._spilled_records = 0
        self._spilled_bytes = 0
        self._blocks = 0
        self._refusals: list[str] = []

    # -- the interface that matters --------------------------------------

    def hold(self, records: Iterable[Any]) -> "_Held":
        """Keep `records` reachable, in memory or on disk. The caller cannot tell."""
        return _Held(self, records)

    def keep(self, records: Iterable[Any]) -> Sequence[Any]:
        """Decide once and return the sequence. `hold` is this plus cleanup.

        **Spills when the process is actually short of memory, not when the
        input is merely large.** The first design spilled anything past one
        chunk, which meant a five-thousand-record scan wrote to disk on an idle
        worker with gigabytes free — paying I/O for a bound nobody needed, and
        making the fast path the uncommon one.

        So admission is incremental: records accumulate in memory, and every
        `chunk` of them the budget is asked whether that much more may be held.
        While it says yes nothing touches the disk and the caller gets the plain
        tuple it would have got before this tier existed. The first refusal is
        the moment this process is genuinely near its ceiling, and *that* is
        when everything collected so far, plus everything still coming, goes to
        disk in one streamed pass.
        """
        self._require_open()

        iterator = iter(records)
        buffered: list[Any] = []
        claimed = 0

        while True:
            batch = list(_take(iterator, self.chunk))
            if not batch:
                # Input exhausted and never refused: it all fits.
                return tuple(buffered)

            cost = Budget.estimate(batch) * len(batch)
            if not self.budget.admit(self.name, cost):
                # Under pressure. Release what this call had claimed — it is
                # about to live on disk instead — and spill the whole thing.
                if claimed:
                    self.budget.release(self.name, claimed)
                    self._held = max(0, self._held - claimed)
                from itertools import chain

                return self._spill(chain(buffered, batch, iterator))

            claimed += cost
            self._held += cost
            buffered.extend(batch)

    def _spill(self, stream: Iterator[Any]) -> Sequence[Any]:
        """Stream everything to disk, or say why it could not be.

        The spillability check runs on a *buffered head* before anything is
        written. Discovering mid-stream that a record cannot be encoded would
        leave part of the input already in blocks and the rest still in the
        iterator, and no correct recovery exists from there: falling back to
        memory would silently drop everything already consumed.
        """
        from itertools import chain

        head = list(_take(stream, 64))
        unspillable = next(
            (item for item in head if not spillable(item)), _MISSING,
        )
        if unspillable is not _MISSING:
            # Kept in memory, unbounded, and *reported* — a value that cannot be
            # written back faithfully must not be silently claimed as bounded.
            self._refusals.append(str(Unspillable(unspillable)))
            return tuple(chain(head, stream))

        spilled = SpilledSequence.of(
            chain(head, stream), store=self.store, session=self.name,
            key=self._key, chunk=self.chunk,
        )
        self._spilled_records += len(spilled)
        self._spilled_bytes += spilled.bytes
        self._blocks += len(spilled.blocks)
        self.budget.record_spill(spilled.bytes)
        return spilled

    # -- lifecycle -------------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise SpillError(
                f"session {self.name} is closed; its blocks have been swept. "
                f"Reusing a closed session would read files that are gone"
            )

    def close(self) -> int:
        """Release the memory and delete the disk. Idempotent."""
        if self._closed:
            return 0
        self._closed = True
        self.budget.release(self.name)
        self._held = 0
        return self.store.sweep(self.name)

    def __enter__(self) -> "SpillSession":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.close()

    # -- inspection ------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def refusals(self) -> tuple[str, ...]:
        """Values that could not be spilled, and therefore are not bounded."""
        return tuple(self._refusals)

    def report(self) -> SessionReport:
        return SessionReport(
            name=self.name, held=self._held,
            spilled_records=self._spilled_records,
            spilled_bytes=self._spilled_bytes,
            blocks=self._blocks, closed=self._closed,
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SpillSession {self.name} {'closed' if self._closed else 'open'}>"


class _Missing:
    """A sentinel distinct from `None`, which is a legitimate record."""


_MISSING = _Missing()


def _take(iterator: Iterator[Any], count: int) -> Iterator[Any]:
    """The next `count` items. Bounded, so a batch is never the whole input."""
    from itertools import islice

    return islice(iterator, count)


class _Held:
    """The context manager `hold()` returns."""

    __slots__ = ("_session", "_records", "_sequence")

    def __init__(self, session: SpillSession, records: Iterable[Any]) -> None:
        self._session = session
        self._records = records
        self._sequence: Sequence[Any] | None = None

    def __enter__(self) -> Sequence[Any]:
        self._sequence = self._session.keep(self._records)
        return self._sequence

    def __exit__(self, *_exception: Any) -> None:
        # Releases the resident window and the budget claim, and leaves the
        # blocks — the session owns those and sweeps them on close. Dropping
        # them here would break a caller that kept the sequence.
        if isinstance(self._sequence, SpilledSequence):
            self._sequence.release()


# --- the process-wide budget ---------------------------------------------

_SHARED: Budget | None = None


def shared_budget(ceiling: int = 0) -> Budget:
    """The one budget every session draws from, created on first use.

    A module-level singleton rather than a parameter threaded through every
    call, because the thing being bounded *is* process-wide: a limit each caller
    could opt out of by passing their own would bound nothing.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = Budget(ceiling or _configured_ceiling())
    return _SHARED


def reset_shared_budget(ceiling: int = 0) -> Budget:
    """Replace the shared budget. For tests, and for a worker re-configuring."""
    global _SHARED
    _SHARED = Budget(ceiling or _configured_ceiling())
    return _SHARED


def _configured_ceiling() -> int:
    """`SLPIE_MEMORY_CEILING` in bytes, or the default.

    Environment rather than a config file: the ceiling has to be settable by
    whatever started the process — a container limit, a systemd unit, a Celery
    worker — none of which has a manifest to read.
    """
    raw = os.environ.get("SLPIE_MEMORY_CEILING", "").strip()
    if not raw:
        return DEFAULT_CEILING
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CEILING
    return value if value > 0 else DEFAULT_CEILING


def sweep_stale(
    store: SpillStore | None = None, *, older_than: int = STALE_SECONDS,
) -> int:
    """Reclaim directories left behind by processes that did not close.

    A hard kill cannot run cleanup, so something has to. Age is taken from the
    directory's own mtime, and a session still writing keeps touching it — so an
    active session is never swept from under itself however long it runs.
    """
    active = store if store is not None else FileStore()
    root = getattr(active, "root", None)
    if root is None:
        return 0

    cutoff = time.time() - max(0, older_than)
    reclaimed = 0
    for name in active.sessions():
        directory = root / name
        try:
            if directory.stat().st_mtime < cutoff:
                reclaimed += active.sweep(name)
        except OSError:
            # Another process swept it between listing and stat. That is the
            # intended outcome, not an error.
            continue
    return reclaimed
