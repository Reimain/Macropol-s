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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from .errors import Skip, SkipReason, TruncatedWalk, audit, explain

#: Read in blocks rather than whole. A 200 MB file must not become 200 MB of RAM
#: just to be fingerprinted — the spill tier exists because that pattern is how
#: this platform used to run out of memory.
BLOCK = 1024 * 1024

#: Digest size. 16 bytes is plenty to tell two versions of a file apart, and it
#: keeps a fingerprint of a large tree small enough to store and to send.
DIGEST_BYTES = 16

#: Whether an unreadable file stops the run. Strict is the default because the
#: alternative is a graph quietly wrong about a file nobody read; `SLPIE_STRICT=0`
#: is the development escape, which records and explains instead of raising.
#:
#: Named `strict` rather than `production` because the mode is about what happens
#: to an unreadable file, not about where the process is deployed — a developer
#: debugging a partial scan wants strict on, and a batch job over somebody else's
#: home directory may legitimately want it off.
def default_strict() -> bool:
    """The mode, from the environment. Strict unless explicitly turned off."""
    raw = os.environ.get("SLPIE_STRICT", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


#: Directories never worth fingerprinting. Mirrors the discovery walk rather than
#: importing it: this list is about what to *hash*, that one is about what to
#: *parse*, and they are free to diverge.
SKIP = (
    "/.git/", "/node_modules/", "/.venv/", "/venv/", "/__pycache__/",
    "/dist/", "/build/", "/.tox/", "/target/", "/vendor/", "/.mypy_cache/",
    "/.pytest_cache/", "/site-packages/", "/.slpie/",
)


def excluded(uri: str) -> bool:
    """Whether policy says not to read this, regardless of whether it exists."""
    return any(part in uri for part in SKIP)


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
    #: Files the previous fingerprint knew about and this one could not read.
    #: **Not** removed, and that distinction is the whole point: absent from a
    #: fingerprint because nobody read it is not the same as absent from disk,
    #: and a rescan that conflated them would retire the graph nodes drawn from
    #: files that are still there.
    unknown: tuple[str, ...] = ()

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

    @property
    def trustworthy(self) -> bool:
        """Whether this delta accounts for every file it was asked about."""
        return not self.unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added), "changed": list(self.changed),
            "removed": list(self.removed), "unchanged": self.unchanged,
            "unknown": list(self.unknown), "touched": self.touched,
            "trustworthy": self.trustworthy,
        }

    def __str__(self) -> str:
        unread = (
            f", {len(self.unknown)} unread and therefore left alone"
            if self.unknown else ""
        )
        if self.empty:
            return (
                f"nothing changed ({self.unchanged} file(s) unchanged{unread})"
            )
        return (
            f"{len(self.added)} added, {len(self.changed)} changed, "
            f"{len(self.removed)} removed, {self.unchanged} unchanged{unread}"
        )


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One tree at one moment: uri → content digest."""

    digests: Mapping[str, str] = field(default_factory=dict)
    root: str = ""
    #: Files the walk wanted and could not get, each with its reason. Carried on
    #: the fingerprint rather than logged, because `compare()` needs it: a file
    #: this fingerprint could not read and the previous one knew about is
    #: *unknown*, not removed, and only this list tells the two apart.
    skipped: tuple[Skip, ...] = ()
    #: Whether the walk stopped at its file limit. A count rather than a list of
    #: what lay beyond, because that list is unbounded — see `errors.TruncatedWalk`.
    truncated: bool = False
    #: How many paths policy said not to read. A number, not a list: a monorepo's
    #: `node_modules` is hundreds of thousands of files, and recording one object
    #: each would cost more memory than the fingerprint itself.
    excluded: int = 0

    @classmethod
    def of(
        cls,
        root: str | Path,
        *,
        limit: int = 200_000,
        max_bytes: int = 64 * 1024 * 1024,
        strict: bool | None = None,
    ) -> "Fingerprint":
        """Fingerprint a tree. Bounded, because an unbounded walk is a hang.

        Every file the walk wanted and could not read is recorded as a `Skip`. In
        strict mode — the default — that raises `IncompleteFingerprint` rather
        than returning a fingerprint that silently describes less than the tree,
        and a walk that hits its file limit raises `TruncatedWalk`. In lenient
        mode both are recorded, and `compare()` routes the affected files into
        `Delta.unknown` so a rescan leaves them alone.
        """
        base = Path(root).expanduser().resolve()
        if strict is None:
            strict = default_strict()

        found: dict[str, str] = {}
        skips: list[Skip] = []
        ignored = 0
        truncated = False

        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                uri = path.resolve().as_uri()
            except OSError as error:
                # `is_file()` and `resolve()` both stat, and either can fail on a
                # dangling symlink or an entry that vanished mid-walk. `as_uri()`
                # is pure string work and cannot: `base` was resolved, so every
                # candidate `rglob` yields under it is already absolute.
                skips.append(Skip(path.as_uri(), SkipReason.UNREADABLE, str(error)))
                continue

            if excluded(uri):
                ignored += 1
                continue
            if len(found) >= limit:
                # Stop, rather than walking on to record every file beyond the
                # limit. The recorded form was the first implementation and it is
                # the one that runs a two-million-file monorepo out of memory to
                # describe a walk that had already given up.
                truncated = True
                break
            try:
                size = path.stat().st_size
                if size > max_bytes:
                    skips.append(Skip(
                        uri, SkipReason.TOO_LARGE,
                        f"{size} bytes, over the {max_bytes} byte budget",
                    ))
                    continue
                found[uri] = digest_file(path)
            except OSError as error:
                # A file that disappeared between listing and reading is not in
                # this fingerprint. That used to be reported as a removal, which
                # is exactly the lie this module exists to stop: nobody read it,
                # so nobody knows whether it is gone.
                skips.append(Skip(uri, SkipReason.UNREADABLE, str(error)))

        if truncated and strict:
            raise TruncatedWalk(str(base), limit)

        return cls(
            digests=found, root=str(base), truncated=truncated, excluded=ignored,
            skipped=audit(str(base), skips, strict=strict),
        )

    @property
    def unread(self) -> frozenset[str]:
        """Uris this fingerprint wanted and could not read.

        Excluded paths are not in here: the graph holds nothing derived from a
        `node_modules` the operator configured away, so its absence from a
        fingerprint says nothing that needs qualifying.
        """
        return frozenset(item.uri for item in self.skipped)

    @property
    def complete(self) -> bool:
        """Whether this fingerprint read everything it meant to."""
        return not self.skipped and not self.truncated

    def explain_skips(self) -> str:
        """Why files were not read — what lenient mode shows a developer."""
        if self.truncated:
            return (
                f"  walk_limit: stopped after {len(self.digests)} file(s); "
                f"whatever lies beyond was not reached and cannot be named"
            )
        return explain(self.skipped)

    def compare(self, previous: "Fingerprint | Mapping[str, str]") -> Delta:
        """What moved since `previous`. The whole point of the type.

        A uri the previous fingerprint knew about and this walk did not read is
        reported as **unknown**, never as removed. That one branch is the fix:
        `removed` retires the graph nodes drawn from a file, and retiring them
        because nobody looked is how an incremental engine produces a graph that
        is confidently wrong about a file that is still on disk.

        Three ways a uri lands in `unknown`, and the third is the subtle one:

        * this walk tried to read it and could not;
        * this walk stopped at its limit, so it can vouch for nothing it did not
          reach — after a truncated walk *every* disappearance is unknown;
        * the **exclusion policy changed** since the baseline. Adding `/vendor/`
          to `SKIP` makes a hundred files vanish from the fingerprint without a
          single one being deleted, and calling that a removal would retire
          everything they justify. Tested against the current policy rather than
          recorded per file, so it costs nothing on the ordinary path.
        """
        old = (
            previous.digests if isinstance(previous, Fingerprint) else dict(previous)
        )
        new = self.digests
        unread = self.unread
        vanished = set(old) - set(new)

        unaccounted = vanished if self.truncated else {
            uri for uri in vanished if uri in unread or excluded(uri)
        }

        added = tuple(sorted(set(new) - set(old)))
        removed = tuple(sorted(vanished - unaccounted))
        changed = tuple(sorted(
            uri for uri in set(new) & set(old) if new[uri] != old[uri]
        ))
        return Delta(
            added=added, changed=changed, removed=removed,
            unchanged=len(set(new) & set(old)) - len(changed),
            unknown=tuple(sorted(unaccounted)),
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
            "complete": self.complete, "truncated": self.truncated,
            "excluded": self.excluded,
            "skipped": [item.to_dict() for item in self.skipped],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Fingerprint":
        return cls(
            digests=dict(payload.get("digests", {})),
            root=str(payload.get("root", "")),
            truncated=bool(payload.get("truncated", False)),
            excluded=int(payload.get("excluded", 0)),
            skipped=tuple(
                Skip(
                    str(item.get("uri", "")),
                    SkipReason(str(item.get("reason", "unreadable"))),
                    str(item.get("detail", "")),
                )
                for item in payload.get("skipped", ())
            ),
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
        if self.truncated:
            qualifier = ", truncated"
        elif self.skipped:
            qualifier = f", {len(self.skipped)} unread"
        else:
            qualifier = ""
        return (
            f"{len(self.digests)} file(s), digest {self.digest[:12]}{qualifier}"
        )
