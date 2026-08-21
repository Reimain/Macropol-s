"""A sequence that behaves like a tuple and does not have to fit in memory.

This is the piece that makes the rest of the platform survive a large tree
without any verb being rewritten. Every verb treats `Flow.value` as a sequence —
it iterates it, takes `len()`, slices it. `SpilledSequence` satisfies all of that
while holding only a bounded window, so `discover . | link | govern` over four
million observations runs in the same code path as over four hundred.

Three decisions, and each one is about staying honest under that substitution:

**It is re-iterable, not a generator.** A verb may walk its input twice —
`_dependencies` in the SBOM emitter does exactly that — and a generator would
silently yield nothing the second time, producing an SBOM with no dependencies
and no error. Every iteration re-reads from the block, which costs I/O and buys
the property that makes it safe to drop in.

**`len()` is exact and free.** The record count is stored when the block is
written, so `len()` never reads the file. A length that required a full scan
would turn `if not flow.empty` — which is everywhere — into a full pass over four
million records.

**Random access is honest about its cost.** `sequence[n]` is O(n) into the block
because the format is newline-delimited. It is supported because slicing a head
is a normal thing to do, and a small LRU of decoded windows makes the common
pattern — walking forward, or touching the first few — cheap. Pretending it were
O(1) would mean building an offset index nobody asked for; pretending it were
unsupported would break `head` and `sort`.

The alternative designs were considered and rejected: memory-mapping needs a
fixed record width, and SQLite for this is a second store for something that is
already a file of lines.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from ..errors import SpillError
from .codec import decode, encode
from .ident import block_id

#: How many decoded records to keep resident from the last read. One window is
#: enough for forward iteration and for the `head`/`sort --limit` pattern; more
#: would be a cache with its own memory problem inside the thing that exists to
#: bound memory.
WINDOW = 4096


@dataclass(frozen=True, slots=True)
class BlockRef:
    """Where a spilled block is and what is in it.

    Carries `count` and `bytes` so a caller can report and budget without
    touching the disk — the two questions asked far more often than the contents.
    """

    session: str
    block: str
    count: int
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session, "block": self.block,
            "count": self.count, "bytes": self.bytes,
        }

    def __str__(self) -> str:
        return f"{self.block[:12]}… ({self.count:,} records, {self.bytes / 1e6:.1f} MB)"


def write_block(
    store: Any, session: str, values: Sequence[Any], *, key: bytes,
) -> BlockRef:
    """Encode `values` into one content-addressed block. Returns its reference.

    The whole block is encoded before it is written, which is the one place this
    tier holds something proportional to the data. It is bounded by the caller:
    `SpilledSequence.of` chunks input into blocks, so "the whole block" is a
    window, not the whole scan.
    """
    lines = [encode(value) for value in values]
    content = ("\n".join(lines) + "\n" if lines else "").encode("utf-8")
    identifier = block_id(content, key=key)
    store.put(session, identifier, content)
    return BlockRef(
        session=session, block=identifier, count=len(lines), bytes=len(content),
    )


class SpilledSequence(Sequence):
    """Records on disk, addressed like a tuple.

    Immutable by construction: the blocks behind it are content-addressed and
    written once, so two readers of the same sequence cannot disturb each other.
    That is what makes one safe to hand to concurrent consumers.
    """

    __slots__ = ("_store", "_blocks", "_length", "_offsets", "_window",
                 "_window_at", "_lock")

    def __init__(self, store: Any, blocks: Sequence[BlockRef]) -> None:
        self._store = store
        self._blocks = tuple(blocks)
        self._length = sum(block.count for block in self._blocks)
        # Where each block starts in the flattened sequence, so indexing picks a
        # block by arithmetic rather than by opening every one before it.
        offsets: list[int] = []
        running = 0
        for block in self._blocks:
            offsets.append(running)
            running += block.count
        self._offsets = tuple(offsets)
        self._window: list[Any] = []
        self._window_at = -1
        # Guards the window only. Two threads iterating the same sequence must
        # not see each other's half-loaded window.
        self._lock = threading.Lock()

    # -- construction ----------------------------------------------------

    @classmethod
    def of(
        cls,
        values: Any,
        *,
        store: Any,
        session: str,
        key: bytes,
        chunk: int = WINDOW,
    ) -> "SpilledSequence":
        """Spill an iterable, a chunk at a time.

        Streams: `values` may be a generator over four million records and only
        `chunk` of them are ever held. This is the constructor that matters —
        one that took a materialised list would require the caller to have
        already done the thing this exists to prevent.
        """
        blocks: list[BlockRef] = []
        batch: list[Any] = []
        for value in values:
            batch.append(value)
            if len(batch) >= chunk:
                blocks.append(write_block(store, session, batch, key=key))
                batch.clear()
        if batch:
            blocks.append(write_block(store, session, batch, key=key))
        return cls(store, blocks)

    # -- the sequence protocol -------------------------------------------

    def __len__(self) -> int:
        return self._length

    def __iter__(self) -> Iterator[Any]:
        """Streamed, one block at a time. Never holds the whole sequence."""
        for block in self._blocks:
            with self._store.open(block.session, block.block) as handle:
                for line in handle:
                    if line.strip():
                        yield decode(line)

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            # Materialised, because a slice is asked for when the caller wants a
            # bounded piece — `head --count 5`. A lazy slice would defer the
            # read to somewhere the cost is invisible.
            return [self[position] for position in range(*index.indices(self._length))]

        position = index + self._length if index < 0 else index
        if not 0 <= position < self._length:
            # `IndexError`, not `SpillError`: this satisfies the `Sequence`
            # protocol, and a caller iterating it must not have to know it is
            # spilled. Substitutability outranks the taxonomy at this seam.
            raise IndexError(
                f"index {index} is out of range for {self._length} spilled record(s)"
            )
        return self._at(position)

    def _at(self, position: int) -> Any:
        with self._lock:
            if self._window_at >= 0 and (
                self._window_at <= position < self._window_at + len(self._window)
            ):
                return self._window[position - self._window_at]

        number = self._block_of(position)
        block = self._blocks[number]
        start = self._offsets[number]
        loaded = self._load(block)
        with self._lock:
            self._window = loaded
            self._window_at = start
        return loaded[position - start]

    def _block_of(self, position: int) -> int:
        """Which block holds `position`. Binary search over the offsets."""
        import bisect

        return max(0, bisect.bisect_right(self._offsets, position) - 1)

    def _load(self, block: BlockRef) -> list[Any]:
        with self._store.open(block.session, block.block) as handle:
            return [decode(line) for line in handle if line.strip()]

    # -- inspection ------------------------------------------------------

    @property
    def blocks(self) -> tuple[BlockRef, ...]:
        return self._blocks

    @property
    def bytes(self) -> int:
        return sum(block.bytes for block in self._blocks)

    @property
    def resident(self) -> int:
        """How many records are actually in memory right now."""
        with self._lock:
            return len(self._window)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spilled": True,
            "records": self._length,
            "blocks": len(self._blocks),
            "bytes": self.bytes,
            "resident": self.resident,
        }

    def release(self) -> None:
        """Drop the resident window. The blocks stay; this is not a delete."""
        with self._lock:
            self._window = []
            self._window_at = -1

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<SpilledSequence {self._length:,} records in "
            f"{len(self._blocks)} block(s), {self.resident} resident>"
        )
