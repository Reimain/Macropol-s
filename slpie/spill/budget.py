"""How much this process is allowed to hold, and what happens when it will not fit.

The budget is **per process, shared by every session in it**, and that is the
decision that makes concurrency safe. A per-session budget of 64 MB is not a
limit at all once twenty sessions are running: it is a 1.3 GB limit wearing a
small number. So sessions draw from one pot, and a session that arrives when the
pot is empty spills immediately rather than being told it has room that the
machine does not have.

The consequence is deliberate and worth stating plainly: **under load, everybody
gets slower rather than one unlucky request getting killed.** Spilling costs
latency; an OOM kill costs the whole worker, including the nineteen requests that
were behaving. Degrading is the better failure, and it is the one this picks.

Two numbers, both meaningful:

* `ceiling` — bytes of in-memory records this process will hold. Nothing to do
  with RSS, which includes the interpreter, the graph and everything else; this
  is the part the spill tier actually controls, and claiming to bound the rest
  would be claiming something untrue.
* `reserve` — the fraction kept free so that *releasing* memory is always
  possible. A budget run to exactly zero cannot spill, because spilling itself
  needs a buffer to encode into.

Accounting is by **estimated encoded size**, measured once per block from a real
sample rather than guessed per record. Calling `len(json.dumps(x))` on every
record to decide whether to keep it would cost more than the record.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..errors import SpillError

#: Default ceiling: 128 MB of in-memory records for the whole process. Chosen to
#: be small enough that spilling is exercised in ordinary use rather than only
#: under stress — a spill path that only runs in production is a spill path
#: nobody has tested.
DEFAULT_CEILING = 128 * 1024 * 1024

#: Keep an eighth free. Below this the tier is in pressure and admits nothing new.
RESERVE_FRACTION = 0.125

#: Sampled to estimate a record's encoded size. Enough to smooth over one
#: unusually long excerpt, cheap enough to be free.
SAMPLE = 32


@dataclass(slots=True)
class BudgetReport:
    """What the budget is doing, for a status endpoint and for tests."""

    ceiling: int
    used: int
    peak: int
    admitted: int
    refused: int
    spilled_bytes: int
    holders: int

    @property
    def free(self) -> int:
        return max(0, self.ceiling - self.used)

    @property
    def pressure(self) -> float:
        return round(self.used / self.ceiling, 4) if self.ceiling else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ceiling": self.ceiling, "used": self.used, "free": self.free,
            "peak": self.peak, "pressure": self.pressure,
            "admitted": self.admitted, "refused": self.refused,
            "spilled_bytes": self.spilled_bytes, "holders": self.holders,
        }

    def __str__(self) -> str:
        return (
            f"{self.used / 1e6:.1f}/{self.ceiling / 1e6:.0f} MB "
            f"({self.pressure:.0%}), {self.refused} refusal(s), "
            f"{self.spilled_bytes / 1e6:.1f} MB spilled"
        )


class Budget:
    """A process-wide ceiling on in-memory records, with admission control.

    Thread-safe, because the whole point is several sessions at once. The lock
    is held only for arithmetic — never across I/O — so a session spilling to a
    slow disk does not block another session's admission check.
    """

    def __init__(self, ceiling: int = DEFAULT_CEILING) -> None:
        if ceiling <= 0:
            raise SpillError(
                f"a memory ceiling must be positive; {ceiling} would refuse "
                f"every admission and spill each record individually"
            )
        self.ceiling = int(ceiling)
        self._lock = threading.Lock()
        self._used = 0
        self._peak = 0
        self._admitted = 0
        self._refused = 0
        self._spilled = 0
        self._holders: dict[str, int] = {}

    # -- admission -------------------------------------------------------

    @property
    def reserve(self) -> int:
        return int(self.ceiling * RESERVE_FRACTION)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def free(self) -> int:
        with self._lock:
            return max(0, self.ceiling - self._used - self.reserve)

    @property
    def under_pressure(self) -> bool:
        with self._lock:
            return self._used + self.reserve >= self.ceiling

    def admit(self, holder: str, nbytes: int) -> bool:
        """Ask for `nbytes`. True if granted; False means spill instead.

        Never raises and never blocks. A caller told `False` has a correct
        alternative — write it to disk — so making them wait for room would
        trade a cheap disk write for an unbounded stall.
        """
        if nbytes < 0:
            raise SpillError("cannot admit a negative size")
        with self._lock:
            if self._used + nbytes + self.reserve > self.ceiling:
                self._refused += 1
                return False
            self._used += nbytes
            self._peak = max(self._peak, self._used)
            self._admitted += 1
            self._holders[holder] = self._holders.get(holder, 0) + nbytes
            return True

    def release(self, holder: str, nbytes: int = 0) -> int:
        """Give memory back. With no size, releases everything `holder` holds."""
        with self._lock:
            held = self._holders.get(holder, 0)
            amount = held if nbytes <= 0 else min(nbytes, held)
            self._used = max(0, self._used - amount)
            remaining = held - amount
            if remaining > 0:
                self._holders[holder] = remaining
            else:
                self._holders.pop(holder, None)
            return amount

    def record_spill(self, nbytes: int) -> None:
        with self._lock:
            self._spilled += nbytes

    # -- estimation ------------------------------------------------------

    @staticmethod
    def estimate(records: Any, *, sample: int = SAMPLE) -> int:
        """Bytes one record costs, from a sample rather than from a guess.

        A fixed per-record constant would be wrong in the direction that
        matters: an observation carrying a 500-character excerpt is an order of
        magnitude bigger than one carrying none, and the trees that spill are
        exactly the ones full of the former.
        """
        from .codec import encode

        items = list(records)[:sample]
        if not items:
            return 0
        total = 0
        counted = 0
        for item in items:
            try:
                total += len(encode(item)) + 1        # +1 for the newline
                counted += 1
            except Exception:  # noqa: BLE001 - an unspillable record still costs memory
                total += 512
                counted += 1
        # Python's own object overhead roughly doubles the encoded size in
        # memory. Two is a deliberate under-estimate rather than a precise
        # figure: over-estimating would spill work that would have fitted.
        return (total // max(counted, 1)) * 2

    def report(self) -> BudgetReport:
        with self._lock:
            return BudgetReport(
                ceiling=self.ceiling, used=self._used, peak=self._peak,
                admitted=self._admitted, refused=self._refused,
                spilled_bytes=self._spilled, holders=len(self._holders),
            )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Budget {self.used / 1e6:.1f}/{self.ceiling / 1e6:.0f} MB>"
