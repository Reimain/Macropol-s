"""The spill id — fixed length, content-derived, and scoped to one session.

Every spilled block is addressed by an id with three properties, and each one
exists to stop a specific way this goes wrong under load:

**Fixed length.** Always `LENGTH` characters of lowercase hex, whatever the block
contains. A caller can validate one at a glance, a path built from it has a known
size, and a log line carrying one is not sometimes 8 and sometimes 90 characters.
Variable-length identifiers are how a path length limit becomes a production
incident on the one tenant whose name is long.

**Content-derived.** The id is a digest of the bytes, so writing the same block
twice writes it once. Under concurrency this matters more than it looks: two
users scanning the same monorepo produce byte-identical blocks, and a
content-addressed store deduplicates them without either one knowing the other
exists.

**Session-scoped, by keyed hash rather than by convention.** The digest is keyed
with a per-session secret, so the same bytes in two sessions produce two
*different* ids. Three consequences:

* One session's id cannot be derived from content another session also holds, so
  a caller cannot fish for another tenant's blocks by guessing.
* Blocks cannot collide across sessions, so cleanup for one session can never
  delete a block another is still reading.
* Deduplication stays *within* a session, which is the correct boundary: sharing
  blocks between tenants would be a data leak dressed up as an optimisation.

The keying is `blake2b(key=...)` — a keyed hash, which is what this needs.
`hashlib` gives it directly, so there is no third-party dependency and no hand-
rolled construction. It is not a signature and does not pretend to be: a block id
proves nothing about who wrote it, only that a caller who does not hold the
session key cannot construct it.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Final

from ..errors import SpillError

#: Digest size in bytes. 16 bytes is 128 bits — collision-resistant far beyond
#: any plausible block count, and short enough that a path built from it is
#: comfortable on every filesystem.
DIGEST_BYTES: Final[int] = 16

#: The resulting id length in characters. Asserted rather than derived at the
#: call site so a change to `DIGEST_BYTES` cannot silently change every path.
LENGTH: Final[int] = DIGEST_BYTES * 2

#: blake2b's maximum key length. A longer secret would raise at hash time, which
#: is a failure at the first spill rather than at session creation.
MAX_KEY_BYTES: Final[int] = 64

#: What a valid id looks like. Anchored, so a caller cannot smuggle a path
#: separator or a `..` through an id and read outside the session directory.
PATTERN: Final[re.Pattern[str]] = re.compile(rf"\A[0-9a-f]{{{LENGTH}}}\Z")


def new_session_key() -> bytes:
    """A fresh per-session secret, from the OS entropy source.

    `os.urandom` rather than `random`: the point of the key is that another
    session cannot reconstruct it, and a seeded PRNG shared across forked
    workers would hand every worker the same "secret".
    """
    return os.urandom(32)


def block_id(content: bytes, *, key: bytes) -> str:
    """Bytes plus a session key → the fixed-length id that addresses them."""
    if len(key) > MAX_KEY_BYTES:
        raise SpillError(
            f"a session key may be at most {MAX_KEY_BYTES} bytes; this one is "
            f"{len(key)}. Refused here rather than at the first spill, where it "
            f"would surface as a hashing error nobody could place"
        )
    return hashlib.blake2b(
        content, digest_size=DIGEST_BYTES, key=key,
    ).hexdigest()


def is_block_id(text: str) -> bool:
    """Whether `text` is a well-formed id, and therefore safe to build a path from."""
    return bool(PATTERN.fullmatch(text))


def require_block_id(text: str) -> str:
    """`text` if it is a valid id, otherwise a refusal that says why.

    Every path this package builds goes through here. An id arriving from a
    request body, a resumed session file or a client is untrusted input, and
    `session/blocks/<id>` with an unvalidated id is a directory traversal — the
    kind that reads as a caching bug until somebody notices which files came
    back.
    """
    if not is_block_id(text):
        raise SpillError(
            f"{text[:32]!r} is not a block id: expected exactly {LENGTH} "
            f"lowercase hex characters. Paths are built from these, so anything "
            f"else is refused rather than normalised"
        )
    return text
