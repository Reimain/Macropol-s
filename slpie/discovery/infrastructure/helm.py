"""Helm charts — the chart's identity, its dependencies, and its default images.

Two files are read, and one directory is deliberately not.

``Chart.yaml`` is the chart's manifest: name, version, and the subcharts it
pulls in. Those are real declared dependencies and get
:data:`~slpie.domain.evidence.EvidenceKind.IAC_DECLARATION`.

``values.yaml`` holds *defaults*, and defaults are overridden at install time by
`-f`, by `--set`, and by whatever the umbrella chart says. So an image found in
values.yaml is reported as
:data:`~slpie.domain.evidence.EvidenceKind.CONFIG_REFERENCE` — a name appearing
in configuration, not a resolved fact. If the cluster is also attached, the
Kubernetes discoverer will report what is actually running and the two
observations corroborate or contradict each other; that comparison is the point,
and it only works if this one does not overclaim.

**Templates are not parsed.** Everything under ``templates/`` is a Go template,
and a Go template is not YAML — ``{{- if .Values.ingress.enabled }}`` is a
syntax error to any YAML reader, and ``replicas: {{ .Values.count }}`` parses
only by accident. Rendering them properly needs the Go template engine with the
chart's values, the release name, and the capabilities of the target cluster,
none of which a static reader has. So this module reports that a templates
directory exists and stops. The alternative — regex-stripping the delimiters and
parsing the wreckage — produces workloads that were never deployed and images
that were never pulled, which is worse than the gap.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...environment.schema import parse_yaml
from ...errors import ManifestError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result
from .dockerfile import image_purl

EXTRACTOR = "slpie.helm"

#: Depth beyond which a values tree is not worth walking. Pathological nesting
#: must not cost the scan its stack.
MAX_DEPTH = 12


def chart_purl(name: str, version: str = "") -> Purl:
    return Purl.create("helm", name or "chart", version=version)


def _chart_directory(uri: str) -> str:
    parts = [part for part in uri.replace("\\", "/").split("/") if part not in ("", ".")]
    return parts[-2] if len(parts) > 1 else "chart"


# --- Chart.yaml ----------------------------------------------------------


def discover_chart(source: Source) -> DiscoveryResult:
    """Read a chart's identity and the subcharts it declares."""
    try:
        document = parse_yaml(source.text)
    except ManifestError as error:
        return result((), errors=[f"{source.uri}: not readable as YAML ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected a mapping at the top level"])

    name = str(document.get("name") or _chart_directory(source.uri))
    version = str(document.get("version", "") or "")
    subject = chart_purl(name, version).to_string()
    line = source.line("name:") or 1

    observations: list[Observation] = [declares(
        subject,
        evidence_at(
            source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        node_kind="package",
        ecosystem="helm",
        chart_name=name,
        chart_version=version,
        app_version=str(document.get("appVersion", "")),
        chart_type=str(document.get("type", "application")),
        # Stated on the chart itself so the gap is visible in the graph rather
        # than only in this module's docstring.
        templates_rendered=False,
    )]
    errors: list[str] = []

    for dependency in document.get("dependencies") or ():
        if not isinstance(dependency, Mapping):
            errors.append(f"{source.uri}: a dependency entry is not a mapping")
            continue
        dependency_name = str(dependency.get("name", "")).strip()
        if not dependency_name:
            errors.append(f"{source.uri}: a dependency entry has no name")
            continue
        dependency_line = source.line(f"name: {dependency_name}") or line
        observations.append(depends(
            subject,
            chart_purl(dependency_name).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.IAC_DECLARATION, extractor=EXTRACTOR,
                line=dependency_line, excerpt=source.excerpt(dependency_line),
                digest=source.digest,
            ),
            qualifier="subchart",
            ecosystem="helm",
            range=str(dependency.get("version", "")),
            repository=str(dependency.get("repository", "")),
            alias=str(dependency.get("alias", "")),
        ))

    return result(observations, errors=errors)


# --- values.yaml ---------------------------------------------------------


def discover_values(source: Source) -> DiscoveryResult:
    """Read image repositories and tags out of a chart's defaults."""
    try:
        document = parse_yaml(source.text)
    except ManifestError as error:
        return result((), errors=[f"{source.uri}: not readable as YAML ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected a mapping at the top level"])

    subject = chart_purl(_chart_directory(source.uri)).to_string()
    observations: list[Observation] = []
    seen: set[str] = set()

    for path, reference in _image_references(document):
        purl = image_purl(reference)
        if purl is None or purl.to_string() in seen:
            continue
        seen.add(purl.to_string())
        line = source.line(reference.split(":")[0]) or 1
        observations.append(depends(
            subject, purl.to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.CONFIG_REFERENCE, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            qualifier="values-default",
            ecosystem="docker",
            reference=reference,
            values_path=path,
            # A default, not a deployment: `--set` overrides this at install.
            overridable=True,
        ))

    return result(observations)


def _image_references(node: Any, *, path: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """Every image reference in a values tree, with the path that produced it.

    Two spellings are in the wild: a mapping with ``repository`` and ``tag``,
    and a plain string. Both are recognised; anything else is left alone rather
    than guessed at.
    """
    if depth > MAX_DEPTH or not isinstance(node, Mapping):
        return
    for key, value in node.items():
        here = f"{path}.{key}" if path else str(key)
        if isinstance(value, Mapping):
            repository = value.get("repository")
            if isinstance(repository, str) and repository.strip():
                tag = value.get("tag")
                registry = value.get("registry")
                reference = repository.strip()
                if isinstance(registry, str) and registry.strip():
                    reference = f"{registry.strip().rstrip('/')}/{reference}"
                if tag not in (None, ""):
                    reference = f"{reference}:{tag}"
                yield here, reference
            yield from _image_references(value, path=here, depth=depth + 1)
        elif str(key) == "image" and isinstance(value, str) and value.strip():
            yield here, value.strip()


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.helm.chart", discover_chart,
        ("Chart.yaml", "Chart.yml"),
        ("depends_on", "declares"),
        (EvidenceKind.IAC_DECLARATION,),
    ),
    (
        "slpie.helm.values", discover_values,
        ("values.yaml", "values.yml", "values-*.yaml", "values-*.yml"),
        ("depends_on",),
        (EvidenceKind.CONFIG_REFERENCE,),
    ),
)
