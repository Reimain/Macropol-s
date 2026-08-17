"""Route by tier, so nothing above has to know where a dataset lives.

The alternative is every caller picking a backend, which puts the prefix check
in every caller — and that is where it eventually gets forgotten.

A write to the shared tier is refused here as well as by `DatasetGrant`. Two
independent refusals of the same thing, because this one holds even for a caller
that built an `ObjectRef` by hand and never went near a grant.
"""

from __future__ import annotations

from typing import Any, Iterator

from slpie.workspace import ObjectRef, Tier
from slpie.workspace.store import ObjectStore, StoreError


class TieredStore:
    """One `ObjectStore` facade over the working and shared tiers."""

    tier = "tiered"

    def __init__(self, *, work: ObjectStore, shared: ObjectStore | None = None) -> None:
        self.work = work
        self.shared = shared

    def _for(self, ref: ObjectRef) -> ObjectStore:
        """The backend a key belongs to, read from its first segment.

        `Dataset.key` starts with the tier, so the routing is derivable from the
        key itself rather than passed alongside it — which means a key cannot
        arrive at the wrong backend because a caller forgot an argument.
        """
        head = ref.prefix.split("/", 1)[0]
        if head == Tier.SHARED.value:
            if self.shared is None:
                raise StoreError(
                    "this deployment has no shared tier configured, so "
                    f"{ref.path!r} cannot be reached. Configure an S3 store, or "
                    f"publish the corpus to the working tier"
                )
            return self.shared
        return self.work

    def put(self, ref: ObjectRef, content: bytes) -> int:
        if ref.prefix.split("/", 1)[0] == Tier.SHARED.value:
            raise StoreError(
                f"the shared tier is read-only: {ref.path!r} is a corpus many "
                f"tenants read, and a writable one is a way for them to reach "
                f"each other"
            )
        return self.work.put(ref, content)

    def get(self, ref: ObjectRef) -> bytes:
        return self._for(ref).get(ref)

    def exists(self, ref: ObjectRef) -> bool:
        return self._for(ref).exists(ref)

    def list(self, prefix: str) -> Iterator[str]:
        head = prefix.split("/", 1)[0]
        if head == Tier.SHARED.value and self.shared is not None:
            yield from self.shared.list(prefix)
            return
        yield from self.work.list(prefix)

    def delete(self, ref: ObjectRef) -> bool:
        if ref.prefix.split("/", 1)[0] == Tier.SHARED.value:
            raise StoreError("the shared tier is read-only")
        return self.work.delete(ref)

    def size(self, ref: ObjectRef) -> int:
        return self._for(ref).size(ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "work": self.work.to_dict(),
            "shared": self.shared.to_dict() if self.shared else None,
        }
