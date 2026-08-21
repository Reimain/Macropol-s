"""The control plane: decide, place, provision, reclaim.

This is where the rules meet. Everything else in the package is a type; this is
the only file that *decides* anything, and it exists as one file so the decision
cannot be made two subtly different ways in two places.

The order of `provision` is the security argument, and it is deliberate:

    1. authorise      does this principal hold a role that permits a workspace
                      in this scope?  (`AccessEngine.check`, default-deny)
    2. admit          does the tenant's quota have room?
    3. narrow         which datasets does this principal reach — filtered by
                      *both* the grant's own reach and a second RBAC decision
    4. narrow         which environment variables belong to this scope
    5. hand over      a fully resolved `SpawnRequest`, and only then a runtime

A runtime never sees a principal id it could look something up with. By the time
it is called, every question of entitlement has already been answered, so a
compromised or simply buggy spawner cannot widen access — it has nothing to widen
access *with*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..errors import SlpieError
from ..identity.principal import Principal
from ..rbac import AccessEngine, Outcome, Scope
from .dataset import DatasetGrant, Visibility
from .quota import Allocation, Quota, Usage
from .spawn import Spawner, SpawnRequest, Started
from .workspace import State, Workspace, WorkspaceError


class PlaneError(SlpieError):
    """The control plane refused to provision, and says which rule stopped it."""


#: The action a principal must hold to get a workspace at all. Named as data so
#: it appears in `AccessEngine.permitted()` alongside every other action rather
#: than being a special case somebody has to know about.
#: Dotted, not colon-separated. `matches_action` understands `workspace.*` and
#: has never understood `workspace:*`, so the colon form could not be granted by
#: any wildcard: an operator writing `allow workspace.* on "*"` got nothing, and
#: nothing said so. The colon is also already load-bearing on the other side of
#: the pair — resources are `env:prod/*`, `dataset:sales` — so using it for
#: actions made two unlike things look alike.
WORKSPACE_ACTION = "workspace.create"
DATASET_ACTION = "dataset.read"


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a workspace was put, and why.

    Regions are deferred, so today this records the decision and its reason with
    a single region. The type exists now because retrofitting "why here?" onto a
    platform that never recorded it means the answer is lost for everything
    already running.
    """

    workspace_id: str
    region: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id, "region": self.region,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Provisioned:
    """What came back: the workspace, where it went, and what it can see."""

    workspace: Workspace
    placement: Placement
    grants: tuple[DatasetGrant, ...]
    started: Started | None = None
    plan: tuple[Mapping[str, Any], ...] = ()

    @property
    def running(self) -> bool:
        return self.started is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace.to_dict(),
            "placement": self.placement.to_dict(),
            "grants": [grant.to_dict() for grant in self.grants],
            "started": self.started.to_dict() if self.started else None,
            "plan_objects": len(self.plan),
        }


class ControlPlane:
    """Decides who gets a workspace, holding what, and how much of it."""

    def __init__(
        self,
        *,
        access: AccessEngine,
        spawner: Spawner | None = None,
        region: str = "local",
        default_allocation: Allocation | None = None,
    ) -> None:
        self.access = access
        self.spawner = spawner
        self.region = region
        self.default_allocation = default_allocation or Allocation()

        self._workspaces: dict[str, Workspace] = {}
        self._quotas: dict[str, Quota] = {}
        self._grants: list[DatasetGrant] = []
        self._environment: dict[tuple[str, str], dict[str, str]] = {}

    # -- what the tenant bought ------------------------------------------

    def set_quota(self, tenant: str, quota: Quota) -> None:
        self._quotas[tenant] = quota

    def quota_of(self, tenant: str) -> Quota:
        return self._quotas.get(tenant, Quota())

    def usage_of(self, tenant: str) -> Usage:
        """What the tenant is consuming. Counted from live workspaces only."""
        usage = Usage()
        for workspace in self._workspaces.values():
            if workspace.scope.tenant == tenant and workspace.live:
                usage = usage.plus(workspace.allocation)
        return usage

    # -- what exists -----------------------------------------------------

    @property
    def grants(self) -> tuple[DatasetGrant, ...]:
        """Every grant, unfiltered.

        The administrative catalogue route reads this — it lists what a tenant
        holds, which is a different question from what a principal may see, and
        `datasets_for` answers that one. It was reading `getattr(plane,
        "grants", ())` against a plane that kept them in `_grants` with no
        accessor, so the Catalog screen listed nothing in every deployment.
        """
        return tuple(self._grants)

    def grant(self, grant: DatasetGrant) -> DatasetGrant:
        self._grants.append(grant)
        return grant

    def set_environment(
        self, scope: Scope, values: Mapping[str, str],
    ) -> None:
        """Environment variables bound to a scope.

        Keyed by `(tenant, realm)` so a realm's values never reach a sibling
        realm. A tenant-level entry (`realm=""`) is inherited by every realm in
        that tenant, which is how a shared endpoint is configured once.
        """
        self._environment[(scope.tenant, scope.realm)] = dict(values)

    def environment_for(self, scope: Scope) -> dict[str, str]:
        """Tenant-level values, overlaid by realm-level ones.

        Never crosses a tenant. A realm value shadows a tenant value of the same
        name, which is the ordinary override people expect.
        """
        merged = dict(self._environment.get((scope.tenant, ""), {}))
        if scope.realm:
            merged.update(self._environment.get((scope.tenant, scope.realm), {}))
        return merged

    # -- what a principal may see ----------------------------------------

    def datasets_for(
        self, principal: Principal, *, scope: Scope, now: float | None = None,
    ) -> tuple[DatasetGrant, ...]:
        """Every grant this principal reaches in this scope.

        Two independent conditions, both required:

        * the **grant's own reach** — is this principal, in this scope, inside
          the visibility the grant was written with;
        * an **RBAC decision** — does this principal hold a role permitting
          `dataset.read` on that dataset in the scope that owns it.

        Requiring both means a bug in either narrows access rather than widening
        it, which is the only safe direction for a bug in this function to fail.
        """
        found: list[DatasetGrant] = []
        for grant in self._grants:
            if not grant.reaches(principal_urn=principal.urn, scope=scope):
                continue
            # A public corpus is owned globally, and a tenant-scoped role does
            # not reach the global scope — deliberately, so a tenant
            # administrator cannot gain the platform. So for a public grant the
            # owner has already decided everybody may read it, and the only
            # remaining question is whether this principal holds a read role at
            # all, which is asked in *their* scope. Every other visibility is
            # checked in the scope that owns the data.
            asked = scope if grant.visibility is Visibility.PUBLIC else grant.dataset.scope
            decision = self.access.check(
                principal, DATASET_ACTION, grant.dataset.name,
                scope=asked, now=now,
            )
            if decision.outcome is Outcome.ALLOWED:
                found.append(grant)
        return tuple(found)

    # -- provisioning ----------------------------------------------------

    def provision(
        self,
        principal: Principal,
        *,
        scope: Scope,
        allocation: Allocation | None = None,
        image: str = "",
        start: bool = True,
        now: int | None = None,
    ) -> Provisioned:
        """Authorise, admit, narrow, and hand a resolved request to the runtime."""
        moment = now if now is not None else time.time_ns()
        wanted = allocation or self.default_allocation

        if not scope.tenant:
            raise PlaneError(
                "a workspace must be provisioned into a tenant; a global one "
                "would reach every tenant's datasets"
            )

        # 1 — authorise. Default deny: no role, no workspace.
        decision = self.access.check(
            principal, WORKSPACE_ACTION, "workspace", scope=scope,
            now=moment / 1_000_000_000,
        )
        if decision.outcome is not Outcome.ALLOWED:
            raise PlaneError(
                f"{principal.urn} may not create a workspace in {scope}: "
                f"{decision.reason or decision.outcome.value}"
            )

        # 2 — admit. The tenant's ceiling, with the limit that stopped it named.
        quota = self.quota_of(scope.tenant)
        fits, why = quota.admits(self.usage_of(scope.tenant), wanted)
        if not fits:
            raise PlaneError(f"cannot provision in {scope.tenant}: {why}")

        workspace = Workspace(
            principal_urn=principal.urn, scope=scope, allocation=wanted,
            created_at=moment, last_seen_at=moment,
        )
        existing = self._workspaces.get(workspace.workspace_id)
        if existing is not None and not existing.state.terminal:
            # The id is derived from principal and scope, so a refreshed browser
            # tab lands here rather than doubling the tenant's bill.
            return Provisioned(
                workspace=existing,
                placement=Placement(existing.workspace_id, self.region,
                                    "already provisioned"),
                grants=self.datasets_for(principal, scope=scope),
            )

        # 3, 4 — narrow to what this principal and scope may reach.
        grants = self.datasets_for(principal, scope=scope)
        environment = self.environment_for(scope)

        placement = Placement(
            workspace.workspace_id, self.region,
            f"single region ({self.region}); cross-region placement is deferred",
        )

        request = SpawnRequest(
            workspace_id=workspace.workspace_id,
            tenant=scope.tenant, realm=scope.realm,
            principal_urn=principal.urn,
            allocation=wanted, grants=grants, environment=environment,
            image=image, labels={"region": self.region},
        )

        plan: tuple[Mapping[str, Any], ...] = ()
        started: Started | None = None
        if self.spawner is not None:
            plan = tuple(self.spawner.plan(request))
            if start:
                workspace = workspace.become(State.STARTING, now=moment)
                try:
                    started = self.spawner.start(request)
                except Exception as error:  # noqa: BLE001 - a runtime may fail anyhow
                    workspace = workspace.become(
                        State.FAILED, detail=str(error)[:200], now=moment,
                    )
                    self._workspaces[workspace.workspace_id] = workspace
                    raise PlaneError(
                        f"{workspace.workspace_id} did not start: {error}"
                    ) from error
                workspace = workspace.become(State.RUNNING, now=moment)

        self._workspaces[workspace.workspace_id] = workspace
        return Provisioned(
            workspace=workspace, placement=placement, grants=grants,
            started=started, plan=plan,
        )

    # -- lifecycle -------------------------------------------------------

    def touch(self, workspace_id: str, *, now: int | None = None) -> Workspace:
        workspace = self.require(workspace_id)
        moved = workspace.touched(now=now if now is not None else time.time_ns())
        self._workspaces[workspace_id] = moved
        return moved

    def reclaim(self, workspace_id: str, *, reason: str = "") -> Workspace:
        """Stop a workspace and release its share of the quota."""
        workspace = self.require(workspace_id)
        if workspace.state.terminal:
            return workspace
        # A workspace that never started has nothing to drain, and REQUESTED
        # cannot reach STOPPING — it goes straight to STOPPED.
        draining = (
            workspace.become(State.STOPPING, detail=reason)
            if workspace.can_become(State.STOPPING) else workspace
        )
        if self.spawner is not None:
            self.spawner.stop(workspace_id)
        stopped = draining.become(State.STOPPED, detail=reason)
        self._workspaces[workspace_id] = stopped
        return stopped

    def reclaim_idle(self, *, now: int | None = None) -> tuple[Workspace, ...]:
        """Every workspace past its idle allowance. What a sweeper calls."""
        moment = now if now is not None else time.time_ns()
        return tuple(
            self.reclaim(
                workspace.workspace_id,
                reason=(
                    f"idle {workspace.idle_for(now=moment):.0f}m, past the "
                    f"{workspace.allocation.idle_timeout_minutes}m allowance"
                ),
            )
            for workspace in list(self._workspaces.values())
            if workspace.reclaimable(now=moment)
        )

    # -- reading ---------------------------------------------------------

    def require(self, workspace_id: str) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceError(f"no workspace {workspace_id!r}")
        return workspace

    def workspaces(
        self, *, tenant: str = "", live_only: bool = False,
    ) -> tuple[Workspace, ...]:
        return tuple(sorted(
            (
                workspace for workspace in self._workspaces.values()
                if (not tenant or workspace.scope.tenant == tenant)
                and (not live_only or workspace.live)
            ),
            key=lambda item: item.workspace_id,
        ))

    def status(self) -> dict[str, Any]:
        """What an administrator's console renders."""
        tenants = sorted({w.scope.tenant for w in self._workspaces.values()})
        return {
            "region": self.region,
            "runtime": getattr(self.spawner, "runtime", None)
            and self.spawner.runtime.value,
            "workspaces": len(self._workspaces),
            "live": sum(1 for w in self._workspaces.values() if w.live),
            "tenants": [
                {
                    "tenant": tenant,
                    "usage": self.usage_of(tenant).to_dict(),
                    "quota": self.quota_of(tenant).to_dict(),
                    "headroom": self.quota_of(tenant).headroom(
                        self.usage_of(tenant)
                    ),
                }
                for tenant in tenants
            ],
            "datasets": len(self._grants),
        }
