"""The seam a storage tier implements, and the rule it cannot opt out of.

Two tiers, two very different backends — a filesystem for per-user working data,
an object store for shared corpora — behind one protocol, so nothing above this
file branches on where data lives.

**The tenant prefix is enforced here, not by the backend.** Every key is checked
against the caller's prefix before the backend is asked, and a key that escapes
it is refused rather than normalised. This matters because the alternative — a
correctly configured bucket policy — is a control that lives in somebody else's
console, is invisible from this codebase, and is wrong about once per migration.
Refusing here means a misconfigured bucket cannot widen access, because the
bucket is never asked for a key outside the prefix.

`..` in a key is the obvious attack and is not the only one. `a/./../../b`,
absolute paths, a leading slash, backslashes on a store that normalises them, and
a key that is exactly the prefix of a *different* tenant (`acme` vs `acme-corp`)
are all refused by `ObjectRef`, which is why callers construct one rather than
passing strings around.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any, Iterator, Protocol, runtime_checkable

from ..errors import SlpieError


class StoreError(SlpieError):
    """A storage key is malformed, escapes its prefix, or could not be read."""


#: One path segment. No slashes, no dots-only segments, no control characters.
#:
#: A leading underscore is allowed because `Dataset.key` uses `_global` and `_`
#: for the scope that owns nothing — reserved names that cannot collide with a
#: real tenant, since a tenant name may not start with one. `.` and `..` are
#: still refused: neither starts with a character this permits.
SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A key that has been checked against the prefix it must stay inside.

    Constructing one is the check. There is no way to reach a backend with a
    bare string, which is what stops the check from being the thing somebody
    forgets on the one code path nobody reviewed.
    """

    prefix: str
    key: str

    def __post_init__(self) -> None:
        for name, value in (("prefix", self.prefix), ("key", self.key)):
            if not value:
                raise StoreError(f"an object reference needs a {name}")
            if value.startswith("/") or "\\" in value:
                raise StoreError(
                    f"{name} {value!r} is absolute or uses backslashes; keys are "
                    f"relative, forward-slashed, and refused rather than rewritten"
                )
            for segment in value.split("/"):
                if not SEGMENT.match(segment):
                    raise StoreError(
                        f"{name} {value!r} has an unusable segment {segment!r}. "
                        f"`..` and `.` are refused here rather than normalised: a "
                        f"normaliser turns a hostile key into a valid one nobody "
                        f"chose"
                    )

        # Belt to that brace. Even with every segment clean, assert the joined
        # path still sits under the prefix — so a future change to SEGMENT
        # cannot silently open a way out.
        full = posixpath.normpath(f"{self.prefix}/{self.key}")
        if full != f"{self.prefix}/{self.key}" or not full.startswith(
            f"{self.prefix}/"
        ):
            raise StoreError(f"{self.key!r} escapes the prefix {self.prefix!r}")

    @property
    def path(self) -> str:
        """The full key the backend sees."""
        return f"{self.prefix}/{self.key}"

    def __str__(self) -> str:
        return self.path


def within(prefix: str, candidate: str) -> bool:
    """Whether `candidate` is inside `prefix`, on a segment boundary.

    The segment boundary is the point. `acme` must not match `acme-corp`, and a
    `startswith` check says it does — which is a cross-tenant read that looks
    like a working prefix filter.
    """
    return candidate == prefix or candidate.startswith(f"{prefix}/")


@runtime_checkable
class ObjectStore(Protocol):
    """What a storage tier must implement.

    Deliberately small. A tier that also offered `move`, `copy` and `sign` would
    be four more operations each needing the prefix check applied correctly, and
    the check is the only thing here that must never be wrong.
    """

    tier: str

    def put(self, ref: ObjectRef, content: bytes) -> int:
        """Write. Returns bytes written, or 0 if the content was already there."""
        ...

    def get(self, ref: ObjectRef) -> bytes:
        """Read. Raises `StoreError` if absent."""
        ...

    def exists(self, ref: ObjectRef) -> bool:
        ...

    def list(self, prefix: str) -> Iterator[str]:
        """Keys under `prefix`. Never crosses a prefix boundary."""
        ...

    def delete(self, ref: ObjectRef) -> bool:
        ...

    def size(self, ref: ObjectRef) -> int:
        ...

    def to_dict(self) -> dict[str, Any]:
        ...
