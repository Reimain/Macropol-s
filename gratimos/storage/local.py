"""A secured local repository for staged and captured bytes.

Two properties matter here, because keys can originate from data the kernel
merely *observed* rather than authored:

* **Containment.** Every key is resolved and then verified to still live under
  the root. ``../`` traversal, absolute keys, and symlinks that escape the root
  are refused, not sanitized.
* **Atomicity.** Writes land in a temp file inside the same directory and are
  renamed into place, so a reader never sees a half-written object.

Permissions default to owner-only (0700 dirs / 0600 files) since staged payloads
routinely contain the raw data the whole system is being pointed at.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterator, Mapping

from ..errors import StorageError, UnsafePathError
from ..ids import digest, now_ns
from .base import ObjectRef

_META_SUFFIX = ".meta.json"


class LocalRepository:
    """Path-contained, atomic, owner-private object store on local disk."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        name: str = "local",
        dir_mode: int = 0o700,
        file_mode: int = 0o600,
    ) -> None:
        self.name = name
        self.dir_mode = dir_mode
        self.file_mode = file_mode
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(dir_mode)
        except OSError:  # pragma: no cover - e.g. a mounted volume
            pass
        self._lock = threading.Lock()

    # -- path safety -----------------------------------------------------

    def resolve(self, key: str) -> Path:
        """Map a key to a path, refusing anything that escapes the root."""
        if not key or key in (".", ".."):
            raise UnsafePathError(f"empty or relative key: {key!r}")
        candidate = Path(key)
        if candidate.is_absolute() or candidate.drive:
            raise UnsafePathError(f"absolute keys are not allowed: {key!r}")
        if any(part == ".." for part in candidate.parts):
            raise UnsafePathError(f"traversal is not allowed: {key!r}")
        target = (self.root / candidate).resolve()
        if target != self.root and self.root not in target.parents:
            raise UnsafePathError(f"key escapes repository root: {key!r}")
        return target

    # -- writes ----------------------------------------------------------

    def put(self, key: str, data: bytes, *, metadata: Mapping[str, str] | None = None) -> ObjectRef:
        target = self.resolve(key)
        payload = bytes(data)
        with self._lock:
            self._mkdir(target.parent)
            handle, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".gratimos-", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(handle, "wb") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(tmp, self.file_mode)
                os.replace(tmp, target)
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                raise StorageError(f"failed to write {key!r}: {exc}") from exc
            if metadata:
                self._write_metadata(target, dict(metadata))
        return self._ref(key, target, dict(metadata or {}))

    def put_text(self, key: str, text: str, **kwargs: object) -> ObjectRef:
        return self.put(key, text.encode("utf-8"), **kwargs)  # type: ignore[arg-type]

    def put_json(self, key: str, value: object, **kwargs: object) -> ObjectRef:
        raw = json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")
        return self.put(key, raw, **kwargs)  # type: ignore[arg-type]

    # -- reads -----------------------------------------------------------

    def get(self, key: str) -> bytes:
        target = self.resolve(key)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise StorageError(f"failed to read {key!r}: {exc}") from exc

    def get_text(self, key: str) -> str:
        return self.get(key).decode("utf-8")

    def get_json(self, key: str) -> object:
        return json.loads(self.get_text(key))

    def exists(self, key: str) -> bool:
        try:
            return self.resolve(key).is_file()
        except UnsafePathError:
            return False

    def stat(self, key: str) -> ObjectRef:
        target = self.resolve(key)
        if not target.is_file():
            raise StorageError(f"no such object: {key!r}")
        return self._ref(key, target, self._read_metadata(target))

    def delete(self, key: str) -> bool:
        target = self.resolve(key)
        if not target.is_file():
            return False
        target.unlink()
        Path(str(target) + _META_SUFFIX).unlink(missing_ok=True)
        return True

    def list(self, prefix: str = "") -> Iterator[ObjectRef]:
        base = self.resolve(prefix) if prefix else self.root
        if base.is_file():
            yield self.stat(prefix)
            return
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.endswith(_META_SUFFIX) or path.name.startswith(".gratimos-"):
                continue
            key = path.relative_to(self.root).as_posix()
            yield self._ref(key, path, self._read_metadata(path))

    # -- maintenance -----------------------------------------------------

    def total_bytes(self, prefix: str = "") -> int:
        return sum(ref.size for ref in self.list(prefix))

    def prune(self, prefix: str = "", *, older_than_ns: int = 0, keep: int = 0) -> list[str]:
        """Drop staged objects by age or count, newest kept first."""
        refs = sorted(self.list(prefix), key=lambda r: r.ts_ns, reverse=True)
        cutoff = now_ns() - older_than_ns if older_than_ns else 0
        removed: list[str] = []
        for index, ref in enumerate(refs):
            too_old = cutoff and ref.ts_ns < cutoff
            over_count = keep and index >= keep
            if too_old or over_count:
                if self.delete(ref.key):
                    removed.append(ref.key)
        return removed

    def clear(self) -> None:
        """Remove every object. Only ever touches paths under the root."""
        for child in self.root.iterdir():
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)

    # -- internals -------------------------------------------------------

    def _mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for parent in [path, *path.parents]:
            if parent == self.root:
                break
            try:
                parent.chmod(self.dir_mode)
            except OSError:  # pragma: no cover - best effort
                break

    def _write_metadata(self, target: Path, metadata: dict[str, str]) -> None:
        sidecar = Path(str(target) + _META_SUFFIX)
        sidecar.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        os.chmod(sidecar, self.file_mode)

    def _read_metadata(self, target: Path) -> dict[str, str]:
        sidecar = Path(str(target) + _META_SUFFIX)
        if not sidecar.is_file():
            return {}
        try:
            return dict(json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}

    def _ref(self, key: str, path: Path, metadata: dict[str, str]) -> ObjectRef:
        stat = path.stat()
        return ObjectRef(
            key=key,
            size=stat.st_size,
            content_digest=digest(path.read_bytes()) if stat.st_size <= 8 << 20 else "",
            store=self.name,
            uri=f"file://{path}",
            ts_ns=int(stat.st_mtime * 1e9),
            metadata=metadata,
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<LocalRepository {self.name} root={self.root}>"
