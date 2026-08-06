"""Where spilled blocks live, and the rules that make concurrent use safe.

A `SpillStore` is deliberately tiny — put bytes, get bytes, drop, sweep — because
the interesting decisions are about *when* and *where*, not about storage. The
stdlib default writes files; a ring-1 adapter can put them in S3 behind the same
four methods without ring 0 learning that S3 exists.

Four rules, and each is about a way concurrent writers destroy each other:

**Writes are atomic.** A block is written to a uniquely-named temporary file in
the same directory and then `os.replace`d into place, which is atomic on POSIX
and on Windows. A reader therefore sees a whole block or no block — never the
first half of one another process is still writing. Writing directly to the final
path is the single most common way a cache becomes a source of truncated reads
under load.

**A block is written once.** Ids are content-derived, so a block that already
exists has identical bytes and re-writing it would be pure cost. The check is
`exists()` before write rather than a lock: two processes racing to write the
same content both produce the same bytes, and the loser's `os.replace` is
harmless.

**Sessions are directories, and a session only ever touches its own.** Every path
is `root/<session>/<id>`, `<session>` is validated, and `<id>` must match the
fixed-length hex pattern. Nothing accepts a path from a caller. That is what
makes cleanup safe: a session sweeping its own directory cannot reach into one
another session is reading from.

**Nothing is deleted implicitly on read.** A block stays until its session is
released or swept. A store that dropped blocks as they were consumed would make
re-reading a sequence — which any verb may do, because `Flow` values are not
promised to be single-pass — silently return nothing the second time.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from ..errors import SpillError
from .codec import SpillError
from .ident import require_block_id

#: A session directory name. Anchored and conservative — a session id reaching
#: this from an HTTP request is untrusted, and `root/../../etc` is not a session.
SESSION_PATTERN = re.compile(r"\A[0-9a-zA-Z][0-9a-zA-Z_-]{0,63}\Z")

#: Where blocks go when the caller names no root. Under the system temp
#: directory, in a named subdirectory so an operator can find and measure it.
DEFAULT_ROOT_NAME = "slpie-spill"


def require_session(name: str) -> str:
    """A session name that is safe to use as a directory component."""
    if not SESSION_PATTERN.fullmatch(name):
        raise SpillError(
            f"{name[:32]!r} is not a usable session name: letters, digits, "
            f"dashes and underscores only, up to 64 characters. Directories are "
            f"built from this, so it is refused rather than sanitised — "
            f"sanitising two different names into the same directory would let "
            f"one session read another's blocks"
        )
    return name


@runtime_checkable
class SpillStore(Protocol):
    """Where blocks are kept. Four methods, so an adapter is small."""

    def put(self, session: str, block: str, content: bytes) -> int:
        """Store `content`. Returns the bytes stored, 0 if it was already there."""

    def get(self, session: str, block: str) -> bytes:
        """The block's bytes. Raises `SpillError` if it is gone."""

    def open(self, session: str, block: str) -> Any:
        """The block as a *streaming* text handle, for line-at-a-time reads."""

    def drop(self, session: str, block: str) -> bool:
        """Remove one block. True if it was there."""

    def sweep(self, session: str) -> int:
        """Remove everything for a session. Returns the bytes reclaimed."""


@dataclass(frozen=True, slots=True)
class StoreReport:
    """What a store is holding, for an operator who has to answer for the disk."""

    root: str
    sessions: int
    blocks: int
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root, "sessions": self.sessions,
            "blocks": self.blocks, "bytes": self.bytes,
        }

    def __str__(self) -> str:
        return (
            f"{self.blocks} block(s), {self.bytes / 1e6:.1f} MB across "
            f"{self.sessions} session(s) under {self.root}"
        )


class FileStore:
    """The stdlib default: one directory per session, one file per block."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(
            root if root is not None
            else Path(tempfile.gettempdir()) / DEFAULT_ROOT_NAME
        ).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Guards directory creation only. Block writes need no lock: they are
        # content-addressed and atomically replaced, so a race between two
        # writers of the same block ends with both having written the same bytes.
        self._lock = threading.Lock()

    # -- paths -----------------------------------------------------------

    def _session_dir(self, session: str) -> Path:
        path = self.root / require_session(session)
        if not path.exists():
            with self._lock:
                path.mkdir(parents=True, exist_ok=True)
        return path

    def _path(self, session: str, block: str) -> Path:
        return self._session_dir(session) / require_block_id(block)

    # -- the protocol ----------------------------------------------------

    def put(self, session: str, block: str, content: bytes) -> int:
        path = self._path(session, block)
        if path.exists():
            # Content-addressed: the bytes already there are these bytes.
            return 0

        # Written to a unique temporary name in the *same directory*, then
        # atomically renamed. Same directory because `os.replace` is only atomic
        # within a filesystem, and /tmp is often a different one.
        handle, staged = tempfile.mkstemp(dir=path.parent, prefix=".staging-")
        try:
            with os.fdopen(handle, "wb") as target:
                target.write(content)
            os.replace(staged, path)
        except BaseException:
            # Includes KeyboardInterrupt deliberately: leaving a `.staging-`
            # file behind on Ctrl-C would accumulate across runs and the sweep
            # would not recognise it as a block.
            Path(staged).unlink(missing_ok=True)
            raise
        return len(content)

    def get(self, session: str, block: str) -> bytes:
        path = self._path(session, block)
        try:
            return path.read_bytes()
        except OSError as error:
            raise SpillError(
                f"spilled block {block[:12]}… is unreadable: {error}. The answer "
                f"built on it would be short by however much it held, so this "
                f"raises rather than returning what is left"
            ) from error

    def open(self, session: str, block: str) -> Any:
        """A text handle. Streaming is the point: a block is read a line at a time."""
        path = self._path(session, block)
        try:
            return path.open("r", encoding="utf-8")
        except OSError as error:
            raise SpillError(
                f"spilled block {block[:12]}… cannot be opened: {error}"
            ) from error

    def drop(self, session: str, block: str) -> bool:
        path = self._path(session, block)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def sweep(self, session: str) -> int:
        """Everything for one session. Never touches another's directory."""
        path = self.root / require_session(session)
        if not path.is_dir():
            return 0
        reclaimed = sum(
            item.stat().st_size for item in path.iterdir() if item.is_file()
        )
        shutil.rmtree(path, ignore_errors=True)
        return reclaimed

    # -- inspection ------------------------------------------------------

    def blocks(self, session: str) -> tuple[str, ...]:
        path = self.root / require_session(session)
        if not path.is_dir():
            return ()
        from .ident import is_block_id

        return tuple(sorted(
            item.name for item in path.iterdir()
            if item.is_file() and is_block_id(item.name)
        ))

    def sessions(self) -> tuple[str, ...]:
        return tuple(sorted(
            item.name for item in self.root.iterdir()
            if item.is_dir() and SESSION_PATTERN.fullmatch(item.name)
        ))

    def report(self) -> StoreReport:
        blocks = total = 0
        names = self.sessions()
        for session in names:
            for block in self.blocks(session):
                blocks += 1
                total += (self.root / session / block).stat().st_size
        return StoreReport(
            root=str(self.root), sessions=len(names), blocks=blocks, bytes=total,
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<FileStore {self.root}>"
