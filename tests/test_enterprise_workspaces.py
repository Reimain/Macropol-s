"""Kubernetes provisioning and tiered storage, tested without a cluster.

That is the whole point of the plan/apply split. `plan()` renders every object
`start()` would create, so the manifests can be validated against the real
Kubernetes API models — the same code a cluster's clients use — in CI, on a
laptop, and in a notebook.

The tests that earn their place are the security ones. A manifest can be
perfectly valid and still mount a service-account token, allow egress to the
cloud metadata endpoint, or leave a workspace reachable from the pod next door.
Each of those is a tenant boundary that the control plane believes it is
enforcing and the cluster is not.
"""

from __future__ import annotations

import pytest

from slpie.workspace import Allocation, ObjectRef, SpawnRequest, Tier
from slpie.workspace.store import StoreError
from slpie_enterprise.spawn import DEFAULT_IMAGE, KubernetesSpawner, namespace_of
from slpie_enterprise.spawn.validate import validate
from slpie_enterprise.storage import FilesystemStore, S3Store, TieredStore


@pytest.fixture()
def request_for():
    def build(**overrides):
        settings = {
            "workspace_id": "ws-abc123", "tenant": "acme", "realm": "payments",
            "principal_urn": "urn:slpie:user:ada",
            "allocation": Allocation(cpu=2, memory_mb=4096, disk_gb=20),
            "environment": {"DB_URL": "postgres://x", "API_KEY": "secret"},
        }
        settings.update(overrides)
        return SpawnRequest(**settings)
    return build


@pytest.fixture()
def spawner():
    return KubernetesSpawner(ingress_host="nb.acme.test")


def planned(spawner, request, kind):
    return next(o for o in spawner.plan(request) if o["kind"] == kind)


# --- the plan is real, and needs no cluster ---------------------------------


def test_a_plan_renders_every_object_without_a_cluster(spawner, request_for):
    plan = spawner.plan(request_for())
    kinds = [obj["kind"] for obj in plan]

    assert kinds == [
        "Namespace", "ResourceQuota", "LimitRange", "ServiceAccount", "Secret",
        "PersistentVolumeClaim", "NetworkPolicy", "Pod", "Service", "Ingress",
    ]


def test_every_planned_object_satisfies_the_kubernetes_api_models(
    spawner, request_for,
):
    """Deserialised through `kubernetes.client` — the code a cluster's own
    clients use. A manifest a cluster would reject fails here rather than in
    front of a customer at apply time."""
    pytest.importorskip("kubernetes")
    request = request_for()

    result = validate(
        spawner.plan(request),
        namespace=namespace_of("acme"), workspace_id="ws-abc123",
    )

    assert result.schema_checked
    assert result.ok, result.explain()


def test_the_validator_has_teeth():
    """A validator that always passes is worse than none."""
    leaky = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": "leaky", "namespace": "slpie-acme",
                     "labels": {"slpie.dev/workspace": "ws-1"}},
        "spec": {
            "automountServiceAccountToken": True,
            "containers": [{"name": "nb", "image": "x", "resources": {},
                            "securityContext": {"allowPrivilegeEscalation": True}}],
        },
    }
    result = validate([leaky], namespace="slpie-acme", workspace_id="ws-1")

    assert not result.ok
    reasons = " ".join(item.detail for item in result.problems)
    assert "service-account token" in reasons
    assert "privilege escalation" in reasons
    assert "no CPU or memory limit" in reasons


def test_starting_without_a_client_says_so_rather_than_failing_obscurely(
    spawner, request_for,
):
    from slpie.workspace.spawn import SpawnError

    with pytest.raises(SpawnError, match="can plan but not apply"):
        spawner.start(request_for())


# --- the tenant boundary ----------------------------------------------------


def test_every_object_lands_in_the_tenants_namespace(spawner, request_for):
    plan = spawner.plan(request_for())

    for obj in plan:
        if obj["kind"] == "Namespace":
            assert obj["metadata"]["name"] == "slpie-acme"
            continue
        assert obj["metadata"]["namespace"] == "slpie-acme", obj["kind"]


def test_two_tenants_never_share_a_namespace(spawner, request_for):
    here = spawner.plan(request_for(tenant="acme"))
    there = spawner.plan(request_for(tenant="globex", workspace_id="ws-def456"))

    assert here[0]["metadata"]["name"] != there[0]["metadata"]["name"]


def test_a_workspace_is_reclaimable_whole(spawner, request_for):
    """`kubectl delete -l slpie.dev/workspace=...` must not leave anything."""
    plan = spawner.plan(request_for())
    per_workspace = [
        obj for obj in plan
        if obj["kind"] not in ("Namespace", "ResourceQuota", "LimitRange")
    ]

    for obj in per_workspace:
        assert obj["metadata"]["labels"]["slpie.dev/workspace"] == "ws-abc123", (
            f"{obj['kind']} would be left behind"
        )


def test_an_empty_realm_is_omitted_rather_than_set_to_empty(spawner, request_for):
    """An empty label value is a selector that matches everything."""
    labels = planned(spawner, request_for(realm=""), "Pod")["metadata"]["labels"]

    assert "slpie.dev/realm" not in labels
    assert "" not in labels.values()


# --- the security posture ---------------------------------------------------


def test_a_notebook_cannot_reach_the_kubernetes_api(spawner, request_for):
    """It could otherwise list every pod in its namespace — every other user."""
    request = request_for()
    account = planned(spawner, request, "ServiceAccount")
    pod = planned(spawner, request, "Pod")

    assert account["automountServiceAccountToken"] is False
    assert pod["spec"]["automountServiceAccountToken"] is False


def test_a_notebook_cannot_reach_the_cloud_metadata_service(spawner, request_for):
    """169.254.169.254 hands out the node's IAM role."""
    policy = planned(spawner, request_for(), "NetworkPolicy")

    blocks = [
        target["ipBlock"]
        for rule in policy["spec"]["egress"]
        for target in rule.get("to", [])
        if "ipBlock" in target
    ]
    assert blocks
    for block in blocks:
        if block["cidr"] == "0.0.0.0/0":
            assert "169.254.169.254/32" in block["except"]


def test_a_workspace_is_not_reachable_from_the_pod_beside_it(spawner, request_for):
    """Without this the tenant boundary exists only in the control plane."""
    policy = planned(spawner, request_for(), "NetworkPolicy")

    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    sources = policy["spec"]["ingress"][0]["from"]
    assert sources == [{"namespaceSelector": {"matchLabels": {
        "kubernetes.io/metadata.name": "ingress-nginx",
    }}}]


def test_the_container_runs_unprivileged_with_a_read_only_root(
    spawner, request_for,
):
    pod = planned(spawner, request_for(), "Pod")
    container = pod["spec"]["containers"][0]

    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_a_read_only_root_still_leaves_jupyter_somewhere_to_write(
    spawner, request_for,
):
    """Otherwise the pod comes up and the first cell fails."""
    pod = planned(spawner, request_for(), "Pod")
    mounts = {m["mountPath"] for m in pod["spec"]["containers"][0]["volumeMounts"]}

    assert "/tmp" in mounts
    assert "/home/jovyan/.local" in mounts


def test_environment_values_are_mounted_by_reference_not_inlined(
    spawner, request_for,
):
    """`kubectl describe pod` prints env; it does not print a secret's contents."""
    request = request_for()
    pod = planned(spawner, request, "Pod")
    container = pod["spec"]["containers"][0]

    assert container["envFrom"] == [{"secretRef": {"name": "ws-abc123-env"}}]
    inline = {item["name"]: item["value"] for item in container["env"]}
    assert "API_KEY" not in inline
    assert "secret" not in str(container["env"])


# --- resources --------------------------------------------------------------


def test_the_allocation_becomes_a_real_cpu_and_memory_limit(spawner, request_for):
    pod = planned(spawner, request_for(), "Pod")
    limits = pod["spec"]["containers"][0]["resources"]["limits"]

    assert limits["cpu"] == "2000m"
    assert limits["memory"] == "4096Mi"


def test_a_gpu_allocation_reaches_the_container(spawner, request_for):
    request = request_for(allocation=Allocation(cpu=4, memory_mb=16_384, gpu=1))
    limits = planned(spawner, request, "Pod")["spec"]["containers"][0][
        "resources"]["limits"]

    assert limits["nvidia.com/gpu"] == "1"


def test_the_cluster_enforces_a_ceiling_of_its_own(spawner, request_for):
    """Redundant with the control plane on purpose.

    Two independent limits means a bug in ours produces a refused pod rather
    than an unbounded bill — and the cluster is the one an attacker cannot reach
    by calling our API differently.
    """
    quota = planned(spawner, request_for(), "ResourceQuota")

    assert quota["spec"]["hard"]["count/pods"]
    assert quota["spec"]["hard"]["requests.memory"]


def test_the_disk_allocation_becomes_the_claim(spawner, request_for):
    claim = planned(spawner, request_for(), "PersistentVolumeClaim")

    assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi"


def test_the_default_image_is_a_real_jupyter_image(spawner, request_for):
    pod = planned(spawner, request_for(), "Pod")

    assert pod["spec"]["containers"][0]["image"] == DEFAULT_IMAGE
    assert "notebook" in DEFAULT_IMAGE


def test_a_caller_may_pin_a_different_image(spawner, request_for):
    pod = planned(spawner, request_for(image="acme/notebook@sha256:abc"), "Pod")

    assert pod["spec"]["containers"][0]["image"] == "acme/notebook@sha256:abc"


def test_each_user_gets_their_own_url(spawner, request_for):
    ingress = planned(spawner, request_for(), "Ingress")
    path = ingress["spec"]["rules"][0]["http"]["paths"][0]["path"]

    assert path == "/user/acme/ws-abc123"
    assert spawner.url_for(request_for()).endswith(path)


# --- storage ----------------------------------------------------------------


def test_the_working_tier_is_content_addressed(tmp_path):
    store = FilesystemStore(tmp_path)
    ref = ObjectRef(prefix="work/acme/payments", key="notes.txt")

    assert store.put(ref, b"hello") == 5
    assert store.put(ref, b"hello") == 0, "the same bytes cost one write"
    assert store.get(ref) == b"hello"


def test_a_partial_write_never_becomes_a_readable_object(tmp_path, monkeypatch):
    """A half-written checkpoint is a corrupt notebook, and it looks like the
    user's fault."""
    import os

    store = FilesystemStore(tmp_path)
    ref = ObjectRef(prefix="work/acme", key="big.bin")

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        store.put(ref, b"x" * 4096)

    assert not store.exists(ref)


def test_listing_never_crosses_a_tenant_boundary(tmp_path):
    store = FilesystemStore(tmp_path)
    store.put(ObjectRef(prefix="work/acme", key="mine.txt"), b"a")
    store.put(ObjectRef(prefix="work/acme-corp", key="theirs.txt"), b"b")

    found = list(store.list("work/acme"))

    assert found == ["work/acme/mine.txt"], "a prefix match crossed a tenant"


def test_a_key_that_escapes_the_root_is_refused_twice(tmp_path):
    """`ObjectRef` refuses it, and the store refuses it again."""
    store = FilesystemStore(tmp_path)

    with pytest.raises(StoreError):
        store.get(ObjectRef(prefix="work/acme", key="../../etc/passwd"))


def test_the_shared_tier_cannot_be_written_through_the_router(tmp_path):
    tiered = TieredStore(work=FilesystemStore(tmp_path))
    corpus = ObjectRef(prefix=f"{Tier.SHARED.value}/_global/_", key="npm.json")

    with pytest.raises(StoreError, match="read-only"):
        tiered.put(corpus, b"x")
    with pytest.raises(StoreError, match="read-only"):
        tiered.delete(corpus)


def test_the_router_sends_a_key_to_the_tier_its_prefix_names(tmp_path):
    class Recording:
        tier = "shared"

        def __init__(self):
            self.asked: list[str] = []

        def get(self, ref):
            self.asked.append(ref.path)
            return b"corpus"

        def to_dict(self):
            return {"tier": self.tier}

    shared = Recording()
    tiered = TieredStore(work=FilesystemStore(tmp_path), shared=shared)

    assert tiered.get(ObjectRef(prefix="shared/_global/_", key="npm.json")) == b"corpus"
    assert shared.asked == ["shared/_global/_/npm.json"]


def test_a_missing_shared_tier_says_what_to_do_about_it(tmp_path):
    tiered = TieredStore(work=FilesystemStore(tmp_path))

    with pytest.raises(StoreError, match="no shared tier configured"):
        tiered.get(ObjectRef(prefix="shared/_global/_", key="npm.json"))


def test_the_s3_tier_rechecks_the_prefix_on_a_segment_boundary():
    """A bucket prefix is a string match: `acme` matches `acme-corp`."""
    class Fake:
        def list_objects_v2(self, **_settings):
            return {"Contents": [
                {"Key": "shared/acme/corpus/a.json"},
                {"Key": "shared/acme-corp/secrets.json"},
            ], "IsTruncated": False}

    store = S3Store("bucket", client=Fake())

    assert list(store.list("shared/acme")) == ["shared/acme/corpus/a.json"]


def test_a_tiered_store_describes_both_halves(tmp_path):
    tiered = TieredStore(
        work=FilesystemStore(tmp_path), shared=S3Store("corpora", client=object()),
    )
    body = tiered.to_dict()

    assert body["work"]["backend"] == "filesystem"
    assert body["shared"]["bucket"] == "corpora"
