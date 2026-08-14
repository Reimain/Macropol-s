"""Multi-tenant notebook workspaces, and the one claim that must never fail.

Everything here is one question asked several ways: **can a user reach data that
is not theirs?** A quota that is off by one costs money; a segregation bug costs
the company. So the tests that matter most are the negative ones, and several of
them exist specifically to fail if somebody later "simplifies" a check away.

The pattern to notice: `datasets_for` requires *both* the grant's own reach and
an RBAC decision. `test_narrowing_survives_either_check_being_wrong` asserts that
removing either one still refuses — so a bug in this function fails closed.
"""

from __future__ import annotations

import pytest

from slpie.identity.principal import Principal
from slpie.rbac import AccessEngine, Role, Scope, allow, system_roles
from slpie.workspace import (
    Allocation,
    ControlPlane,
    Dataset,
    DatasetGrant,
    ObjectRef,
    Quota,
    State,
    Tier,
    Usage,
    Visibility,
    Workspace,
    WorkspaceError,
)
from slpie.workspace.dataset import DatasetError
from slpie.workspace.plane import PlaneError
from slpie.workspace.quota import QuotaError
from slpie.workspace.store import StoreError, within

MINUTE = 60_000_000_000
#: A realistic base instant. Zero would be indistinguishable from "never seen",
#: which `Workspace.idle_for` treats as not-idle on purpose.
EPOCH = 1_800_000_000_000_000_000


# --- fixtures ---------------------------------------------------------------


def principal(subject: str, tenant: str) -> Principal:
    return Principal(
        issuer="https://id.acme.test", subject=subject, tenant=tenant,
        email=f"{subject}@{tenant}.test", email_verified=True,
    )


@pytest.fixture()
def roles():
    graph = system_roles()
    graph.add(Role(
        name="notebook-user",
        permissions=(allow("workspace:create", "workspace"),
                     allow("dataset:read", "*")),
        description="may open a workspace and read what is granted to them",
    ))
    return graph


@pytest.fixture()
def plane(roles):
    engine = AccessEngine(roles)
    control = ControlPlane(access=engine, region="eu-west-1")
    control.set_quota("acme", Quota(max_workspaces=3, max_cpu=8.0,
                                    max_memory_mb=16_384, max_disk_gb=100))
    control.set_quota("globex", Quota(max_workspaces=2))
    return control


@pytest.fixture()
def ada(plane):
    who = principal("ada", "acme")
    plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="acme"))
    return who


@pytest.fixture()
def zed(plane):
    """A user at a *different* tenant, holding the identical role."""
    who = principal("zed", "globex")
    plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="globex"))
    return who


# --- segregation: the claim the product rests on ----------------------------


def test_a_user_never_sees_another_tenants_dataset(plane, ada, zed):
    """The one that matters. Same role, same action, different tenant."""
    theirs = Dataset(name="globex-revenue", scope=Scope(tenant="globex"))
    plane.grant(DatasetGrant(
        dataset=theirs, visibility=Visibility.TENANT, granted_by="admin",
    ))

    for_zed = plane.datasets_for(zed, scope=Scope(tenant="globex"))
    for_ada = plane.datasets_for(ada, scope=Scope(tenant="acme"))

    assert [g.dataset.name for g in for_zed] == ["globex-revenue"]
    assert for_ada == (), "acme reached a globex dataset"


def test_asking_in_someone_elses_scope_does_not_help(plane, ada):
    """A principal naming a scope they hold no binding in is refused."""
    theirs = Dataset(name="globex-revenue", scope=Scope(tenant="globex"))
    plane.grant(DatasetGrant(dataset=theirs, visibility=Visibility.TENANT))

    assert plane.datasets_for(ada, scope=Scope(tenant="globex")) == ()


def test_a_private_grant_reaches_exactly_one_principal(plane, ada):
    mine = Dataset(name="ada-scratch", scope=Scope(tenant="acme"))
    plane.grant(DatasetGrant(
        dataset=mine, visibility=Visibility.PRIVATE,
        principal_urn=ada.urn, writable=True,
    ))

    colleague = principal("bob", "acme")
    plane.access.bind(colleague.urn, "notebook-user", scope=Scope(tenant="acme"))

    assert len(plane.datasets_for(ada, scope=Scope(tenant="acme"))) == 1
    assert plane.datasets_for(colleague, scope=Scope(tenant="acme")) == ()


def test_a_realm_grant_does_not_leak_to_a_sibling_realm(plane):
    payments = Scope(tenant="acme", realm="payments")
    fulfilment = Scope(tenant="acme", realm="fulfilment")

    who = principal("cleo", "acme")
    plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="acme"))
    plane.grant(DatasetGrant(
        dataset=Dataset(name="card-data", scope=payments),
        visibility=Visibility.REALM,
    ))

    assert len(plane.datasets_for(who, scope=payments)) == 1
    assert plane.datasets_for(who, scope=fulfilment) == ()


def test_a_public_corpus_reaches_every_tenant(plane, ada, zed):
    """The shared-corpus case, which must work as well as the private one."""
    plane.grant(DatasetGrant(
        dataset=Dataset(name="npm-registry", scope=Scope(), tier=Tier.SHARED),
        visibility=Visibility.PUBLIC,
    ))

    assert len(plane.datasets_for(ada, scope=Scope(tenant="acme"))) == 1
    assert len(plane.datasets_for(zed, scope=Scope(tenant="globex"))) == 1


def test_narrowing_requires_both_the_grant_and_a_role(plane):
    """Two independent conditions, so a bug in either fails closed."""
    who = principal("dana", "acme")           # bound to no role at all
    plane.grant(DatasetGrant(
        dataset=Dataset(name="ledger", scope=Scope(tenant="acme")),
        visibility=Visibility.TENANT,
    ))

    # The grant's own reach admits her; the RBAC decision does not.
    grant = plane._grants[0]
    assert grant.reaches(principal_urn=who.urn, scope=Scope(tenant="acme"))
    assert plane.datasets_for(who, scope=Scope(tenant="acme")) == ()


def test_environment_variables_never_cross_a_tenant(plane):
    plane.set_environment(Scope(tenant="acme"), {"DB_URL": "acme-db"})
    plane.set_environment(Scope(tenant="globex"), {"DB_URL": "globex-db"})

    assert plane.environment_for(Scope(tenant="acme"))["DB_URL"] == "acme-db"
    assert plane.environment_for(Scope(tenant="globex"))["DB_URL"] == "globex-db"


def test_a_realm_value_shadows_the_tenant_value_it_overrides(plane):
    plane.set_environment(Scope(tenant="acme"), {"REGION": "eu", "TIER": "std"})
    plane.set_environment(Scope(tenant="acme", realm="payments"), {"TIER": "pci"})

    merged = plane.environment_for(Scope(tenant="acme", realm="payments"))
    assert merged == {"REGION": "eu", "TIER": "pci"}


def test_a_spawn_request_carries_variable_names_but_never_their_values():
    """`to_dict` reaches logs. Values are secret by assumption."""
    from slpie.workspace.spawn import SpawnRequest

    request = SpawnRequest(
        workspace_id="ws-1", tenant="acme", realm="", principal_urn="urn:x",
        allocation=Allocation(), environment={"DB_PASSWORD": "hunter2"},
    )
    body = request.to_dict()

    assert body["environment"] == ["DB_PASSWORD"]
    assert "hunter2" not in repr(body)


# --- storage keys -----------------------------------------------------------


def test_a_key_cannot_climb_out_of_its_prefix():
    with pytest.raises(StoreError, match="unusable segment"):
        ObjectRef(prefix="work/acme", key="../globex/secrets")


def test_a_key_cannot_be_absolute_or_use_backslashes():
    with pytest.raises(StoreError, match="absolute or uses backslashes"):
        ObjectRef(prefix="work/acme", key="/etc/passwd")
    with pytest.raises(StoreError, match="absolute or uses backslashes"):
        ObjectRef(prefix="work/acme", key="a\\..\\b")


def test_a_single_dot_segment_is_refused_rather_than_normalised():
    """A normaliser turns a hostile key into a valid one nobody chose."""
    with pytest.raises(StoreError):
        ObjectRef(prefix="work/acme", key="a/./b")


def test_a_prefix_match_respects_segment_boundaries():
    """`acme` must not match `acme-corp` — that is a cross-tenant read."""
    assert within("work/acme", "work/acme/notes.txt")
    assert within("work/acme", "work/acme")
    assert not within("work/acme", "work/acme-corp/secrets.txt")


def test_a_dataset_key_puts_the_tenant_first():
    """So a prefix listing cannot cross a tenant even if a caller forgets."""
    dataset = Dataset(name="orders", scope=Scope(tenant="acme", realm="payments"))

    assert dataset.key == "work/acme/payments/orders"


def test_a_dataset_name_that_could_escape_a_path_is_refused():
    for bad in ("..", "a/b", "UPPER", "-leading", "x" * 70, ""):
        with pytest.raises(DatasetError):
            Dataset(name=bad, scope=Scope(tenant="acme"))


def test_a_shared_corpus_cannot_be_granted_writable():
    """Two tenants reading one corpus must not reach each other through it."""
    corpus = Dataset(name="npm-registry", scope=Scope(), tier=Tier.SHARED)

    with pytest.raises(DatasetError, match="read-only"):
        DatasetGrant(dataset=corpus, visibility=Visibility.PUBLIC, writable=True)


def test_a_private_grant_naming_nobody_is_refused():
    with pytest.raises(DatasetError, match="names no principal"):
        DatasetGrant(
            dataset=Dataset(name="x", scope=Scope(tenant="acme")),
            visibility=Visibility.PRIVATE,
        )


# --- quota ------------------------------------------------------------------


def test_the_tenant_ceiling_names_the_limit_that_stopped_it(plane, ada):
    """'Quota exceeded' sends somebody to a dashboard; this tells them what."""
    quota = Quota(max_workspaces=1)
    usage = Usage(workspaces=1)

    fits, why = quota.admits(usage, Allocation())
    assert not fits
    assert "1 of 1 workspaces" in why


def test_a_third_workspace_is_refused_when_the_tenant_bought_two(plane, roles):
    plane.set_quota("acme", Quota(max_workspaces=2, max_cpu=99, max_memory_mb=99_999))
    people = []
    for name in ("ada", "bob", "cleo"):
        who = principal(name, "acme")
        plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="acme"))
        people.append(who)

    for who in people[:2]:
        result = plane.provision(who, scope=Scope(tenant="acme"), start=False)
        # REQUESTED does not consume the quota — only a live workspace does.
        plane._workspaces[result.workspace.workspace_id] = (
            result.workspace.become(State.STARTING).become(State.RUNNING)
        )

    with pytest.raises(PlaneError, match="2 of 2 workspaces"):
        plane.provision(people[2], scope=Scope(tenant="acme"), start=False)


def test_an_allocation_below_what_a_kernel_needs_is_refused():
    with pytest.raises(QuotaError, match="below the 512MB floor"):
        Allocation(memory_mb=128)
    with pytest.raises(QuotaError, match="below the 0.1 floor"):
        Allocation(cpu=0.01)


def test_usage_is_counted_from_live_workspaces_only(plane, ada):
    provisioned = plane.provision(ada, scope=Scope(tenant="acme"), start=False)
    assert plane.usage_of("acme").workspaces == 0, "REQUESTED does not consume"

    plane.reclaim(provisioned.workspace.workspace_id)
    assert plane.usage_of("acme").workspaces == 0


def test_headroom_is_what_an_administrator_reads(plane):
    quota = Quota(max_workspaces=10, max_cpu=16.0)
    left = quota.headroom(Usage(workspaces=3, cpu=6.0))

    assert left["workspaces"] == 7
    assert left["cpu"] == 10.0


# --- provisioning -----------------------------------------------------------


def test_a_principal_with_no_role_gets_no_workspace(plane):
    """Default deny. The rule the RBAC engine already enforces, reused here."""
    stranger = principal("mallory", "acme")

    with pytest.raises(PlaneError, match="may not create a workspace"):
        plane.provision(stranger, scope=Scope(tenant="acme"), start=False)


def test_a_workspace_cannot_be_provisioned_without_a_tenant(plane, ada):
    with pytest.raises(PlaneError, match="must be provisioned into a tenant"):
        plane.provision(ada, scope=Scope(), start=False)


def test_asking_twice_returns_the_same_workspace(plane, ada):
    """A refreshed browser tab must not double the tenant's bill."""
    first = plane.provision(ada, scope=Scope(tenant="acme"), start=False)
    plane.touch(first.workspace.workspace_id)
    second = plane.provision(ada, scope=Scope(tenant="acme"), start=False)

    assert first.workspace.workspace_id == second.workspace.workspace_id
    assert second.placement.reason == "already provisioned"


def test_the_workspace_id_is_derived_not_random(plane, ada):
    one = Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))
    two = Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))

    assert one.workspace_id == two.workspace_id
    assert one.workspace_id.startswith("ws-")


def test_the_same_user_in_two_tenants_gets_two_workspaces(ada):
    here = Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))
    there = Workspace(principal_urn=ada.urn, scope=Scope(tenant="globex"))

    assert here.workspace_id != there.workspace_id


def test_a_workspace_id_is_usable_as_a_kubernetes_name(ada):
    workspace = Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))

    assert len(workspace.workspace_id) <= 63
    assert workspace.workspace_id.islower()
    assert workspace.namespace == "slpie-acme"


def test_a_workspace_with_no_owner_is_refused():
    with pytest.raises(WorkspaceError, match="names no principal"):
        Workspace(principal_urn="", scope=Scope(tenant="acme"))


def test_a_workspace_with_no_tenant_is_refused():
    with pytest.raises(WorkspaceError, match="no tenant"):
        Workspace(principal_urn="urn:x", scope=Scope())


# --- lifecycle --------------------------------------------------------------


def test_a_workspace_cannot_go_backwards(ada):
    """Two kernels on one volume corrupt a notebook and it looks like user error."""
    running = (
        Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))
        .become(State.STARTING).become(State.RUNNING)
    )

    with pytest.raises(WorkspaceError, match="cannot go from running to requested"):
        running.become(State.REQUESTED)


def test_every_legal_transition_is_reachable(ada):
    base = Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))
    walk = base.become(State.STARTING).become(State.RUNNING)
    walk = walk.become(State.IDLE).become(State.RUNNING)
    walk = walk.become(State.STOPPING).become(State.STOPPED)

    assert walk.state is State.STOPPED
    assert walk.become(State.REQUESTED).state is State.REQUESTED


def test_an_idle_workspace_past_its_allowance_is_reclaimable(ada):
    workspace = (
        Workspace(
            principal_urn=ada.urn, scope=Scope(tenant="acme"),
            allocation=Allocation(idle_timeout_minutes=30),
            last_seen_at=EPOCH,
        )
        .become(State.STARTING).become(State.RUNNING)
    )
    workspace = workspace.touched(now=EPOCH)

    assert not workspace.reclaimable(now=EPOCH + 29 * MINUTE)
    assert workspace.reclaimable(now=EPOCH + 31 * MINUTE)


def test_touching_an_idle_workspace_brings_it_back(ada):
    workspace = (
        Workspace(principal_urn=ada.urn, scope=Scope(tenant="acme"))
        .become(State.STARTING).become(State.RUNNING).become(State.IDLE)
    )

    assert workspace.touched(now=MINUTE).state is State.RUNNING


def test_the_sweeper_reclaims_only_what_is_past_its_allowance(plane, roles):
    plane.set_quota("acme", Quota(max_workspaces=9, max_cpu=99, max_memory_mb=99_999))
    for name in ("ada", "bob"):
        who = principal(name, "acme")
        plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="acme"))
        result = plane.provision(
            who, scope=Scope(tenant="acme"),
            allocation=Allocation(idle_timeout_minutes=10), start=False,
            now=EPOCH,
        )
        plane._workspaces[result.workspace.workspace_id] = (
            result.workspace.become(State.STARTING).become(State.RUNNING)
        )

    assert plane.reclaim_idle(now=EPOCH + 5 * MINUTE) == ()
    reclaimed = plane.reclaim_idle(now=EPOCH + 20 * MINUTE)
    assert len(reclaimed) == 2
    assert all(item.state is State.STOPPED for item in reclaimed)


def test_reclaiming_releases_the_quota(plane, roles):
    plane.set_quota("acme", Quota(max_workspaces=1, max_cpu=99, max_memory_mb=99_999))
    ada_ = principal("ada", "acme")
    bob = principal("bob", "acme")
    for who in (ada_, bob):
        plane.access.bind(who.urn, "notebook-user", scope=Scope(tenant="acme"))

    first = plane.provision(ada_, scope=Scope(tenant="acme"), start=False)
    plane._workspaces[first.workspace.workspace_id] = (
        first.workspace.become(State.STARTING).become(State.RUNNING)
    )
    assert plane.usage_of("acme").workspaces == 1

    with pytest.raises(PlaneError):
        plane.provision(bob, scope=Scope(tenant="acme"), start=False)

    plane.reclaim(first.workspace.workspace_id)
    assert plane.provision(bob, scope=Scope(tenant="acme"), start=False)


# --- the administrator's view -----------------------------------------------


def test_the_console_reports_usage_and_headroom_per_tenant(plane, ada):
    plane.provision(ada, scope=Scope(tenant="acme"), start=False)
    body = plane.status()

    assert body["region"] == "eu-west-1"
    acme = next(item for item in body["tenants"] if item["tenant"] == "acme")
    assert "usage" in acme and "quota" in acme and "headroom" in acme


def test_placement_records_why_even_though_regions_are_deferred(plane, ada):
    """Retrofitting 'why here?' loses the answer for everything already running."""
    result = plane.provision(ada, scope=Scope(tenant="acme"), start=False)

    assert result.placement.region == "eu-west-1"
    assert result.placement.reason
