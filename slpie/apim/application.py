"""An application: the consumer a subscription and a credential belong to.

The unit of subscription is deliberately *not* the principal. Two services run
by the same team share a person's identity and should not share a rate limit or
a revocation — revoking one team member's access to a batch job should not take
the interactive dashboard down with it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .errors import ApimError


@dataclass(frozen=True, slots=True)
class Application:
    """One consumer of the platform's APIs."""

    application_id: str
    name: str
    owner_urn: str
    tenant: str = ""
    throttle: str = "gold"
    description: str = ""
    state: str = "active"           # active | blocked
    created_at: float = 0.0
    labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.application_id:
            raise ApimError("an application must have an id")
        if not self.owner_urn:
            # An application nobody owns is an application nobody can be asked
            # about when it starts behaving oddly at three in the morning.
            raise ApimError(f"application {self.application_id!r} has no owner")

    @property
    def usable(self) -> bool:
        return self.state == "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "name": self.name,
            "owner_urn": self.owner_urn,
            "tenant": self.tenant,
            "throttle": self.throttle,
            "description": self.description,
            "state": self.state,
            "created_at": self.created_at,
            "labels": dict(self.labels),
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.application_id}, {self.state})"


@dataclass
class Applications:
    """The register. Small on purpose — the interesting state is elsewhere."""

    now: Any = time.time
    _by_id: dict[str, Application] = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Application]:
        return iter(sorted(self._by_id.values(), key=lambda item: item.application_id))

    def register(
        self,
        application_id: str,
        name: str,
        owner_urn: str,
        *,
        tenant: str = "",
        throttle: str = "gold",
        description: str = "",
    ) -> Application:
        if application_id in self._by_id:
            raise ApimError(f"application {application_id!r} is already registered")
        built = Application(
            application_id=application_id, name=name, owner_urn=owner_urn,
            tenant=tenant, throttle=throttle, description=description,
            created_at=float(self.now()),
        )
        self._by_id[application_id] = built
        return built

    def get(self, application_id: str) -> Application | None:
        return self._by_id.get(application_id)

    def block(self, application_id: str, *, reason: str) -> Application:
        """Blocked, not deleted. A deleted application takes its history with
        it, and the history is what answers "what was this calling"."""
        if not reason.strip():
            raise ApimError("blocking an application needs a reason")
        held = self._by_id.get(application_id)
        if held is None:
            raise ApimError(f"no application {application_id!r}")
        blocked = Application(
            application_id=held.application_id, name=held.name,
            owner_urn=held.owner_urn, tenant=held.tenant, throttle=held.throttle,
            description=held.description, state="blocked",
            created_at=held.created_at,
            labels=held.labels + (("blocked_because", reason),),
        )
        self._by_id[application_id] = blocked
        return blocked

    def status(self) -> dict[str, Any]:
        return {
            "applications": len(self._by_id),
            "active": sum(1 for item in self if item.usable),
            "by_tier": {
                tier: sum(1 for item in self if item.throttle == tier)
                for tier in sorted({item.throttle for item in self})
            },
        }
