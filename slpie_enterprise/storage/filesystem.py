"""The working tier: per-user data on a filesystem or a mounted volume.

Content-addressed writes, so putting the same bytes twice costs one write and
the second returns 0. Atomic renames, so a reader sees all of a value or none —
a partially written notebook checkpoint is a corrupt notebook, and it looks like
the user's fault.

The prefix check lives in `ObjectRef` (ring 0) rather than here, deliberately.
Every backend gets it for free and none of them can implement it slightly
differently.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterator

from slpie.workspace import ObjectRef
from slpie.workspace.store import StoreError, within


class FilesystemStore:
    """`ObjectStore` over a directory."""

    tier = "work"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: ObjectRef) -> Path:
        candidate = (self.root / ref.path).resolve()
        # `ObjectRef` already refused anything that could climb out. This is the
        # second lock on the same door: if the ref rules ever loosen, a resolved
        # path outside the root still cannot be read.
        if not str(candidate).startswith(str(self.root) + os.sep):
            raise StoreError(f"{ref.path!r} resolves outside {self.root}")
        return candidate

    def put(self, ref: ObjectRef, content: bytes) -> int:
        target = self._path(ref)
        digest = hashlib.blake2b(content, digest_size=16).hexdigest()
        if target.exists() and self._digest(target) == digest:
            return 0                      # content-addressed: already there

        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(target.suffix + f".{os.getpid()}.partial")
        staging.write_bytes(content)
        os.replace(staging, target)       # atomic: all of it, or none
        return len(content)

    @staticmethod
    def _digest(path: Path) -> str:
        hasher = hashlib.blake2b(digest_size=16)
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                hasher.update(block)
        return hasher.hexdigest()

    def get(self, ref: ObjectRef) -> bytes:
        try:
            return self._path(ref).read_bytes()
        except FileNotFoundError as error:
            raise StoreError(f"no object at {ref.path}") from error

    def exists(self, ref: ObjectRef) -> bool:
        return self._path(ref).is_file()

    def list(self, prefix: str) -> Iterator[str]:
        base = (self.root / prefix)
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.endswith(".partial"):
                continue
            key = path.relative_to(self.root).as_posix()
            # Belt to brace: never yield a key outside the prefix, even if the
            # walk somehow reached one through a symlink.
            if within(prefix, key):
                yield key

    def delete(self, ref: ObjectRef) -> bool:
        try:
            self._path(ref).unlink()
            return True
        except FileNotFoundError:
            return False

    def size(self, ref: ObjectRef) -> int:
        try:
            return self._path(ref).stat().st_size
        except FileNotFoundError as error:
            raise StoreError(f"no object at {ref.path}") from error

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "backend": "filesystem", "root": str(self.root)}
