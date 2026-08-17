"""What was called, aggregated — and deliberately not what was in it.

This is where an API manager and a privacy commitment collide, so the collision
is settled here in writing rather than in six months when one of them quietly
loses.

`slpie/suggest/attention.py` states, as a non-optional commitment, that the
platform stores aggregates and never trails. Conventional API analytics does the
opposite: it keeps the full request line, the query string, sometimes the body,
because "you never know what you will want to ask later". That is exactly the
reasoning the attention module refuses.

**So a call record is an aggregate from the moment it is made.** The key is
`(api, operation, application, status class, minute)` and there is nowhere to
put a parameterised path, a query string, a body or a caller's address. Not
"we redact them later" — there is no field. A question this cannot answer is a
question the platform declines to answer, which is the same trade §26 makes
when it quarantines a document rather than guessing at it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

#: Records are bucketed to the minute. Finer would make the bucket close enough
#: to a single call to identify one, which is the property being avoided.
BUCKET_SECONDS = 60.0


def status_class(status: int) -> str:
    """`2xx`, `4xx`, `5xx` — not the exact code.

    The exact code on a single call is a fact about that call. The class is a
    fact about the traffic, which is what analytics is for.
    """
    if status <= 0:
        return "none"
    return f"{status // 100}xx"


@dataclass(frozen=True, slots=True)
class Bucket:
    """One aggregate. The whole vocabulary of what may be remembered."""

    api: str
    operation: str
    application: str
    status: str
    minute: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "api": self.api,
            "operation": self.operation,
            "application": self.application,
            "status": self.status,
            "minute": self.minute,
        }


@dataclass
class Analytics:
    """Counters, and the refusal reasons behind them.

    Refusal *reasons* are kept because an operator needs to know whether a wall
    of 403s is one unsubscribed application or forty misconfigured ones, and the
    reason answers that where the count alone does not. The reason is the
    gateway's own stage name — a closed vocabulary, not caller-supplied text.
    """

    now: Any = time.time
    _counts: dict[Bucket, int] = field(default_factory=dict, repr=False)
    _refusals: dict[str, int] = field(default_factory=dict, repr=False)
    _latency: dict[str, list[float]] = field(default_factory=dict, repr=False)

    def record(
        self,
        *,
        api: str,
        operation: str,
        application: str = "anonymous",
        status: int = 200,
        refused_by: str = "",
        seconds: float = 0.0,
    ) -> Bucket:
        bucket = Bucket(
            api=api,
            operation=operation,
            application=application or "anonymous",
            status=status_class(status),
            minute=int(float(self.now()) // BUCKET_SECONDS),
        )
        self._counts[bucket] = self._counts.get(bucket, 0) + 1
        if refused_by:
            self._refusals[refused_by] = self._refusals.get(refused_by, 0) + 1
        if seconds > 0:
            samples = self._latency.setdefault(api, [])
            samples.append(seconds)
            # A bounded window rather than every sample ever seen: a p99 over
            # the last thousand calls is the useful number, and an unbounded
            # list is a leak with a statistic attached.
            if len(samples) > 1000:
                del samples[: len(samples) - 1000]
        return bucket

    def __len__(self) -> int:
        return sum(self._counts.values())

    def __iter__(self) -> Iterator[tuple[Bucket, int]]:
        return iter(sorted(self._counts.items(), key=lambda pair: pair[0].minute))

    def p99(self, api: str) -> float:
        samples = sorted(self._latency.get(api, ()))
        if not samples:
            return 0.0
        return samples[min(len(samples) - 1, int(len(samples) * 0.99))]

    def summary(self) -> dict[str, Any]:
        """What the analytics screen renders."""
        by_api: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_application: dict[str, int] = {}
        for bucket, count in self._counts.items():
            by_api[bucket.api] = by_api.get(bucket.api, 0) + count
            by_status[bucket.status] = by_status.get(bucket.status, 0) + count
            by_application[bucket.application] = (
                by_application.get(bucket.application, 0) + count
            )
        return {
            "calls": len(self),
            "buckets": len(self._counts),
            "by_api": dict(sorted(by_api.items(), key=lambda pair: -pair[1])),
            "by_status": dict(sorted(by_status.items())),
            "top_consumers": dict(
                sorted(by_application.items(), key=lambda pair: -pair[1])[:10]
            ),
            "refusals": dict(sorted(self._refusals.items(), key=lambda pair: -pair[1])),
            "p99": {api: round(self.p99(api), 4) for api in sorted(by_api)},
        }
