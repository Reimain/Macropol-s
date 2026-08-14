"""One namespace per tenant, one JupyterLab per user, and nothing shared.

Implements `slpie.workspace.Spawner`. What it creates, per workspace:

=======================  =====================================================
`Namespace`              one per **tenant**, not per user — so a NetworkPolicy
                         and a ResourceQuota can be written once and cover
                         everybody
`ResourceQuota`          the tenant's ceiling, enforced by the cluster as well
                         as by the control plane. Two independent limits, so a
                         bug in ours does not become an unbounded bill
`LimitRange`             a default and a maximum per pod, so a workspace
                         created by some other path still cannot ask for the
                         whole node
`ServiceAccount`         per workspace, with **no** API access — a notebook
                         that could call the Kubernetes API could list its
                         neighbours
`Secret`                 the scoped environment variables, mounted by reference
                         so `kubectl describe pod` does not print them
`PersistentVolumeClaim`  the working tier, one per workspace
`Pod`                    JupyterLab, with limits, a read-only root filesystem
                         and no privilege escalation
`Service`                in-cluster address
`NetworkPolicy`          **default deny**, then the two flows a notebook needs
`Ingress`                the user's URL
=======================  =====================================================

**`plan()` renders every one of those without a cluster.** That is not a
convenience — it is how this is testable at all, and it is the same plan/apply
split `slpie/binding/guard.py` applies to a live target: the decision is
reviewable while it is still free. `start()` is the only method that needs a
cluster, and it is a thin loop over the same objects `plan()` returned.

The NetworkPolicy is the part worth reading twice. Default deny both ways, then
egress to DNS and to the object store, and ingress only from the ingress
controller. Without it, two workspaces in one namespace can reach each other's
pods directly by IP — which is a tenant boundary that exists in the control
plane and not in the cluster.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from slpie.workspace import Runtime, SpawnRequest, Started
from slpie.workspace.spawn import SpawnError

#: The image a workspace runs. Pinned by digest in production; the tag here is
#: the readable default, and `SpawnRequest.image` overrides it.
DEFAULT_IMAGE = "quay.io/jupyter/scipy-notebook:python-3.11"

#: Where the working tier is mounted inside the notebook.
WORK_MOUNT = "/home/jovyan/work"
SHARED_MOUNT = "/home/jovyan/shared"

#: JupyterLab's port inside the pod.
PORT = 8888

#: Applied to everything, so `kubectl delete -l` reclaims a workspace whole and
#: a stray object left behind by a failed apply is findable.
def labels_for(request: SpawnRequest) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "slpie-workspace",
        "app.kubernetes.io/managed-by": "slpie",
        "slpie.dev/workspace": request.workspace_id,
        "slpie.dev/tenant": request.tenant,
        # Realm may be empty; a label value must not be, so it is omitted rather
        # than set to "" — an empty label value is a selector that matches
        # everything, which is the opposite of what a realm label is for.
        **({"slpie.dev/realm": request.realm} if request.realm else {}),
    }


def namespace_of(tenant: str) -> str:
    return f"slpie-{tenant}"


def _quantity_cpu(cpu: float) -> str:
    """Kubernetes millicores. `0.5` is `500m`."""
    return f"{int(cpu * 1000)}m"


class KubernetesSpawner:
    """Provisions a workspace as a set of Kubernetes objects.

    The client is injected rather than constructed, so `plan()` works with no
    cluster and the tests below run everywhere. `start()` raises a clear error
    if it is asked to act without one, rather than failing somewhere inside a
    library with a connection refused.
    """

    runtime = Runtime.KUBERNETES

    def __init__(
        self,
        *,
        client: Any = None,
        image: str = DEFAULT_IMAGE,
        storage_class: str = "",
        ingress_host: str = "notebooks.example.com",
        ingress_class: str = "nginx",
        service_account_token: bool = False,
    ) -> None:
        self.client = client
        self.image = image
        self.storage_class = storage_class
        self.ingress_host = ingress_host
        self.ingress_class = ingress_class
        #: False by default. A notebook that can call the Kubernetes API can
        #: list the pods beside it, which is a tenant boundary the control plane
        #: thinks it is enforcing.
        self.service_account_token = service_account_token

    # -- planning --------------------------------------------------------

    def plan(self, request: SpawnRequest) -> Sequence[Mapping[str, Any]]:
        """Every object `start` would create. Touches nothing."""
        return [
            self._namespace(request),
            self._resource_quota(request),
            self._limit_range(request),
            self._service_account(request),
            self._secret(request),
            self._claim(request),
            self._network_policy(request),
            self._pod(request),
            self._service(request),
            self._ingress(request),
        ]

    def _namespace(self, request: SpawnRequest) -> dict[str, Any]:
        return {
            "apiVersion": "v1", "kind": "Namespace",
            "metadata": {
                "name": namespace_of(request.tenant),
                "labels": {
                    "app.kubernetes.io/managed-by": "slpie",
                    "slpie.dev/tenant": request.tenant,
                    # Lets a cluster-wide policy target every tenant namespace
                    # without enumerating them.
                    "pod-security.kubernetes.io/enforce": "restricted",
                },
            },
        }

    def _resource_quota(self, request: SpawnRequest) -> dict[str, Any]:
        """The tenant's ceiling, enforced by the cluster as well as by us.

        Deliberately redundant with `slpie.workspace.Quota`. Two independent
        limits means a bug in the control plane produces a refused pod rather
        than an unbounded bill, and the cluster is the one an attacker cannot
        reach by calling our API differently.
        """
        allocation = request.allocation
        return {
            "apiVersion": "v1", "kind": "ResourceQuota",
            "metadata": {
                "name": f"{request.tenant}-quota",
                "namespace": namespace_of(request.tenant),
                "labels": {"app.kubernetes.io/managed-by": "slpie"},
            },
            "spec": {"hard": {
                "requests.cpu": _quantity_cpu(allocation.cpu * 20),
                "requests.memory": f"{allocation.memory_mb * 20}Mi",
                "persistentvolumeclaims": "40",
                "count/pods": "40",
            }},
        }

    def _limit_range(self, request: SpawnRequest) -> dict[str, Any]:
        allocation = request.allocation
        return {
            "apiVersion": "v1", "kind": "LimitRange",
            "metadata": {
                "name": f"{request.tenant}-limits",
                "namespace": namespace_of(request.tenant),
            },
            "spec": {"limits": [{
                "type": "Container",
                "default": {
                    "cpu": _quantity_cpu(allocation.cpu),
                    "memory": f"{allocation.memory_mb}Mi",
                },
                "defaultRequest": {
                    "cpu": _quantity_cpu(allocation.cpu / 4),
                    "memory": f"{allocation.memory_mb // 2}Mi",
                },
                "max": {
                    "cpu": _quantity_cpu(allocation.cpu * 2),
                    "memory": f"{allocation.memory_mb * 2}Mi",
                },
            }]},
        }

    def _service_account(self, request: SpawnRequest) -> dict[str, Any]:
        return {
            "apiVersion": "v1", "kind": "ServiceAccount",
            "metadata": {
                "name": request.workspace_id,
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
            },
            # The important line in this file. A notebook that can reach the
            # Kubernetes API can list every pod in its namespace, which includes
            # every other user of the same tenant.
            "automountServiceAccountToken": self.service_account_token,
        }

    def _secret(self, request: SpawnRequest) -> dict[str, Any]:
        return {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {
                "name": f"{request.workspace_id}-env",
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
            },
            "type": "Opaque",
            "stringData": dict(request.environment),
        }

    def _claim(self, request: SpawnRequest) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {
                "requests": {"storage": f"{request.allocation.disk_gb}Gi"},
            },
        }
        if self.storage_class:
            spec["storageClassName"] = self.storage_class
        return {
            "apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": f"{request.workspace_id}-work",
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
            },
            "spec": spec,
        }

    def _network_policy(self, request: SpawnRequest) -> dict[str, Any]:
        """Default deny, then only the flows a notebook actually needs.

        Without this, two workspaces in one namespace reach each other's pods
        directly by IP. The control plane believes it is enforcing a boundary
        that the cluster is not.
        """
        return {
            "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{request.workspace_id}-isolation",
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
            },
            "spec": {
                "podSelector": {"matchLabels": {
                    "slpie.dev/workspace": request.workspace_id,
                }},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{
                    # Only the ingress controller. Not the pod next door.
                    "from": [{"namespaceSelector": {"matchLabels": {
                        "kubernetes.io/metadata.name": "ingress-nginx",
                    }}}],
                    "ports": [{"protocol": "TCP", "port": PORT}],
                }],
                "egress": [
                    {   # DNS, or nothing resolves.
                        "to": [{"namespaceSelector": {"matchLabels": {
                            "kubernetes.io/metadata.name": "kube-system",
                        }}}],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    {   # The object store, and whatever else is deliberately
                        # reachable. Not the cluster's own network: 169.254/16
                        # is the cloud metadata service, and a notebook that can
                        # reach it can assume the node's IAM role.
                        "to": [{"ipBlock": {
                            "cidr": "0.0.0.0/0",
                            "except": [
                                "169.254.169.254/32",   # cloud metadata
                                "10.0.0.0/8",
                                "172.16.0.0/12",
                                "192.168.0.0/16",
                            ],
                        }}],
                        "ports": [{"protocol": "TCP", "port": 443}],
                    },
                ],
            },
        }

    def _pod(self, request: SpawnRequest) -> dict[str, Any]:
        allocation = request.allocation
        mounts = [
            {"name": "work", "mountPath": WORK_MOUNT},
            # JupyterLab writes runtime state; the root filesystem is read-only,
            # so it needs somewhere that is not.
            {"name": "runtime", "mountPath": "/home/jovyan/.local"},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
        volumes: list[dict[str, Any]] = [
            {"name": "work", "persistentVolumeClaim": {
                "claimName": f"{request.workspace_id}-work",
            }},
            {"name": "runtime", "emptyDir": {}},
            {"name": "tmp", "emptyDir": {}},
        ]

        resources: dict[str, Any] = {
            "requests": {
                "cpu": _quantity_cpu(allocation.cpu / 2),
                "memory": f"{allocation.memory_mb // 2}Mi",
            },
            "limits": {
                "cpu": _quantity_cpu(allocation.cpu),
                "memory": f"{allocation.memory_mb}Mi",
            },
        }
        if allocation.gpu:
            resources["limits"]["nvidia.com/gpu"] = str(allocation.gpu)

        return {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {
                "name": request.workspace_id,
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
                "annotations": {
                    "slpie.dev/principal": request.principal_urn,
                    "slpie.dev/datasets": ",".join(
                        grant.dataset.name for grant in request.grants
                    ),
                },
            },
            "spec": {
                "serviceAccountName": request.workspace_id,
                "automountServiceAccountToken": self.service_account_token,
                "restartPolicy": "OnFailure",
                "securityContext": {
                    "runAsNonRoot": True, "runAsUser": 1000, "fsGroup": 100,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [{
                    "name": "notebook",
                    "image": request.image or self.image,
                    "ports": [{"containerPort": PORT, "name": "http"}],
                    "resources": resources,
                    "envFrom": [{"secretRef": {
                        "name": f"{request.workspace_id}-env",
                    }}],
                    "env": [
                        {"name": "SLPIE_WORKSPACE", "value": request.workspace_id},
                        {"name": "SLPIE_TENANT", "value": request.tenant},
                        {"name": "SLPIE_WORK_DIR", "value": WORK_MOUNT},
                        {"name": "SLPIE_SHARED_DIR", "value": SHARED_MOUNT},
                    ],
                    "volumeMounts": mounts,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "readinessProbe": {
                        "httpGet": {"path": "/api", "port": PORT},
                        "initialDelaySeconds": 10, "periodSeconds": 5,
                    },
                    "livenessProbe": {
                        "httpGet": {"path": "/api", "port": PORT},
                        "initialDelaySeconds": 60, "periodSeconds": 30,
                    },
                }],
                "volumes": volumes,
            },
        }

    def _service(self, request: SpawnRequest) -> dict[str, Any]:
        return {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {
                "name": request.workspace_id,
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
            },
            "spec": {
                "selector": {"slpie.dev/workspace": request.workspace_id},
                "ports": [{"port": 80, "targetPort": PORT, "name": "http"}],
            },
        }

    def _ingress(self, request: SpawnRequest) -> dict[str, Any]:
        path = f"/user/{request.tenant}/{request.workspace_id}"
        return {
            "apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
            "metadata": {
                "name": request.workspace_id,
                "namespace": namespace_of(request.tenant),
                "labels": labels_for(request),
                "annotations": {
                    "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
                    "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
                },
            },
            "spec": {
                "ingressClassName": self.ingress_class,
                "rules": [{
                    "host": self.ingress_host,
                    "http": {"paths": [{
                        "path": path, "pathType": "Prefix",
                        "backend": {"service": {
                            "name": request.workspace_id,
                            "port": {"number": 80},
                        }},
                    }]},
                }],
            },
        }

    def url_for(self, request: SpawnRequest) -> str:
        return (
            f"https://{self.ingress_host}"
            f"/user/{request.tenant}/{request.workspace_id}"
        )

    # -- applying --------------------------------------------------------

    def start(self, request: SpawnRequest) -> Started:
        """Apply the plan. The only method that needs a cluster."""
        if self.client is None:
            raise SpawnError(
                "no Kubernetes client was configured, so this spawner can plan "
                "but not apply. `plan()` renders every object it would create; "
                "pass a client to act on them"
            )
        applied = self.client.apply(self.plan(request))
        return Started(
            workspace_id=request.workspace_id,
            runtime=self.runtime,
            url=self.url_for(request),
            node=str(applied.get("node", "")) if isinstance(applied, dict) else "",
            detail=f"applied {len(self.plan(request))} object(s)",
        )

    def stop(self, workspace_id: str) -> bool:
        if self.client is None:
            raise SpawnError("no Kubernetes client was configured")
        return bool(self.client.delete(
            label_selector=f"slpie.dev/workspace={workspace_id}",
        ))

    def status(self, workspace_id: str) -> Mapping[str, Any]:
        if self.client is None:
            return {"workspace_id": workspace_id, "runtime": self.runtime.value,
                    "known": False, "detail": "no client configured"}
        return self.client.status(workspace_id)
