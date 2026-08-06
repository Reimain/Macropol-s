"""The seam a runtime implements to start a notebook.

`Spawner` is a protocol, not a class to inherit. The kernel decides *what* a
workspace is entitled to; a runtime decides *how* to make that real — a
Kubernetes pod, a container, a subprocess. Neither knows about the other beyond
this file, which is the same arrangement `GraphStore` has with SQLite and
Postgres.

**A request is fully resolved before it reaches a spawner.** By the time
`SpawnRequest` exists, the RBAC decision has been made, the quota has been
checked, the datasets have been filtered to what this principal may see, and the
environment has been narrowed to what this scope is allowed. A spawner that
received a user id and went looking would be a spawner that could be asked for
somebody else's workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..errors import SlpieError
from .dataset import DatasetGrant
from .quota import Allocation


class SpawnError(SlpieError):
    """A workspace could not be started, or was asked for improperly."""


class Runtime(str, Enum):
    """Which implementation is in use. Reported, so an answer says where it ran."""

    KUBERNETES = "kubernetes"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    """Everything needed to start one workspace, already authorised.

    Note what is *not* here: a principal's roles, a tenant's quota, the full
    dataset catalogue. Those were inputs to the decision, not inputs to the
    spawn — passing them on would let a runtime re-derive an answer the control
    plane already reached, and re-derivation is where two implementations start
    to disagree.
    """

    workspace_id: str
    tenant: str
    realm: str
    principal_urn: str
    allocation: Allocation
    #: Already filtered to what this principal may see. A spawner mounts these
    #: and nothing else.
    grants: tuple[DatasetGrant, ...] = ()
    #: Already narrowed to this scope. Values may be secret, so a spawner is
    #: expected to place them somewhere a `kubectl describe` will not show.
    environment: Mapping[str, str] = field(default_factory=dict)
    image: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id:
            raise SpawnError("a spawn request needs a workspace id")
        if not self.principal_urn:
            raise SpawnError(
                "a spawn request names no principal; a workspace nobody owns is "
                "one nobody can be billed for or have revoked"
            )

    @property
    def writable_grants(self) -> tuple[DatasetGrant, ...]:
        return tuple(grant for grant in self.grants if grant.writable)

    @property
    def read_only_grants(self) -> tuple[DatasetGrant, ...]:
        return tuple(grant for grant in self.grants if not grant.writable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id, "tenant": self.tenant,
            "realm": self.realm, "principal_urn": self.principal_urn,
            "allocation": self.allocation.to_dict(),
            "grants": [grant.to_dict() for grant in self.grants],
            # Names only. The values are secret by assumption, and a to_dict()
            # that leaked them would leak them into every log line that ever
            # rendered a request.
            "environment": sorted(self.environment),
            "image": self.image, "labels": dict(self.labels),
        }


@dataclass(frozen=True, slots=True)
class Started:
    """What a runtime returns once the workspace is running."""

    workspace_id: str
    runtime: Runtime
    url: str = ""
    token: str = ""
    node: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id, "runtime": self.runtime.value,
            "url": self.url, "node": self.node, "detail": self.detail,
            # Deliberately not the token.
            "has_token": bool(self.token),
        }


@runtime_checkable
class Spawner(Protocol):
    """What a runtime must implement.

    Four operations, and `plan` is the one that makes this testable. It renders
    exactly what `start` would create without creating it — the same plan/apply
    split `slpie/binding/guard.py` applies to a live target, for the same
    reason: the decision should be reviewable while it is still free.
    """

    runtime: Runtime

    def plan(self, request: SpawnRequest) -> Sequence[Mapping[str, Any]]:
        """What `start` would create. Touches nothing."""
        ...

    def start(self, request: SpawnRequest) -> Started:
        """Make it real."""
        ...

    def stop(self, workspace_id: str) -> bool:
        """Reclaim it. True if something was there to reclaim."""
        ...

    def status(self, workspace_id: str) -> Mapping[str, Any]:
        """What is running, as far as the runtime can see."""
        ...
