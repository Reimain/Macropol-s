"""A real chart — `Chart.yaml`, values, templates — not a directory of manifests.

The distinction matters. A chart whose templates hardcode what `values.yaml`
declares is a Kubernetes emitter wearing Helm's filenames: it installs, and then
`helm upgrade --set workers.replicas=8` does nothing, which is exactly the
surprise a chart is supposed to prevent.

So the values file carries the topology and the templates read it. What SLPIE's
manifest decides is the *default*; what the operator passes at upgrade time
wins, because that is what a chart is for.
"""

from __future__ import annotations

from typing import Mapping

from ..manifest import Deployment
from ._common import IMAGE, TAG, command_for, environment_for, header, image, port_for

NAME = "helm"

#: Bumped when the templates change shape, not when a deployment does. A chart
#: version that tracked the estate would make every scan a new release.
CHART_VERSION = "0.1.0"


def render(deployment: Deployment) -> Mapping[str, str]:
    return {
        "Chart.yaml": _chart(deployment),
        "values.yaml": _values(deployment),
        "templates/_helpers.tpl": _templated(deployment, _helpers()),
        "templates/deployment.yaml": _templated(deployment, _deployment_template()),
        "templates/service.yaml": _templated(deployment, _service_template()),
        "templates/hpa.yaml": _templated(deployment, _hpa_template()),
        "templates/NOTES.txt": _templated(deployment, _notes(deployment)),
    }


def _templated(deployment: Deployment, body: str) -> str:
    """A template with the same banner every other rendered file carries.

    In Go-template syntax rather than YAML's `#`, so the banner is consumed at
    render time and never reaches the cluster — a `# generated` comment inside
    `NOTES.txt` would be printed at the operator on every `helm install`.

    It is here because a template with no banner reads as hand-written, which is
    the one thing a generated file must never do: somebody edits it, the next
    render silently discards the edit, and they lose an afternoon.
    """
    banner = "\n".join(header(deployment, comment=""))
    return "{{/*\n" + banner + "\n*/}}\n" + body


def _chart(deployment: Deployment) -> str:
    return "\n".join(header(deployment) + [
        "",
        "apiVersion: v2",
        "name: slpie",
        f"description: SLPIE — architecture intelligence for {deployment.environment}",
        "type: application",
        f"version: {CHART_VERSION}",
        f"appVersion: \"{TAG}\"",
    ]) + "\n"


def _values(deployment: Deployment) -> str:
    lines = header(deployment) + [
        "",
        "image:",
        f"  repository: {IMAGE}",
        f"  tag: \"{TAG}\"",
        "  pullPolicy: IfNotPresent",
        "",
        f"environment: {deployment.environment}",
        "",
        "env:",
    ]
    for key, value in environment_for(deployment).items():
        lines.append(f"  {key}: \"{value}\"")

    lines += ["", "components:"]
    for component in deployment.components:
        lines += [
            f"  {component.name}:",
            f"    enabled: true",
            f"    replicas: {component.size}",
            "    command: [" + ", ".join(f'"{p}"' for p in command_for(component)) + "]",
            f"    port: {port_for(component)}",
        ]
        if component.cpu or component.memory:
            lines.append("    resources:")
            lines.append("      requests:")
            if component.cpu:
                lines.append(f"        cpu: \"{component.cpu}\"")
            if component.memory:
                lines.append(f"        memory: {component.memory}")
        lines.append(f"    autoscaling:")
        lines.append(f"      enabled: {str(component.elastic).lower()}")
        if component.elastic:
            lines.append(f"      minReplicas: {component.minimum}")
            lines.append(f"      maxReplicas: {component.maximum}")
        if component.ingress:
            lines.append(f"    ingress: {component.ingress}")
    return "\n".join(lines) + "\n"


def _helpers() -> str:
    return (
        '{{- define "slpie.labels" -}}\n'
        'app.kubernetes.io/name: {{ .name }}\n'
        'app.kubernetes.io/part-of: slpie\n'
        'app.kubernetes.io/managed-by: {{ .root.Release.Service }}\n'
        '{{- end }}\n'
    )


def _deployment_template() -> str:
    return (
        '{{- range $name, $c := .Values.components }}\n'
        '{{- if $c.enabled }}\n'
        '---\n'
        'apiVersion: apps/v1\n'
        'kind: Deployment\n'
        'metadata:\n'
        '  name: {{ $name }}\n'
        '  labels:\n'
        '    {{- include "slpie.labels" (dict "name" $name "root" $) | nindent 4 }}\n'
        'spec:\n'
        '  {{- if not $c.autoscaling.enabled }}\n'
        '  replicas: {{ $c.replicas }}\n'
        '  {{- end }}\n'
        '  selector:\n'
        '    matchLabels:\n'
        '      app.kubernetes.io/name: {{ $name }}\n'
        '  template:\n'
        '    metadata:\n'
        '      labels:\n'
        '        app.kubernetes.io/name: {{ $name }}\n'
        '    spec:\n'
        '      containers:\n'
        '        - name: {{ $name }}\n'
        '          image: "{{ $.Values.image.repository }}:{{ $.Values.image.tag }}"\n'
        '          imagePullPolicy: {{ $.Values.image.pullPolicy }}\n'
        '          command: {{ toJson $c.command }}\n'
        '          {{- if $c.port }}\n'
        '          ports:\n'
        '            - containerPort: {{ $c.port }}\n'
        '          {{- end }}\n'
        '          {{- with $c.resources }}\n'
        '          resources: {{- toYaml . | nindent 12 }}\n'
        '          {{- end }}\n'
        '          env:\n'
        '            {{- range $key, $value := $.Values.env }}\n'
        '            - name: {{ $key }}\n'
        '              value: {{ $value | quote }}\n'
        '            {{- end }}\n'
        '{{- end }}\n'
        '{{- end }}\n'
    )


def _service_template() -> str:
    return (
        '{{- range $name, $c := .Values.components }}\n'
        '{{- if and $c.enabled $c.port }}\n'
        '---\n'
        'apiVersion: v1\n'
        'kind: Service\n'
        'metadata:\n'
        '  name: {{ $name }}\n'
        'spec:\n'
        '  selector:\n'
        '    app.kubernetes.io/name: {{ $name }}\n'
        '  ports:\n'
        '    - port: {{ $c.port }}\n'
        '      targetPort: {{ $c.port }}\n'
        '{{- end }}\n'
        '{{- end }}\n'
    )


def _hpa_template() -> str:
    return (
        '{{- range $name, $c := .Values.components }}\n'
        '{{- if and $c.enabled $c.autoscaling.enabled }}\n'
        '---\n'
        'apiVersion: autoscaling/v2\n'
        'kind: HorizontalPodAutoscaler\n'
        'metadata:\n'
        '  name: {{ $name }}\n'
        'spec:\n'
        '  scaleTargetRef:\n'
        '    apiVersion: apps/v1\n'
        '    kind: Deployment\n'
        '    name: {{ $name }}\n'
        '  minReplicas: {{ $c.autoscaling.minReplicas }}\n'
        '  maxReplicas: {{ $c.autoscaling.maxReplicas }}\n'
        '  metrics:\n'
        '    - type: Resource\n'
        '      resource:\n'
        '        name: cpu\n'
        '        target:\n'
        '          type: Utilization\n'
        '          averageUtilization: 70\n'
        '{{- end }}\n'
        '{{- end }}\n'
    )


def _notes(deployment: Deployment) -> str:
    ingress = [item for item in deployment.components if item.ingress]
    lines = [
        f"SLPIE is installed into {{{{ .Release.Namespace }}}} as "
        f"{deployment.environment}.",
        "",
    ]
    if ingress:
        lines += [f"  Console:  https://{item.ingress}" for item in ingress]
    else:
        lines.append("  No ingress declared. Reach the API with `kubectl port-forward`.")
    lines += [
        "",
        "  slpie deploy status     what is running against what was declared",
        "",
    ]
    return "\n".join(lines)


def gaps(deployment: Deployment) -> tuple[str, ...]:
    found = []
    stores = [
        store.name for store in deployment.persistence.stores
        if store.engine in ("postgres", "s3", "gcs", "azure-blob", "redis")
    ]
    if stores:
        found.append(
            f"this chart has no subchart for {', '.join(sorted(stores))}: a "
            f"managed store is deliberately not something an application chart "
            f"installs. Point the environment variables at existing ones."
        )
    if deployment.regions.replicas:
        found.append(
            "a chart installs into one cluster; the declared replica regions "
            "each need their own release."
        )
    return tuple(found)
