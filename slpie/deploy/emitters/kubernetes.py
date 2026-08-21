"""Raw manifests, for clusters without Helm — and the only emitter with elasticity.

One file per component plus the shared pieces, rather than one long document.
Multi-document YAML is legal and `kubectl apply -f .` reads a directory happily,
and a reviewer diffing a change to the worker pool should see a diff in the
worker file rather than a hunk two hundred lines into a combined one.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import command_for, environment_for, header, image, port_for

NAME = "kubernetes"


def render(deployment: Deployment) -> Mapping[str, str]:
    files: dict[str, str] = {
        "00-namespace.yaml": _namespace(deployment),
    }
    for component in deployment.components:
        body = _deployment(deployment, component)
        if port_for(component):
            body += ["---"] + _service(deployment, component)
        if component.ingress:
            body += ["---"] + _ingress(deployment, component)
        if component.elastic:
            body += ["---"] + _autoscaler(deployment, component)
        files[f"{component.name}.yaml"] = "\n".join(body) + "\n"
    return files


def _namespace(deployment: Deployment) -> str:
    lines = header(deployment) + [
        "",
        "apiVersion: v1",
        "kind: Namespace",
        "metadata:",
        f"  name: {deployment.environment}",
        "  labels:",
        f"    app.kubernetes.io/part-of: slpie",
        f"    slpie.io/environment: {deployment.environment}",
    ]
    return "\n".join(lines) + "\n"


def _meta(deployment: Deployment, component, indent: str = "  ") -> list[str]:
    return [
        f"{indent}name: {component.name}",
        f"{indent}namespace: {deployment.environment}",
        f"{indent}labels:",
        f"{indent}  app.kubernetes.io/name: {component.name}",
        f"{indent}  app.kubernetes.io/part-of: slpie",
    ]


def _deployment(deployment: Deployment, component) -> list[str]:
    values = environment_for(deployment)
    lines = header(deployment) + [
        "",
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
    ] + _meta(deployment, component) + [
        "spec:",
        f"  replicas: {component.size}",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {component.name}",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {component.name}",
        "    spec:",
        "      containers:",
        f"        - name: {component.name}",
        f"          image: {image()}",
        "          command: [" + ", ".join(f'"{part}"' for part in command_for(component)) + "]",
    ]
    if port := port_for(component):
        lines += [
            "          ports:",
            f"            - containerPort: {port}",
            # A readiness probe that never fails is decoration. `/api/status` is
            # a real route and answers without a database, which is exactly what
            # a probe needs: it says the process is serving, not that the
            # estate is healthy.
            "          readinessProbe:",
            "            httpGet: { path: /api/status, port: " + str(port) + " }",
            "            initialDelaySeconds: 5",
        ]
    if component.cpu or component.memory:
        lines += ["          resources:", "            requests:"]
        if component.cpu:
            lines.append(f"              cpu: \"{component.cpu}\"")
        if component.memory:
            lines.append(f"              memory: {component.memory}")
    if values:
        lines.append("          env:")
        for key, value in values.items():
            lines += [f"            - name: {key}", f"              value: \"{value}\""]
    # A pool that is drained rather than killed. §23's deallocation protocol is
    # four steps and this is the one Kubernetes can be told about: give the
    # worker its grace period, and the ledger append lands before release.
    if component.name != "api":
        seconds = _seconds(deployment.elasticity.drain_grace)
        lines.append(f"      terminationGracePeriodSeconds: {seconds}")
    return lines


def _service(deployment: Deployment, component) -> list[str]:
    port = port_for(component)
    return [
        "apiVersion: v1",
        "kind: Service",
        "metadata:",
    ] + _meta(deployment, component) + [
        "spec:",
        "  selector:",
        f"    app.kubernetes.io/name: {component.name}",
        "  ports:",
        f"    - port: {port}",
        f"      targetPort: {port}",
    ]


def _ingress(deployment: Deployment, component) -> list[str]:
    port = port_for(component) or 8765
    return [
        "apiVersion: networking.k8s.io/v1",
        "kind: Ingress",
        "metadata:",
    ] + _meta(deployment, component) + [
        "spec:",
        "  rules:",
        f"    - host: {component.ingress}",
        "      http:",
        "        paths:",
        "          - path: /",
        "            pathType: Prefix",
        "            backend:",
        "              service:",
        f"                name: {component.name}",
        f"                port: {{ number: {port} }}",
    ]


def _autoscaler(deployment: Deployment, component) -> list[str]:
    """The elasticity range, as an HPA — with the asymmetry §23 argues for.

    Scale up on a short window, down on a long one. Being briefly
    over-provisioned costs money; being briefly under-provisioned costs
    correctness, because a scan that times out is a gap in an answer. An HPA
    expresses that as stabilization windows, and it is the whole reason the
    numbers differ.
    """
    return [
        "apiVersion: autoscaling/v2",
        "kind: HorizontalPodAutoscaler",
        "metadata:",
    ] + _meta(deployment, component) + [
        "spec:",
        "  scaleTargetRef:",
        "    apiVersion: apps/v1",
        "    kind: Deployment",
        f"    name: {component.name}",
        f"  minReplicas: {component.minimum}",
        f"  maxReplicas: {component.maximum}",
        "  metrics:",
        "    - type: Resource",
        "      resource:",
        "        name: cpu",
        "        target: { type: Utilization, averageUtilization: 70 }",
        "  behavior:",
        "    scaleUp:",
        f"      stabilizationWindowSeconds: {_seconds(deployment.elasticity.scale_up_window)}",
        "    scaleDown:",
        f"      stabilizationWindowSeconds: {_seconds(deployment.elasticity.scale_down_window)}",
    ]


#: `30s`, `10m`, `2h` — the durations a manifest is written in.
_UNITS = {"s": 1, "m": 60, "h": 3600}


def _seconds(duration: str) -> int:
    """A declared duration in seconds, defaulting rather than raising.

    A malformed duration is not worth failing a render over: the manifest
    validator is where a bad value should have been caught, and reaching here
    with one means emitting something safe beats emitting nothing.
    """
    text = duration.strip()
    if not text:
        return 30
    unit = _UNITS.get(text[-1], 0)
    try:
        return int(float(text[:-1]) * unit) if unit else int(float(text))
    except ValueError:
        return 30


def gaps(deployment: Deployment) -> tuple[str, ...]:
    found = []
    if deployment.elasticity.curve == "logarithmic":
        # Honest about the shape of what was emitted. An HPA is proportional to
        # utilisation, not logarithmic in queue depth, and claiming otherwise
        # would be the manifest and the cluster disagreeing silently.
        found.append(
            "the declared logarithmic curve is rendered as a CPU-utilisation "
            "HPA: Kubernetes has no queue-depth curve built in. The sub-linear "
            "growth §23 describes needs the elasticity controller, not the HPA."
        )
    external = [
        store.name for store in deployment.persistence.stores
        if store.engine in ("s3", "gcs", "azure-blob", "postgres")
    ]
    if external:
        found.append(
            f"{', '.join(sorted(external))} is not rendered: a managed store is "
            f"the cloud emitter's business, and this one only writes workloads."
        )
    return tuple(found)
