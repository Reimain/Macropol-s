"""The environment the documentation and the demo are both built from.

One manifest, used by the screenshot pass and by the demo builder, so the two
cannot describe different systems. A screenshot of one estate beside a demo of
another is the kind of drift that makes a reader stop trusting both.

It is deliberately a *simulated* estate rather than this repository: the
documentation should show what the console looks like against something with
services, boundaries, a database and an external provider in it, and this
repository has none of those.
"""

from __future__ import annotations

from pathlib import Path

MANIFEST = """
apiVersion: slpie/v1
environment: acme-production
target: simulated

security:
  concerns: [pci-dss, gdpr, soc2]
  boundaries:
    - name: cardholder-data
      contains: [payments, vault]

codebase:
  - root: ./services/payments
    team: payments
    domain: billing
  - root: ./services/orders
    team: fulfilment
    domain: commerce
  - root: ./services/vault
    team: security
    domain: billing

data:
  - folder: ./warehouse/schemas
    kind: schema
  - uri: postgres://analytics/orders
    kind: database
    classification: pii

network:
  - name: payments-api
    url: https://api.acme.com/v1
    kind: rest
  - name: order-events
    uri: kafka://broker/orders
    kind: event-stream

web:
  - name: storefront
    root: ./apps/storefront
    framework: next

providers:
  - name: stripe
    kind: external-api
"""


def build(root: str | Path):
    """Declare, materialise, attach, scan, govern and furnish. Returns the engine.

    Each step is allowed to fail loudly rather than being wrapped in a bare
    `except`: a screenshot pass that silently produced an empty console would
    publish a picture of nothing and call it documentation.

    ── Why this does more than scan ─────────────────────────────────────

    Three screens read things a bare engine does not have. Findings reads the
    projection `govern` fills; Workspaces reads a control plane, which a
    single-tenant install legitimately does not have; and Gateway, Analytics and
    Throttling read an API manager, which is a configuration rather than a
    default. Left unfurnished they render honest empty states — and an empty
    state is the *right* answer for a bare install and the *wrong* picture of
    the product, because a reader cannot tell "not configured" from "nothing to
    show" in a screenshot.

    So the demo world is a configured deployment rather than a fresh one. What
    is added here is configuration, not fabrication: every number those screens
    show is computed by the same code a real deployment runs.
    """
    from slpie.engine import Engine

    engine = Engine.from_text(MANIFEST)
    engine.declare()
    engine.simulate(root=str(root))
    engine.attach()
    engine.scan()
    _govern(engine)
    engine.plane = _plane()
    return engine


def _govern(engine) -> None:
    """Run the rules, so the Findings screen has the estate's findings on it."""
    from slpie.compose import Composition, Context
    from slpie.compose.registry import registry

    Composition.read("scan | govern", verbs=registry()).run(
        Context(engine=engine, root=str(engine.world.root) if engine.world else ""))


class _DemoSpawner:
    """A runtime that hands back what it would have started.

    `Runtime.LOCAL` is declared in `slpie/workspace/spawn.py` and implemented by
    nothing, so a control plane built without a spawner leaves every workspace
    un-started — and un-started workspaces consume no quota, which is why the
    admin screens showed three workspaces and zero usage. That gap is recorded
    in `docs/AUDIT.md`; this is the demo standing in for it, deliberately in
    `tools/` rather than in the kernel, because adding a kernel runtime to make
    a screenshot look better is the wrong order to do things in.

    It starts nothing. `plan()` renders exactly what a real local runtime would
    create, which is the honest half, and `start()` reports it as running so the
    quota arithmetic has something to count.
    """

    def __init__(self) -> None:
        from slpie.workspace.spawn import Runtime

        self.runtime = Runtime.LOCAL
        self._running: dict[str, dict] = {}

    def plan(self, request):
        return ({
            "kind": "directory",
            "path": f"/var/lib/slpie/{request.workspace_id}",
            "cpu": request.allocation.cpu,
            "memory_mb": request.allocation.memory_mb,
        },)

    def start(self, request):
        from slpie.workspace.spawn import Started

        self._running[request.workspace_id] = {"plan": self.plan(request)}
        return Started(
            workspace_id=request.workspace_id, runtime=self.runtime,
            url=f"http://127.0.0.1/workspaces/{request.workspace_id}",
            node="localhost", detail="a directory on this machine",
        )

    def stop(self, workspace_id: str) -> bool:
        return self._running.pop(workspace_id, None) is not None

    def status(self, workspace_id: str):
        return {"running": workspace_id in self._running, "runtime": self.runtime.value}


def _plane():
    """Two tenants, their quotas, their datasets and a live workspace each.

    `acme` is near its ceiling and `globex` is not, because a quota screen where
    every bar is at 10% shows the layout and not the point — the reader needs to
    see what approaching a limit looks like.
    """
    from slpie.identity.principal import Principal
    from slpie.rbac import AccessEngine, Role, Scope, allow, system_roles
    from slpie.workspace import (
        Allocation,
        ControlPlane,
        Dataset,
        DatasetGrant,
        Quota,
        Visibility,
    )

    def principal(subject: str, tenant: str) -> Principal:
        return Principal(
            issuer="https://id.acme.test", subject=subject, tenant=tenant,
            email=f"{subject}@{tenant}.test", email_verified=True,
        )

    roles = system_roles()
    # `analyst` is already a system role, and it grants asking and scanning
    # rather than provisioning. This is the seat that opens a workspace.
    roles.add(Role(
        name="workspace-user",
        permissions=(allow("workspace.create", "workspace"),
                     allow("dataset.read", "*")),
        description="opens a workspace and reads what their tenant granted",
    ))
    plane = ControlPlane(access=AccessEngine(roles), region="eu-west-1",
                         spawner=_DemoSpawner())

    plane.set_quota("acme", Quota(max_workspaces=4, max_cpu=16.0,
                                  max_memory_mb=32_768, max_disk_gb=500))
    plane.set_quota("globex", Quota(max_workspaces=2, max_cpu=4.0,
                                    max_memory_mb=8_192, max_disk_gb=100))

    for tenant, datasets in (
        ("acme", ("orders", "payments-ledger", "cardholder-data")),
        ("globex", ("revenue",)),
    ):
        for name in datasets:
            plane.grant(DatasetGrant(
                dataset=Dataset(name=name, scope=Scope(tenant=tenant)),
                visibility=Visibility.TENANT, granted_by="platform",
            ))

    for tenant, who, size in (
        ("acme", "ada", Allocation(cpu=4.0, memory_mb=8_192, disk_gb=120)),
        ("acme", "bo", Allocation(cpu=8.0, memory_mb=16_384, disk_gb=250)),
        ("globex", "zed", Allocation(cpu=2.0, memory_mb=4_096, disk_gb=40)),
    ):
        actor = principal(who, tenant)
        plane.access.bind(actor.urn, "workspace-user", scope=Scope(tenant=tenant))
        plane.provision(actor, scope=Scope(tenant=tenant), allocation=size)

    return plane
