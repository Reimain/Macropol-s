"""Memory budget: how much the kernel is allowed to hold.

"Hold it in memory if memory allows, otherwise stage it cheaply" only works if
*allows* is a number someone decided, not a number discovered at the moment of
the MemoryError. The budget is that number. It tracks residency, admits or
refuses new payloads, and nominates eviction candidates by least-recent use so
the spill tier has a defensible order to work through.

The budget accounts for payloads it was told about. It is not a heap profiler
and does not pretend to be — it is a ledger the kernel keeps honestly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..errors import HubError, MemoryBudgetExceeded
from ..ids import now_ns

MIB = 1 << 20


@dataclass(slots=True)
class Residency:
    """One payload's occupancy record."""

    key: str
    nbytes: int
    admitted_ns: int = field(default_factory=now_ns)
    last_touch_ns: int = field(default_factory=now_ns)
    touches: int = 1
    pinned: bool = False

    def touch(self) -> None:
        self.last_touch_ns = now_ns()
        self.touches += 1

    @property
    def age_s(self) -> float:
        return (now_ns() - self.admitted_ns) / 1e9

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bytes": self.nbytes,
            "touches": self.touches,
            "age_s": round(self.age_s, 3),
            "pinned": self.pinned,
        }


@dataclass(frozen=True, slots=True)
class Admission:
    """The budget's answer to "can I hold this?"."""

    admitted: bool
    key: str
    nbytes: int
    evict: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "key": self.key,
            "bytes": self.nbytes,
            "evict": list(self.evict),
            "reason": self.reason,
        }


class MemoryBudget:
    """A ledger of what is resident, capped at a size the operator chose."""

    def __init__(
        self,
        limit_bytes: int = 256 * MIB,
        *,
        per_payload_limit: int = 64 * MIB,
        high_water: float = 0.85,
    ) -> None:
        if limit_bytes <= 0:
            raise HubError("limit_bytes must be positive")
        self.limit = int(limit_bytes)
        self.per_payload_limit = min(int(per_payload_limit), self.limit)
        self.high_water = high_water
        self._resident: dict[str, Residency] = {}
        self._used = 0
        self._peak = 0
        self._lock = threading.RLock()
        self.evictions = 0
        self.rejections = 0

    # -- accounting ------------------------------------------------------

    @property
    def used(self) -> int:
        return self._used

    @property
    def free(self) -> int:
        return max(self.limit - self._used, 0)

    @property
    def peak(self) -> int:
        return self._peak

    @property
    def pressure(self) -> float:
        return self._used / self.limit if self.limit else 1.0

    @property
    def under_pressure(self) -> bool:
        return self.pressure >= self.high_water

    # -- admission -------------------------------------------------------

    def consider(self, key: str, nbytes: int) -> Admission:
        """Decide whether a payload can be held, and what to evict if not.

        This does not mutate the ledger — the caller stages the payload first
        and only then calls :meth:`admit`, so a failed spill never leaves the
        budget believing something is resident when it is not.
        """
        with self._lock:
            if nbytes > self.per_payload_limit:
                return Admission(
                    False, key, nbytes,
                    reason=f"payload of {nbytes} bytes exceeds per-payload limit {self.per_payload_limit}",
                )
            if nbytes <= self.free:
                return Admission(True, key, nbytes)

            needed = nbytes - self.free
            evict: list[str] = []
            freed = 0
            for candidate in self._eviction_order():
                if freed >= needed:
                    break
                evict.append(candidate.key)
                freed += candidate.nbytes
            if freed < needed:
                return Admission(
                    False, key, nbytes, tuple(evict),
                    reason=f"needs {needed} bytes; only {freed} evictable of {self.limit} total",
                )
            return Admission(True, key, nbytes, tuple(evict), reason="admitted after eviction")

    def admit(self, key: str, nbytes: int, *, pinned: bool = False) -> Residency:
        """Record a payload as resident. Call after any required eviction."""
        with self._lock:
            if nbytes > self.free:
                raise MemoryBudgetExceeded(
                    f"cannot admit {key!r}: {nbytes} bytes requested, {self.free} free "
                    f"(limit {self.limit})"
                )
            existing = self._resident.pop(key, None)
            if existing is not None:
                self._used -= existing.nbytes
            entry = Residency(key=key, nbytes=nbytes, pinned=pinned)
            self._resident[key] = entry
            self._used += nbytes
            self._peak = max(self._peak, self._used)
            return entry

    def release(self, key: str) -> int:
        """Drop a payload from the ledger, returning the bytes freed."""
        with self._lock:
            entry = self._resident.pop(key, None)
            if entry is None:
                return 0
            self._used -= entry.nbytes
            self.evictions += 1
            return entry.nbytes

    def touch(self, key: str) -> None:
        with self._lock:
            if entry := self._resident.get(key):
                entry.touch()

    def pin(self, key: str, pinned: bool = True) -> bool:
        with self._lock:
            if entry := self._resident.get(key):
                entry.pinned = pinned
                return True
            return False

    def reject(self, reason: str = "") -> None:
        with self._lock:
            self.rejections += 1

    # -- eviction --------------------------------------------------------

    def _eviction_order(self) -> list[Residency]:
        """Least-recently-touched first; pinned entries are never candidates."""
        return sorted(
            (r for r in self._resident.values() if not r.pinned),
            key=lambda r: (r.last_touch_ns, -r.nbytes),
        )

    def candidates(self, target_bytes: int = 0) -> list[str]:
        """Keys to evict to free ``target_bytes`` (or to clear the high water)."""
        with self._lock:
            if not target_bytes:
                target_bytes = max(int(self._used - self.limit * self.high_water * 0.8), 0)
            out: list[str] = []
            freed = 0
            for entry in self._eviction_order():
                if freed >= target_bytes:
                    break
                out.append(entry.key)
                freed += entry.nbytes
            return out

    # -- introspection ---------------------------------------------------

    def resident(self, key: str) -> Residency | None:
        return self._resident.get(key)

    def keys(self) -> list[str]:
        return list(self._resident)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "limit": self.limit,
                "used": self._used,
                "free": self.free,
                "peak": self._peak,
                "pressure": round(self.pressure, 4),
                "under_pressure": self.under_pressure,
                "resident_count": len(self._resident),
                "evictions": self.evictions,
                "rejections": self.rejections,
                "largest": sorted(
                    (r.to_dict() for r in self._resident.values()),
                    key=lambda r: r["bytes"], reverse=True,
                )[:5],
            }

    def __contains__(self, key: object) -> bool:
        return str(key) in self._resident

    def __iter__(self) -> Iterator[Residency]:
        return iter(list(self._resident.values()))

    def __len__(self) -> int:
        return len(self._resident)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<MemoryBudget {self._used}/{self.limit} bytes resident={len(self._resident)}>"
