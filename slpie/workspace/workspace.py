"""One user's environment: what it is, and what state it is in.

A workspace has an identity, a scope, an allocation and a lifecycle. It does not
have a URL, a pod name or a volume — those belong to whichever runtime is making
it real, and a model that held them would be a model that could only describe the
runtime it was written against.

The state machine is small and the illegal transitions are refused rather than
logged. `RUNNING → REQUESTED` in particular: a workspace cannot go backwards, and
a bug that tried would otherwise produce a second pod for a user who already had
one, with both writing the same volume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from ..domain.identity import digest
from ..errors import SlpieError
from ..rbac import Scope
from .quota import Allocation


class WorkspaceError(SlpieError):
    """A workspace is malformed, or was asked for a transition it cannot make."""


#: A workspace id becomes a Kubernetes object name, a directory, and part of a
#: hostname. That intersection is narrow: RFC 1123 label rules.
IDENTIFIER = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class State(str, Enum):
    """Where a workspace is in its life."""

    REQUESTED = "requested"    # decided and authorised, nothing created yet
    STARTING = "starting"      # the runtime is building it
    RUNNING = "running"        # a user can reach it
    IDLE = "idle"              # running, but nobody has touched it
    STOPPING = "stopping"      # draining
    STOPPED = "stopped"        # reclaimed; the volume may survive
    FAILED = "failed"          # it did not come up, and we know why

    @property
    def live(self) -> bool:
        """Whether this state consumes the tenant's quota."""
        return self in (State.STARTING, State.RUNNING, State.IDLE, State.STOPPING)

    @property
    def terminal(self) -> bool:
        return self in (State.STOPPED, State.FAILED)


#: The only transitions that exist. Anything else is a bug, and the cheapest
#: place to find it is the moment it is attempted.
TRANSITIONS: dict[State, frozenset[State]] = {
    State.REQUESTED: frozenset({State.STARTING, State.FAILED, State.STOPPED}),
    State.STARTING: frozenset({State.RUNNING, State.FAILED, State.STOPPING}),
    State.RUNNING: frozenset({State.IDLE, State.STOPPING, State.FAILED}),
    State.IDLE: frozenset({State.RUNNING, State.STOPPING, State.FAILED}),
    State.STOPPING: frozenset({State.STOPPED, State.FAILED}),
    State.STOPPED: frozenset({State.REQUESTED}),   # restarting is a new request
    State.FAILED: frozenset({State.REQUESTED}),
}


@dataclass(frozen=True, slots=True)
class Workspace:
    """One user's notebook environment."""

    principal_urn: str
    scope: Scope
    allocation: Allocation = field(default_factory=Allocation)
    state: State = State.REQUESTED
    workspace_id: str = ""
    created_at: int = 0
    last_seen_at: int = 0
    detail: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.principal_urn:
            raise WorkspaceError(
                "a workspace names no principal; one nobody owns cannot be "
                "billed, revoked, or explained to an auditor"
            )
        if not self.scope.tenant:
            raise WorkspaceError(
                f"workspace for {self.principal_urn} has no tenant. A global "
                f"workspace would see every tenant's datasets, which is the one "
                f"outcome this whole package exists to prevent"
            )
        if not self.workspace_id:
            object.__setattr__(self, "workspace_id", self._minted())
        if not IDENTIFIER.match(self.workspace_id):
            raise WorkspaceError(
                f"{self.workspace_id!r} is not a usable workspace id: it becomes "
                f"a Kubernetes object name, a directory and part of a hostname, "
                f"so it must satisfy the narrowest of those (RFC 1123)"
            )

    def _minted(self) -> str:
        """A stable id for this principal in this scope.

        Derived rather than random, so the same user asking twice gets the same
        workspace rather than a second one — which is what stops a refreshed
        browser tab from doubling a tenant's bill.
        """
        short = digest({
            "principal": self.principal_urn,
            "tenant": self.scope.tenant,
            "realm": self.scope.realm,
        }, size=8)
        return f"ws-{short}"

    @property
    def namespace(self) -> str:
        """The Kubernetes namespace this belongs in. One per tenant."""
        return f"slpie-{self.scope.tenant}"

    @property
    def live(self) -> bool:
        return self.state.live

    def can_become(self, state: State) -> bool:
        return state in TRANSITIONS[self.state] or state is self.state

    def become(self, state: State, *, detail: str = "", now: int = 0) -> "Workspace":
        """Move to a new state, or refuse.

        Refuses rather than logs. A workspace that went `RUNNING → REQUESTED`
        would be provisioned a second time while the first was still writing its
        volume, and two kernels on one volume corrupt a notebook in a way that
        looks like the user's fault.
        """
        if state is self.state:
            return self if not detail else replace(self, detail=detail)
        if state not in TRANSITIONS[self.state]:
            raise WorkspaceError(
                f"workspace {self.workspace_id} cannot go from "
                f"{self.state.value} to {state.value}; the only moves from "
                f"{self.state.value} are "
                f"{', '.join(sorted(item.value for item in TRANSITIONS[self.state]))}"
            )
        return replace(
            self, state=state, detail=detail,
            last_seen_at=now or self.last_seen_at,
        )

    def touched(self, *, now: int) -> "Workspace":
        """Record activity. An idle workspace becomes running again."""
        moved = self.become(State.RUNNING, now=now) if self.state is State.IDLE else self
        return replace(moved, last_seen_at=now)

    def idle_for(self, *, now: int) -> float:
        """Minutes since anybody touched it."""
        if not self.last_seen_at:
            return 0.0
        return round((now - self.last_seen_at) / 60_000_000_000, 2)

    def reclaimable(self, *, now: int) -> bool:
        """Whether it has been idle past its allowance.

        The single most effective cost control the platform has: the expensive
        failure is never a busy workspace, it is four hundred idle ones.
        """
        if not self.state.live:
            return False
        return self.idle_for(now=now) >= self.allocation.idle_timeout_minutes

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "principal_urn": self.principal_urn,
            "scope": self.scope.to_dict(),
            "namespace": self.namespace,
            "allocation": self.allocation.to_dict(),
            "state": self.state.value, "live": self.live,
            "created_at": self.created_at, "last_seen_at": self.last_seen_at,
            "detail": self.detail, "labels": dict(self.labels),
        }

    def __str__(self) -> str:
        return (
            f"{self.workspace_id} [{self.state.value}] "
            f"{self.principal_urn} in {self.scope}"
        )
