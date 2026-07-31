"""What a tree looked like last time, so a rescan can read only what moved.

A fingerprint is a mapping of file uri → content digest, and the whole design
turns on one decision: **content, never mtime.**

Modification time is the obvious choice and it is wrong in both directions. A
`git checkout` rewrites mtimes on files whose content is identical, so an
mtime-based scan re-reads a whole tree after switching branches — which is
exactly when somebody is waiting. And a build step that restores a cached file
can write *older* mtimes than the ones already recorded, so an mtime-based scan
skips a file that genuinely changed. The first failure wastes minutes; the second
produces a graph that is quietly wrong about a file it decided not to read.

Hashing costs a read of every file, which sounds like it defeats the purpose —
but reading a file is not the expensive part of a scan. Parsing it, resolving
identities and running twenty-nine discoverers over it is. A fingerprint pass
over a large monorepo reads bytes and does nothing else with them.

The digest is `blake2b` over content only, so a fingerprint is comparable across
machines: a CI runner and a developer's laptop that hold the same commit produce
the same fingerprint, which is what lets one be published as a baseline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

#: Read in blocks rather than whole. A 200 MB file must not become 200 MB of RAM
#: just to be fingerprinted — the spill tier exists because that pattern is how
#: this platform used to run out of memory.
BLOCK = 1024 * 1024

#: Digest size. 16 bytes is plenty to tell two versions of a file apart, and it
#: keeps a fingerprint of a large tree small enough to store and to send.
DIGEST_BYTES = 16

#: Directories never worth fingerprinting. Mirrors the discovery walk rather than
#: importing it: this list is about what to *hash*, that one is about what to
#: *parse*, and they are free to diverge.
SKIP = (
    "/.git/", "/node_modules/", "/.venv/", "/venv/", "/__pycache__/",
    "/dist/", "/build/", "/.tox/", "/target/", "/vendor/", "/.mypy_cache/",
    "/.pytest_cache/", "/site-packages/", "/.slpie/",
)


def digest_file(path: Path) -> str:
    """One file's content digest, read in blocks."""
    hasher = hashlib.blake2b(digest_size=DIGEST_BYTES)
    with path.open("rb") as handle:
        while block := handle.read(BLOCK):
            hasher.update(block)
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class Delta:
    """What moved between two fingerprints.

    Three sets, because they mean three different things to an incremental
    rescan: `added` needs discovering, `changed` needs its old evidence retired
    *and* rediscovering, and `removed` needs only retirement. Collapsing them
    into "dirty" would make the engine re-read files that no longer exist.
    """

    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: int = 0

    @property
    def stale(self) -> tuple[str, ...]:
        """Uris whose recorded evidence no longer holds."""
        return tuple(sorted({*self.changed, *self.removed}))

    @property
    def to_read(self) -> tuple[str, ...]:
        """Uris a rescan must actually open."""
        return tuple(sorted({*self.added, *self.changed}))

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    @property
    def touched(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added), "changed": list(self.changed),
            "removed": list(self.removed), "unchanged": self.unchanged,
            "touched": self.touched,
        }

    def __str__(self) -> str:
        if self.empty:
            return f"nothing changed ({self.unchanged} file(s) unchanged)"
        return (
            f"{len(self.added)} added, {len(self.changed)} changed, "
            f"{len(self.removed)} removed, {self.unchanged} unchanged"
        )


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One tree at one moment: uri → content digest."""

    digests: Mapping[str, str] = field(default_factory=dict)
    root: str = ""

    @classmethod
    def of(
        cls,
        root: str | Path,
        *,
        limit: int = 200_000,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> "Fingerprint":
        """Fingerprint a tree. Bounded, because an unbounded walk is a hang."""
        base = Path(root).expanduser().resolve()
        found: dict[str, str] = {}

        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            if len(found) >= limit:
                break
            if not path.is_file():
                continue
            uri = path.resolve().as_uri()
            if any(part in uri for part in SKIP):
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                found[uri] = digest_file(path)
            except OSError:
                # A file that disappeared between listing and reading is simply
                # not in this fingerprint, which the next delta reports as a
                # removal. Raising would make a rescan fail because somebody
                # saved a file while it ran.
                continue

        return cls(digests=found, root=str(base))

    def compare(self, previous: "Fingerprint | Mapping[str, str]") -> Delta:
        """What moved since `previous`. The whole point of the type."""
        old = (
            previous.digests if isinstance(previous, Fingerprint) else dict(previous)
        )
        new = self.digests

        added = tuple(sorted(set(new) - set(old)))
        removed = tuple(sorted(set(old) - set(new)))
        changed = tuple(sorted(
            uri for uri in set(new) & set(old) if new[uri] != old[uri]
        ))
        return Delta(
            added=added, changed=changed, removed=removed,
            unchanged=len(set(new) & set(old)) - len(changed),
        )

    @property
    def digest(self) -> str:
        """One value for the whole tree, so two trees compare in constant time.

        Order-independent by construction: the uris are sorted before hashing,
        so a fingerprint taken on a filesystem that lists differently still
        produces the same value for the same content.
        """
        hasher = hashlib.blake2b(digest_size=DIGEST_BYTES)
        for uri in sorted(self.digests):
            hasher.update(uri.encode("utf-8"))
            hasher.update(self.digests[uri].encode("utf-8"))
        return hasher.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root, "files": len(self.digests),
            "digest": self.digest, "digests": dict(self.digests),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Fingerprint":
        return cls(
            digests=dict(payload.get("digests", {})),
            root=str(payload.get("root", "")),
        )

    def save(self, path: str | Path) -> Path:
        """Write a baseline. JSON, so a human can look at it and CI can cache it."""
        import json

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Fingerprint":
        """Read a baseline. A missing or unreadable one is an empty fingerprint.

        Empty rather than an error, because "no baseline" is the ordinary first
        run and the correct response to it is a full scan — which is exactly what
        comparing against an empty fingerprint produces.
        """
        import json

        source = Path(path)
        try:
            return cls.from_dict(json.loads(source.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()

    def __len__(self) -> int:
        return len(self.digests)

    def __contains__(self, uri: object) -> bool:
        return uri in self.digests

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self.digests))

    def __str__(self) -> str:
        return f"{len(self.digests)} file(s), digest {self.digest[:12]}"
