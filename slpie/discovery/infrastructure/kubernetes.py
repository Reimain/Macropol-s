"""Kubernetes manifests — what is scheduled, and what it is wired to.

Two things make this harder than it looks.

**Multi-document streams.** A single `k8s.yaml` routinely holds a Deployment, a
Service, an Ingress and a ConfigMap separated by `---`. Reading only the first
document is the classic failure, and it is silent: you get a workload with no
service and no explanation of why. So the stream is split, every document is
parsed, and each one's evidence cites its own line in the original file — a
container image declared in the third document reports the line a human would
actually open.

**Secrets are recorded, never read.** A ``Secret`` document produces exactly one
observation: this Secret exists, at this line, in this namespace. Its ``data``
and ``stringData`` are never parsed, never excerpted and never carried in a
property. A discoverer that quoted a Secret's line would copy the credential
into the graph, the ledger and every SBOM export downstream, where it would
outlive the rotation that was supposed to fix it. The workloads that *reference*
a Secret are still recorded, which is the part impact analysis needs.

A document without both ``apiVersion`` and ``kind`` is not a Kubernetes
manifest and is left alone — that is what keeps this discoverer off Helm's
``Chart.yaml`` and off compose files.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...environment.schema import parse_yaml, split_documents
from ...errors import ManifestError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, excerpt_at, find_line, result
from .dockerfile import image_purl

EXTRACTOR = "slpie.kubernetes"

#: Kinds that schedule containers. Everything here is a workload node.
WORKLOAD_KINDS = frozenset({
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet",
    "Job", "CronJob", "Pod",
})

DEFAULT_NAMESPACE = "default"

#: The marker that says "this is a Helm template, not a manifest".
GO_TEMPLATE = "{{"


def workload_urn(namespace: str, name: str) -> Urn:
    return Urn.create("deployment", "k8s", namespace, name)


def service_urn(namespace: str, name: str) -> Urn:
    return Urn.create("service", "k8s", namespace, name)


def config_urn(namespace: str, name: str) -> Urn:
    return Urn.create("configuration", "k8s", namespace, name)


def secret_urn(namespace: str, name: str) -> Urn:
    return Urn.create("secret", "k8s", namespace, name)


def looks_like_kubernetes(text: str) -> bool:
    """Cheap pre-check so a values.yaml is not parsed five times over."""
    return "apiVersion:" in text and "kind:" in text


def discover_kubernetes(source: Source) -> DiscoveryResult:
    """Read every document in the stream, and report what each one declares."""
    if not looks_like_kubernetes(source.text):
        return result(())

    observations: list[Observation] = []
    errors: list[str] = []

    for body, first_line in split_documents(source.text):
        if GO_TEMPLATE in body:
            # A Helm template is not YAML. Rendering it needs the Go template
            # engine, the chart's values and the target cluster's capabilities;
            # parsing it anyway invents workloads nobody deployed.
            errors.append(
                f"{source.uri}: document at line {first_line} is a Go template, "
                f"not YAML; it is not rendered here"
            )
            continue
        try:
            document = parse_yaml(body)
        except ManifestError as error:
            # One malformed document must not cost the file the others.
            errors.append(f"{source.uri}: document at line {first_line} ({error})")
            continue
        if not isinstance(document, dict):
            continue
        if not document.get("apiVersion") or not document.get("kind"):
            continue
        observations.extend(_document(source, document, body, first_line))

    return result(observations, errors=errors)


def _document(
    source: Source, document: Mapping[str, Any], body: str, offset: int
) -> Iterator[Observation]:
    kind = str(document.get("kind", ""))
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    name = str(metadata.get("name", "") or "unnamed")
    namespace = str(metadata.get("namespace", "") or DEFAULT_NAMESPACE)

    def cite(needle: str, *, evidence_kind: EvidenceKind = EvidenceKind.IAC_DECLARATION):
        local = find_line(body, needle)
        line = local + offset - 1 if local else offset
        return evidence_at(
            source.uri, kind=evidence_kind, extractor=EXTRACTOR,
            line=line, excerpt=excerpt_at(body, local) if local else "",
            digest=source.digest,
        )

    identity = cite(f"kind: {kind}")

    if kind == "Secret":
        # Everything this module will ever say about a Secret. No `data`, no
        # `stringData`, no excerpt of either — see the module docstring.
        yield declares(
            secret_urn(namespace, name).to_string(),
            identity,
            node_kind="secret",
            secret_name=name, namespace=namespace,
            secret_type=str(document.get("type", "")),
            contents_read=False,
        )
        return

    if kind in WORKLOAD_KINDS:
        subject = workload_urn(namespace, name).to_string()
        pod = _pod_spec(document)
        containers = list(_containers(pod))
        yield declares(
            subject, identity,
            node_kind="deployment",
            workload_kind=kind, workload_name=name, namespace=namespace,
            replicas=document.get("spec", {}).get("replicas") if isinstance(document.get("spec"), dict) else None,
            container_names=[str(c.get("name", "")) for c in containers],
            orchestrator="kubernetes",
        )
        for container in containers:
            reference = str(container.get("image", "")).strip()
            if not reference:
                continue
            purl = image_purl(reference)
            if purl is None:
                continue
            yield depends(
                subject, purl.to_string(),
                cite(f"image: {reference}", evidence_kind=EvidenceKind.CONTAINER_MANIFEST),
                qualifier=str(container.get("name", "")),
                ecosystem="docker", reference=reference,
            )
        for target_kind, target_name, needle in _config_references(pod, containers):
            urn = (secret_urn if target_kind == "secret" else config_urn)(namespace, target_name)
            yield Observation(
                kind="references", subject=subject, object=urn.to_string(),
                evidence=cite(needle, evidence_kind=EvidenceKind.CONFIG_REFERENCE),
                qualifier=target_kind,
                properties={"namespace": namespace, "referenced_kind": target_kind},
            )
        return

    if kind == "Service":
        spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
        yield declares(
            service_urn(namespace, name).to_string(), identity,
            node_kind="service",
            service_name=name, namespace=namespace,
            service_type=str(spec.get("type", "ClusterIP")),
            ports=[str(port.get("port", "")) for port in spec.get("ports", []) if isinstance(port, dict)],
            selector=sorted(str(key) for key in (spec.get("selector") or {})) if isinstance(spec.get("selector"), dict) else [],
            orchestrator="kubernetes",
        )
        return

    if kind == "Ingress":
        yield from _ingress(document, identity, cite, namespace, name)
        return

    if kind == "ConfigMap":
        data = document.get("data") if isinstance(document.get("data"), dict) else {}
        yield declares(
            config_urn(namespace, name).to_string(), identity,
            node_kind="configuration",
            config_name=name, namespace=namespace,
            keys=sorted(str(key) for key in data),
        )
        return

    yield declares(
        Urn.create("cloudresource", "k8s", namespace, kind, name).to_string(), identity,
        node_kind="cloud_resource",
        resource_kind=kind, resource_name=name, namespace=namespace,
        api_version=str(document.get("apiVersion", "")),
    )


def _ingress(
    document: Mapping[str, Any], identity: Any, cite: Any, namespace: str, name: str
) -> Iterator[Observation]:
    spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
    rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
    emitted = False
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        host = str(rule.get("host", "") or "*")
        http = rule.get("http") if isinstance(rule.get("http"), Mapping) else {}
        paths = http.get("paths") if isinstance(http.get("paths"), list) else []
        for entry in paths:
            if not isinstance(entry, Mapping):
                continue
            path = str(entry.get("path", "/") or "/")
            api = Urn.create("api", "http", host, path).to_string()
            emitted = True
            yield declares(
                api, identity,
                node_kind="api",
                protocol="http", host=host, path=path,
                ingress=name, namespace=namespace,
                path_type=str(entry.get("pathType", "")),
            )
            backend = _backend_service(entry.get("backend"))
            if backend:
                yield Observation(
                    kind="references", subject=api,
                    object=service_urn(namespace, backend).to_string(),
                    evidence=cite(f"name: {backend}"),
                    qualifier="backend",
                    properties={"namespace": namespace},
                )
    if not emitted:
        yield declares(
            Urn.create("api", "http", "ingress", namespace, name).to_string(), identity,
            node_kind="api", protocol="http", ingress=name, namespace=namespace,
        )


def _backend_service(backend: Any) -> str:
    if not isinstance(backend, Mapping):
        return ""
    service = backend.get("service")
    if isinstance(service, Mapping):
        return str(service.get("name", ""))
    # networking.k8s.io/v1beta1 spelled it differently.
    return str(backend.get("serviceName", ""))


def _pod_spec(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Reach the pod spec, wherever this kind buries it.

    A CronJob nests it two templates deep, which is the single most common
    reason a container image in a CronJob goes unreported.
    """
    spec = document.get("spec")
    if not isinstance(spec, Mapping):
        return {}
    if str(document.get("kind")) == "Pod":
        return spec
    if str(document.get("kind")) == "CronJob":
        job = spec.get("jobTemplate")
        spec = job.get("spec") if isinstance(job, Mapping) else {}
        if not isinstance(spec, Mapping):
            return {}
    template = spec.get("template")
    if isinstance(template, Mapping) and isinstance(template.get("spec"), Mapping):
        return template["spec"]
    return spec if isinstance(spec.get("containers"), list) else {}


def _containers(pod: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    for key in ("initContainers", "containers", "ephemeralContainers"):
        for container in pod.get(key) or ():
            if isinstance(container, Mapping):
                yield container


def _config_references(
    pod: Mapping[str, Any], containers: list[Mapping[str, Any]]
) -> Iterator[tuple[str, str, str]]:
    """Every ConfigMap and Secret a workload names, and the needle to cite."""
    seen: set[tuple[str, str]] = set()

    def remember(target_kind: str, name: Any, needle: str) -> Iterator[tuple[str, str, str]]:
        text = str(name or "").strip()
        if not text or (target_kind, text) in seen:
            return
        seen.add((target_kind, text))
        yield target_kind, text, needle

    for container in containers:
        for entry in container.get("envFrom") or ():
            if not isinstance(entry, Mapping):
                continue
            source_ref = entry.get("configMapRef")
            if isinstance(source_ref, Mapping):
                yield from remember("configmap", source_ref.get("name"), f"name: {source_ref.get('name')}")
            source_ref = entry.get("secretRef")
            if isinstance(source_ref, Mapping):
                yield from remember("secret", source_ref.get("name"), f"name: {source_ref.get('name')}")
        for entry in container.get("env") or ():
            if not isinstance(entry, Mapping):
                continue
            value_from = entry.get("valueFrom")
            if not isinstance(value_from, Mapping):
                continue
            ref = value_from.get("configMapKeyRef")
            if isinstance(ref, Mapping):
                yield from remember("configmap", ref.get("name"), f"name: {ref.get('name')}")
            ref = value_from.get("secretKeyRef")
            if isinstance(ref, Mapping):
                yield from remember("secret", ref.get("name"), f"name: {ref.get('name')}")

    for volume in pod.get("volumes") or ():
        if not isinstance(volume, Mapping):
            continue
        config_map = volume.get("configMap")
        if isinstance(config_map, Mapping):
            yield from remember("configmap", config_map.get("name"), f"name: {config_map.get('name')}")
        secret = volume.get("secret")
        if isinstance(secret, Mapping):
            name = secret.get("secretName") or secret.get("name")
            yield from remember("secret", name, f"secretName: {name}")

    for entry in pod.get("imagePullSecrets") or ():
        if isinstance(entry, Mapping):
            yield from remember("secret", entry.get("name"), f"name: {entry.get('name')}")


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.kubernetes", discover_kubernetes,
        ("*.yaml", "*.yml"),
        ("depends_on", "references", "declares"),
        (EvidenceKind.IAC_DECLARATION, EvidenceKind.CONTAINER_MANIFEST,
         EvidenceKind.CONFIG_REFERENCE),
    ),
)
