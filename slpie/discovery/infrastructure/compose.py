"""Docker Compose — the services on one host, and the order they need.

A compose file is the smallest complete description of a deployment: it names
services, the images or build contexts behind them, which services must start
before which, and what is published where. Each service becomes a
``urn:slpie:service:`` node and ``deploys_to`` the deployment the file itself
describes, so "what runs together" is answerable without inferring it from
directory layout.

``depends_on`` between services is read as written — compose's own
``service_started``/``service_healthy`` conditions are carried as a qualifier
rather than flattened away, because "waits for healthy" and "waits for started"
are different facts about the same pair.

Environment values are counted, never recorded. A compose file is one of the
most common places a credential is pasted, and a discoverer that excerpts
``environment:`` puts it in the graph, the ledger and every export downstream.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...environment.schema import parse_yaml
from ...errors import ManifestError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, scope_of
from .dockerfile import image_purl

EXTRACTOR = "slpie.compose"


def deployment_urn(source: Source) -> Urn:
    """The deployment a compose file describes — named for where it lives."""
    parts = [p for p in source.uri.replace("\\", "/").split("/") if p not in ("", ".")]
    stack = parts[-2] if len(parts) > 1 else scope_of(source.element, "compose")
    return Urn.create("deployment", "compose", stack)


def service_urn(source: Source, name: str) -> Urn:
    return Urn.create("service", scope_of(source.element, "compose"), name)


def discover_compose(source: Source) -> DiscoveryResult:
    """Read services, their images, their ordering and what they publish."""
    try:
        document = parse_yaml(source.text)
    except ManifestError as error:
        return result((), errors=[f"{source.uri}: not readable as YAML ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected a mapping at the top level"])

    services = document.get("services")
    if not isinstance(services, dict):
        return result((), errors=[f"{source.uri}: no services section"])

    deployment = deployment_urn(source).to_string()
    observations: list[Observation] = [declares(
        deployment,
        evidence_at(
            source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
            line=source.line("services:") or 1,
            excerpt=source.excerpt(source.line("services:") or 1),
            digest=source.digest,
        ),
        node_kind="deployment",
        orchestrator="compose",
        compose_version=str(document.get("version", "")),
        service_names=sorted(str(name) for name in services),
    )]
    errors: list[str] = []

    for name, definition in services.items():
        service = service_urn(source, str(name)).to_string()
        line = source.line(f"{name}:")
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )
        if not isinstance(definition, dict):
            errors.append(f"{source.uri}: service {name!r} is not a mapping")
            definition = {}

        build = definition.get("build")
        observations.append(declares(
            service, evidence,
            node_kind="service",
            orchestrator="compose",
            service_name=str(name),
            image=str(definition.get("image", "")),
            build_context=_build_context(build),
            dockerfile=_build_dockerfile(build),
            ports=[str(port) for port in _sequence(definition.get("ports"))],
            volumes=[str(volume) for volume in _sequence(definition.get("volumes"))],
            # The count, never the values: a compose environment block is where
            # credentials get pasted.
            environment_keys=sorted(_environment_names(definition.get("environment"))),
            restart=str(definition.get("restart", "")),
        ))
        observations.append(Observation(
            kind="deploys_to", subject=service, object=deployment,
            evidence=evidence, properties={"orchestrator": "compose"},
        ))

        reference = definition.get("image")
        if isinstance(reference, str) and reference.strip():
            purl = image_purl(reference)
            if purl is not None:
                image_line = source.line(f"image: {reference}") or line
                observations.append(depends(
                    service, purl.to_string(),
                    evidence_at(
                        source.uri, kind=EvidenceKind.CONTAINER_MANIFEST, extractor=EXTRACTOR,
                        line=image_line, excerpt=source.excerpt(image_line),
                        digest=source.digest,
                    ),
                    ecosystem="docker", reference=reference,
                ))

        for other, condition in _dependencies(definition.get("depends_on")):
            if other not in services:
                errors.append(
                    f"{source.uri}: service {name!r} depends on {other!r}, "
                    f"which this file does not define"
                )
            observations.append(depends(
                service, service_urn(source, other).to_string(),
                evidence, qualifier=condition, orchestrator="compose",
            ))

    for network in _keys(document.get("networks")):
        line = source.line(f"{network}:")
        observations.append(declares(
            Urn.create("configuration", "compose-network", scope_of(source.element, "compose"), network).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="configuration", network_name=network,
        ))

    return result(observations, errors=errors)


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _build_context(build: Any) -> str:
    if isinstance(build, str):
        return build
    if isinstance(build, dict):
        return str(build.get("context", ""))
    return ""


def _build_dockerfile(build: Any) -> str:
    if isinstance(build, dict):
        return str(build.get("dockerfile", ""))
    return ""


def _environment_names(environment: Any) -> Iterable[str]:
    if isinstance(environment, dict):
        return [str(key) for key in environment]
    if isinstance(environment, list):
        return [str(item).partition("=")[0] for item in environment if item is not None]
    return []


def _dependencies(value: Any) -> list[tuple[str, str]]:
    """Both compose forms: a list of names, or a mapping with conditions."""
    if isinstance(value, list):
        return [(str(item), "") for item in value if item]
    if isinstance(value, dict):
        pairs: list[tuple[str, str]] = []
        for name, options in value.items():
            condition = ""
            if isinstance(options, Mapping):
                condition = str(options.get("condition", ""))
            pairs.append((str(name), condition))
        return pairs
    return []


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.compose", discover_compose,
        ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
         "docker-compose.*.yml", "docker-compose.*.yaml", "compose.*.yml", "compose.*.yaml"),
        ("depends_on", "deploys_to", "declares"),
        (EvidenceKind.IAC_DECLARATION, EvidenceKind.CONTAINER_MANIFEST),
    ),
)
