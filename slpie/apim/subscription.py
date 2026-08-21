"""Applications and their subscriptions, as an append-only ledger.

This copies `connectors/keyring.py`'s eight methods almost exactly — an
append-only log, a derived current map, supersede-on-resubscribe, a mandatory
reason on revoke, `expiring()`, `history()`, `at(sequence)` and `status()`.

That is not laziness. An operator who has learned the connector keyring already
knows this, and one shape learned twice is cheaper than two shapes learned once.
It also inherits the property that matters: **"we granted this" and "we took it
away, and why" both stay answerable**, because nothing is ever overwritten.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..connectors.keyring import GrantStatus
from .errors import ApimError, SubscriptionRefused

#: How far ahead `expiring()` looks by default. A week is long enough to renew
#: and short enough that the list is still worth reading.
RENEWAL_WINDOW = 7 * 24 * 3600.0


@dataclass(frozen=True, slots=True)
class Subscription:
    """One application's access to one API version."""

    application_id: str
    api_id: str
    api_version: str = "v1"
    throttle: str = "gold"
    status: GrantStatus = GrantStatus.ACTIVE
    subscribed_at: float = 0.0
    expires_at: float = 0.0
    principal_urn: str = ""
    tenant: str = ""
    reason: str = ""
    sequence: int = 0

    @property
    def id(self) -> str:
        return f"{self.application_id}:{self.api_id}@{self.api_version}"

    def expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def expiring(self, *, now: float | None = None, window: float = RENEWAL_WINDOW) -> bool:
        if not self.expires_at:
            return False
        moment = now if now is not None else time.time()
        return not self.expired(now=moment) and self.expires_at - moment <= window

    def usable(self, *, now: float | None = None) -> bool:
        return self.status is GrantStatus.ACTIVE and not self.expired(now=now)

    def replacing(self, **changes: Any) -> "Subscription":
        """A copy with `changes` applied.

        `dataclasses.replace` would do, and this exists because every call site
        here is a *supersession* — a new row that keeps the old one's facts —
        and naming it that way makes the append-only discipline visible at the
        call site rather than inferable from it.
        """
        return Subscription(**{
            "application_id": self.application_id,
            "api_id": self.api_id,
            "api_version": self.api_version,
            "throttle": self.throttle,
            "status": self.status,
            "subscribed_at": self.subscribed_at,
            "expires_at": self.expires_at,
            "principal_urn": self.principal_urn,
            "tenant": self.tenant,
            "reason": self.reason,
            "sequence": self.sequence,
            **changes,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "application_id": self.application_id,
            "api_id": self.api_id,
            "api_version": self.api_version,
            "throttle": self.throttle,
            "status": self.status.value,
            "subscribed_at": self.subscribed_at,
            "expires_at": self.expires_at,
            "principal_urn": self.principal_urn,
            "tenant": self.tenant,
            "reason": self.reason,
            "sequence": self.sequence,
        }

    def __str__(self) -> str:
        return f"{self.id} ({self.status.value}, tier {self.throttle})"


@dataclass
class SubscriptionLedger:
    """Append-only. Current state is derived, never stored twice."""

    now: Any = time.time
    _log: list[Subscription] = field(default_factory=list, repr=False)
    _current: dict[tuple[str, str], Subscription] = field(
        default_factory=dict, repr=False,
    )
    _sequence: int = field(default=0, repr=False)

    def __len__(self) -> int:
        return len(self._current)

    def __iter__(self) -> Iterator[Subscription]:
        return iter(sorted(self._current.values(), key=lambda item: item.id))

    def subscribe(
        self,
        application_id: str,
        api_id: str,
        *,
        api_version: str = "v1",
        throttle: str = "gold",
        expires_at: float = 0.0,
        principal_urn: str = "",
        tenant: str = "",
    ) -> Subscription:
        """Subscribe, superseding any live subscription for the same pair.

        Superseding rather than mutating is what keeps a tier change auditable:
        "this application was on bronze until Tuesday" stays answerable.
        """
        if not application_id or not api_id:
            raise ApimError("a subscription needs both an application and an API")

        key = (application_id, api_id)
        held = self._current.get(key)
        if held is not None and held.status is GrantStatus.ACTIVE:
            self._append(held.replacing(
                status=GrantStatus.SUPERSEDED, reason="resubscribed",
            ))

        return self._append(Subscription(
            application_id=application_id,
            api_id=api_id,
            api_version=api_version,
            throttle=throttle,
            status=GrantStatus.ACTIVE,
            subscribed_at=float(self.now()),
            expires_at=expires_at,
            principal_urn=principal_urn,
            tenant=tenant,
        ))

    def revoke(self, application_id: str, api_id: str, *, reason: str) -> Subscription:
        """Revoke, with a reason. An empty one is refused.

        The same rule `Keyring.revoke` has, for the same reason: somebody will
        ask why this application stopped working, and "it was revoked" is not an
        answer to that question.
        """
        if not reason.strip():
            raise ApimError(
                "revoking a subscription needs a reason; somebody will ask why "
                "this application stopped working"
            )
        held = self._current.get((application_id, api_id))
        if held is None or held.status is not GrantStatus.ACTIVE:
            raise SubscriptionRefused(
                f"{application_id} holds no live subscription to {api_id}"
            )
        return self._append(held.replacing(status=GrantStatus.REVOKED, reason=reason))

    def find(
        self, application_id: str, api_id: str, *, now: float | None = None,
    ) -> Subscription | None:
        """The live subscription for this pair, or nothing."""
        held = self._current.get((application_id, api_id))
        if held is None:
            return None
        return held if held.usable(now=now) else None

    def sweep(self, *, now: float | None = None) -> tuple[Subscription, ...]:
        """Move expired subscriptions to `EXPIRED`, as ledger facts."""
        moment = now if now is not None else float(self.now())
        moved = []
        for held in list(self._current.values()):
            if held.status is GrantStatus.ACTIVE and held.expired(now=moment):
                moved.append(self._append(
                    held.replacing(status=GrantStatus.EXPIRED, reason="expired"),
                ))
        return tuple(moved)

    def expiring(
        self, *, now: float | None = None, window: float = RENEWAL_WINDOW,
    ) -> tuple[Subscription, ...]:
        return tuple(
            held for held in self
            if held.status is GrantStatus.ACTIVE and held.expiring(now=now, window=window)
        )

    def of(self, application_id: str) -> tuple[Subscription, ...]:
        return tuple(held for held in self if held.application_id == application_id)

    def history(
        self, application_id: str = "", api_id: str = "",
    ) -> tuple[Subscription, ...]:
        """Everything that ever happened to this pair, oldest first."""
        return tuple(
            entry for entry in self._log
            if (not application_id or entry.application_id == application_id)
            and (not api_id or entry.api_id == api_id)
        )

    def at(self, sequence: int) -> dict[tuple[str, str], Subscription]:
        """What was live at a point in the log. The bitemporal question, asked
        of subscriptions rather than of the graph."""
        state: dict[tuple[str, str], Subscription] = {}
        for entry in self._log:
            if entry.sequence > sequence:
                break
            state[(entry.application_id, entry.api_id)] = entry
        return state

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        live = [held for held in self if held.usable(now=now)]
        return {
            "subscriptions": len(self._current),
            "live": len(live),
            "expiring": len(self.expiring(now=now)),
            "events": len(self._log),
            "by_tier": {
                tier: sum(1 for held in live if held.throttle == tier)
                for tier in sorted({held.throttle for held in live})
            },
        }

    def _append(self, entry: Subscription) -> Subscription:
        self._sequence += 1
        stamped = entry.replacing(sequence=self._sequence)
        self._log.append(stamped)
        self._current[(stamped.application_id, stamped.api_id)] = stamped
        return stamped
