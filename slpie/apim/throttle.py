"""Throttling: tiers declared as data, decided against a clock.

A `deque` of timestamps and a monotonic token bucket. No Redis, no library — a
rate limit is arithmetic, and ring 0 is stdlib.

The tiers are declared in the shape `governance/policies.py` uses for its rules
and decided the way `workspace/quota.py` decides an allocation: `admits` returns
whether *and why not*. A 429 that says "too many requests" and nothing else
leaves the caller guessing at both the limit and the wait, and guessing at a
wait produces a retry storm.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Mapping


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    """One tier."""

    name: str
    requests: int
    window_seconds: float
    #: A short allowance above the steady rate, so a client that batches three
    #: calls on page load is not refused for behaving normally.
    burst: int = 0
    #: What the counter is keyed on. A subscription is the right default: two
    #: applications sharing a principal should not share a limit.
    applies_to: str = "subscription"
    description: str = ""

    def __post_init__(self) -> None:
        if self.requests <= 0:
            raise ValueError(f"tier {self.name!r} admits no requests at all")
        if self.window_seconds <= 0:
            raise ValueError(f"tier {self.name!r} has no window")

    @property
    def per_second(self) -> float:
        return self.requests / self.window_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requests": self.requests,
            "window_seconds": self.window_seconds,
            "burst": self.burst,
            "applies_to": self.applies_to,
            "description": self.description,
        }


#: The default tiers. Named after WSO2's, because an operator who has run one
#: already knows what `gold` means, and inventing new names buys nothing.
TIERS: Mapping[str, ThrottlePolicy] = {
    tier.name: tier for tier in (
        ThrottlePolicy("unlimited", requests=1_000_000, window_seconds=60,
                       description="no practical limit; for internal callers"),
        ThrottlePolicy("gold", requests=600, window_seconds=60, burst=30,
                       description="ten a second, sustained"),
        ThrottlePolicy("silver", requests=120, window_seconds=60, burst=10,
                       description="two a second"),
        ThrottlePolicy("bronze", requests=20, window_seconds=60, burst=5,
                       description="a third of a second; for evaluation"),
    )
}


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    """Whether this call passes, and everything needed to say why not."""

    allowed: bool
    policy: str
    limit: int
    remaining: int
    reset_at: float
    retry_after: float = 0.0
    reason: str = ""

    def headers(self) -> tuple[tuple[str, str], ...]:
        """The conventional rate-limit headers, so a generic client backs off
        correctly without knowing anything about this platform."""
        sent = [
            ("X-RateLimit-Limit", str(self.limit)),
            ("X-RateLimit-Remaining", str(max(0, self.remaining))),
            ("X-RateLimit-Reset", str(int(self.reset_at))),
            ("X-Slpie-Tier", self.policy),
        ]
        if not self.allowed:
            sent.append(("Retry-After", str(max(1, int(self.retry_after + 0.5)))))
        return tuple(sent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "tier": self.policy,
            "limit": self.limit,
            "remaining": max(0, self.remaining),
            "reset_at": self.reset_at,
            "retry_after": self.retry_after,
            "reason": self.reason,
        }


#: Keys idle for longer than this many windows are dropped. Without it a
#: gateway accumulates one deque per key it has ever seen, which is a slow leak
#: that only shows up in a long-running process — the kind nobody reproduces.
IDLE_WINDOWS = 2
SWEEP_EVERY = 1000


@dataclass
class Throttler:
    """Sliding-window counters, one per key.

    The clock is injected so the tests do not sleep. A rate-limit suite that
    waits for real seconds is a suite people stop running.
    """

    tiers: Mapping[str, ThrottlePolicy] = field(default_factory=lambda: dict(TIERS))
    now: Any = time.monotonic
    _hits: dict[str, Deque[float]] = field(default_factory=dict, repr=False)
    _calls: int = field(default=0, repr=False)

    def admit(self, key: str, tier: str) -> ThrottleDecision:
        policy = self.tiers.get(tier) or self.tiers.get("gold")
        if policy is None:  # pragma: no cover - only with an empty tier table
            raise KeyError(f"no throttle tier {tier!r} and no gold fallback")

        moment = float(self.now())
        window = self._hits.setdefault(key, deque())

        cutoff = moment - policy.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        ceiling = policy.requests + policy.burst
        reset_at = (window[0] + policy.window_seconds) if window else moment

        self._calls += 1
        if self._calls % SWEEP_EVERY == 0:
            self._sweep(moment)

        if len(window) >= ceiling:
            return ThrottleDecision(
                allowed=False,
                policy=policy.name,
                limit=policy.requests,
                remaining=0,
                reset_at=reset_at,
                retry_after=max(0.0, reset_at - moment),
                reason=(
                    f"tier {policy.name!r} admits {policy.requests} requests "
                    f"per {policy.window_seconds:g}s"
                    + (f" plus a burst of {policy.burst}" if policy.burst else "")
                ),
            )

        window.append(moment)
        return ThrottleDecision(
            allowed=True,
            policy=policy.name,
            limit=policy.requests,
            remaining=ceiling - len(window),
            reset_at=(window[0] + policy.window_seconds),
        )

    def _sweep(self, moment: float) -> None:
        longest = max(
            (policy.window_seconds for policy in self.tiers.values()), default=60.0,
        )
        stale = [
            key for key, window in self._hits.items()
            if not window or window[-1] < moment - longest * IDLE_WINDOWS
        ]
        for key in stale:
            self._hits.pop(key, None)

    def forget(self, key: str) -> None:
        self._hits.pop(key, None)

    def status(self) -> dict[str, Any]:
        """What the throttling screen renders."""
        return {
            "tiers": [policy.to_dict() for policy in self.tiers.values()],
            "tracked": len(self._hits),
            "calls": self._calls,
        }
