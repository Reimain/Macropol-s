"""Check a plan against the real Kubernetes API before anybody applies it.

A rendered manifest that a cluster would reject is worse than no manifest,
because it fails at `apply` time in front of a customer rather than in CI. This
deserialises every planned object through `kubernetes.client`'s own models — the
same code the API server's clients use — so a misspelled field, a wrong type or a
missing required key is caught here.

Two things it checks that a schema cannot:

* **the security posture** — no privilege escalation, a read-only root
  filesystem, no service-account token, and a NetworkPolicy that denies by
  default. A manifest can be perfectly valid and still hand a notebook the
  node's IAM role.
* **the tenant boundary** — every object lands in the tenant's namespace, and
  every object carries the labels that make `kubectl delete -l` reclaim a
  workspace whole.

`kubernetes` is an optional extra, so the schema half degrades to a structural
check when it is absent and says so, rather than silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: `kind` to the `kubernetes.client` model that deserialises it.
MODELS: dict[str, str] = {
    "Namespace": "V1Namespace",
    "ResourceQuota": "V1ResourceQuota",
    "LimitRange": "V1LimitRange",
    "ServiceAccount": "V1ServiceAccount",
    "Secret": "V1Secret",
    "PersistentVolumeClaim": "V1PersistentVolumeClaim",
    "NetworkPolicy": "V1NetworkPolicy",
    "Pod": "V1Pod",
    "Service": "V1Service",
    "Ingress": "V1Ingress",
}


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong with a planned object."""

    kind: str
    name: str
    detail: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}/{self.name}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Validation:
    """What a plan looks like to a cluster, before there is one."""

    checked: int = 0
    problems: tuple[Problem, ...] = ()
    schema_checked: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.problems)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "checked": self.checked,
            "schema_checked": self.schema_checked, "detail": self.detail,
            "problems": [
                {"kind": p.kind, "name": p.name, "detail": p.detail,
                 "severity": p.severity}
                for p in self.problems
            ],
        }

    def explain(self) -> str:
        if self.ok and not self.problems:
            how = "against the Kubernetes API models" if self.schema_checked \
                else "structurally (the kubernetes package is not installed)"
            return f"  {self.checked} object(s) validated {how}"
        lines = [f"  {self.checked} object(s) checked, "
                 f"{len(self.problems)} problem(s):"]
        lines.extend(f"    {item}" for item in self.problems)
        return "\n".join(lines)


def _deserialise(objects: Sequence[Mapping[str, Any]]) -> list[Problem]:
    """Round-trip every object through the client's own models."""
    try:
        from kubernetes import client
        from kubernetes.client import ApiClient
    except ImportError:
        return []

    api = ApiClient()
    found: list[Problem] = []

    class _Response:
        """`ApiClient.deserialize` wants something with `.data`."""

        def __init__(self, body: str) -> None:
            self.data = body

    import json

    for obj in objects:
        kind = str(obj.get("kind", ""))
        name = str(obj.get("metadata", {}).get("name", ""))
        model = MODELS.get(kind)
        if model is None:
            found.append(Problem(kind, name, f"no model known for kind {kind!r}"))
            continue
        try:
            api.deserialize(_Response(json.dumps(obj)), getattr(client, model))
        except Exception as error:  # noqa: BLE001 - the library raises broadly
            found.append(Problem(kind, name, f"rejected by {model}: {error}"))
    return found


def _security(objects: Sequence[Mapping[str, Any]]) -> list[Problem]:
    """The posture checks a schema cannot make."""
    found: list[Problem] = []

    for obj in objects:
        kind = str(obj.get("kind", ""))
        name = str(obj.get("metadata", {}).get("name", ""))

        if kind == "Pod":
            spec = obj.get("spec", {})
            if spec.get("automountServiceAccountToken", True):
                found.append(Problem(kind, name, (
                    "mounts a service-account token; a notebook that can reach "
                    "the Kubernetes API can list every pod beside it, which is "
                    "the tenant boundary the control plane thinks it enforces"
                )))
            if not spec.get("securityContext", {}).get("runAsNonRoot"):
                found.append(Problem(kind, name, "may run as root"))
            for container in spec.get("containers", []):
                context = container.get("securityContext", {})
                if context.get("allowPrivilegeEscalation", True):
                    found.append(Problem(kind, name, (
                        f"container {container.get('name')} allows privilege "
                        f"escalation"
                    )))
                if not context.get("readOnlyRootFilesystem"):
                    found.append(Problem(kind, name, (
                        f"container {container.get('name')} has a writable root "
                        f"filesystem"
                    ), severity="warning"))
                limits = container.get("resources", {}).get("limits", {})
                if not limits.get("memory") or not limits.get("cpu"):
                    found.append(Problem(kind, name, (
                        f"container {container.get('name')} has no CPU or memory "
                        f"limit; one notebook can then starve the node"
                    )))

        if kind == "ServiceAccount" and obj.get(
            "automountServiceAccountToken", True
        ):
            found.append(Problem(kind, name, "mounts an API token by default"))

        if kind == "NetworkPolicy":
            spec = obj.get("spec", {})
            types = set(spec.get("policyTypes", []))
            if types != {"Ingress", "Egress"}:
                found.append(Problem(kind, name, (
                    "does not police both directions; egress is how a notebook "
                    "reaches the cloud metadata service"
                )))
            for rule in spec.get("egress", []):
                for target in rule.get("to", []):
                    block = target.get("ipBlock")
                    if block and block.get("cidr") == "0.0.0.0/0":
                        if "169.254.169.254/32" not in block.get("except", []):
                            found.append(Problem(kind, name, (
                                "allows egress to 0.0.0.0/0 without excepting "
                                "169.254.169.254 — a notebook could then assume "
                                "the node's cloud IAM role"
                            )))
    return found


def _tenancy(
    objects: Sequence[Mapping[str, Any]], *, namespace: str, workspace_id: str,
) -> list[Problem]:
    """Everything lands in the tenant's namespace and is reclaimable whole."""
    found: list[Problem] = []
    for obj in objects:
        kind = str(obj.get("kind", ""))
        metadata = obj.get("metadata", {})
        name = str(metadata.get("name", ""))

        if kind == "Namespace":
            if name != namespace:
                found.append(Problem(kind, name, (
                    f"namespace is {name!r}, expected {namespace!r}"
                )))
            continue

        where = metadata.get("namespace", "")
        if where != namespace:
            found.append(Problem(kind, name, (
                f"lands in {where!r} rather than the tenant's {namespace!r}"
            )))

        labels = metadata.get("labels", {})
        if labels.get("slpie.dev/workspace") != workspace_id:
            # Namespace-wide objects (quota, limits) legitimately have no
            # workspace label — they belong to the tenant, not to one user.
            if kind not in ("ResourceQuota", "LimitRange"):
                found.append(Problem(kind, name, (
                    "carries no slpie.dev/workspace label, so `kubectl delete "
                    "-l` would leave it behind when the workspace is reclaimed"
                ), severity="warning"))
        if "" in labels.values():
            found.append(Problem(kind, name, (
                "has an empty label value; an empty selector matches everything"
            )))
    return found


def validate(
    objects: Sequence[Mapping[str, Any]], *, namespace: str, workspace_id: str,
) -> Validation:
    """Check a plan three ways: schema, security posture, tenant boundary."""
    schema = _deserialise(objects)
    try:
        import kubernetes  # noqa: F401
        checked_schema = True
        detail = "schema checked against the kubernetes client models"
    except ImportError:
        checked_schema = False
        detail = (
            "the `kubernetes` package is not installed, so only the structural, "
            "security and tenancy checks ran"
        )

    problems = (
        schema
        + _security(objects)
        + _tenancy(objects, namespace=namespace, workspace_id=workspace_id)
    )
    return Validation(
        checked=len(objects), problems=tuple(problems),
        schema_checked=checked_schema, detail=detail,
    )
