"""OpenAPI 3.x and Swagger 2.0 — every operation, as its own node.

An API description is the closest thing a REST service has to a type signature,
and the unit that matters is the *operation*, not the document. `POST /v1/orders`
is what a client calls, what a breaking change breaks and what a deprecation
retires, so it gets its own identity: ``urn:slpie:api:rest/POST/v1/orders``.
A document-level node would make "who calls this endpoint" unanswerable.

``$ref`` targets become ``references``. That is what turns a schema change into
an answerable blast radius: `Order` is referenced by four operations and two
other schemas, and each of those citations points at a line.

Both dialects are read because both are everywhere. The differences that matter
are small and entirely positional — 3.x has ``servers`` and
``components.schemas``, 2.0 has ``host``/``basePath``/``schemes`` and
``definitions`` — so they are normalised here rather than leaking into every
consumer downstream.

JSON and YAML are both accepted. The YAML reader is SLPIE's own; a specification
using anchors or flow-mapped operations will fail to parse and be reported as an
error, which is preferable to a half-read contract.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Urn
from ...environment.schema import parse_yaml
from ...errors import ManifestError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, evidence_at, result

EXTRACTOR = "slpie.openapi"

#: The methods an OpenAPI path item may carry. Anything else in a path item is
#: metadata (`parameters`, `servers`, `summary`), not an operation.
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

_SLUG = re.compile(r"[^a-z0-9]+")


def load_specification(source: Source) -> tuple[Any, str]:
    """Parse a specification written as JSON or as YAML.

    Returns `(document, error)`. JSON is tried first when the text looks like
    JSON, because a JSON document handed to the YAML reader fails in a way that
    is confusing to read in an error message.
    """
    text = source.text.lstrip()
    if text.startswith(("{", "[")):
        try:
            return json.loads(source.text), ""
        except ValueError as error:
            return None, f"{source.uri}: not valid JSON ({error})"
    try:
        return parse_yaml(source.text), ""
    except ManifestError as error:
        return None, f"{source.uri}: not readable as YAML ({error})"


def slug(text: str) -> str:
    return _SLUG.sub("-", str(text).strip().lower()).strip("-") or "api"


def operation_urn(method: str, path: str) -> Urn:
    """``POST /v1/orders`` → ``urn:slpie:api:rest/POST/v1/orders``."""
    return Urn.create("api", "rest", method.upper(), path)


def schema_urn(scope: str, name: str) -> Urn:
    return Urn.create("schema", scope, name)


def service_urn(name: str) -> Urn:
    return Urn.create("service", name)


def discover_openapi(source: Source) -> DiscoveryResult:
    """Read operations, the service they belong to, and every `$ref`."""
    document, error = load_specification(source)
    if error:
        return result((), errors=[error])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected a mapping at the top level"])

    dialect = _dialect(document)
    if not dialect:
        return result(())

    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    title = str(info.get("title", "") or _basename(source.uri))
    scope = slug(title)
    service = service_urn(_service_name(document, dialect, scope)).to_string()

    def cite(*needles: str, kind: EvidenceKind = EvidenceKind.CONFIG_REFERENCE):
        line = _first_line(source, needles)
        return evidence_at(
            source.uri, kind=kind, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )

    observations: list[Observation] = [declares(
        service,
        cite("title", "openapi", "swagger", kind=EvidenceKind.MANIFEST_DECLARED),
        node_kind="service",
        api_title=title,
        api_version=str(info.get("version", "")),
        specification=dialect,
        base_urls=_base_urls(document, dialect),
    )]
    errors: list[str] = []

    paths = document.get("paths")
    if not isinstance(paths, dict):
        errors.append(f"{source.uri}: no paths section")
        paths = {}

    for path, item in paths.items():
        if not isinstance(item, Mapping):
            errors.append(f"{source.uri}: path {path!r} is not a mapping")
            continue
        for method in HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, Mapping):
                continue
            subject = operation_urn(method, str(path)).to_string()
            operation_id = str(operation.get("operationId", ""))
            evidence = cite(
                f'"{operation_id}"' if operation_id else "",
                f"operationId: {operation_id}" if operation_id else "",
                f"{method}:", f'"{method}"', str(path),
                kind=EvidenceKind.MANIFEST_DECLARED,
            )
            observations.append(declares(
                subject, evidence,
                node_kind="api",
                protocol="rest",
                method=method.upper(),
                path=str(path),
                operation_id=operation_id,
                summary=str(operation.get("summary", ""))[:200],
                tags=[str(tag) for tag in operation.get("tags", []) or ()],
                deprecated=bool(operation.get("deprecated", False)),
                # Presence, not contents: a security scheme's configuration is
                # not this discoverer's business.
                secured=bool(operation.get("security") or document.get("security")),
                status_codes=sorted(
                    str(code) for code in (operation.get("responses") or {})
                ) if isinstance(operation.get("responses"), Mapping) else [],
            ))
            observations.append(Observation(
                kind="owns", subject=service, object=subject,
                evidence=evidence, properties={"protocol": "rest"},
            ))
            for reference in _references(operation):
                observations.append(Observation(
                    kind="references", subject=subject,
                    object=_ref_identity(reference, scope).to_string(),
                    evidence=cite(reference, f"{method}:"),
                    qualifier="schema",
                    properties={"ref": reference},
                ))

    for name, schema in _schemas(document, dialect):
        subject = schema_urn(scope, name).to_string()
        evidence = cite(f'"{name}"', f"{name}:", kind=EvidenceKind.MANIFEST_DECLARED)
        observations.append(declares(
            subject, evidence, node_kind="schema",
            schema_name=name, specification=dialect, api_title=title,
        ))
        for reference in _references(schema):
            target = _ref_identity(reference, scope).to_string()
            if target == subject:
                continue
            observations.append(Observation(
                kind="references", subject=subject, object=target,
                evidence=cite(reference, f"{name}:"),
                qualifier="schema", properties={"ref": reference},
            ))

    return result(observations, errors=errors)


# --- dialect normalisation ----------------------------------------------


def _dialect(document: Mapping[str, Any]) -> str:
    version = str(document.get("openapi", ""))
    if version.startswith("3"):
        return f"openapi-{version}"
    version = str(document.get("swagger", ""))
    if version.startswith("2"):
        return f"swagger-{version}"
    return ""


def _base_urls(document: Mapping[str, Any], dialect: str) -> list[str]:
    if dialect.startswith("openapi"):
        return [
            str(server.get("url", ""))
            for server in document.get("servers", []) or ()
            if isinstance(server, Mapping) and server.get("url")
        ]
    host = str(document.get("host", ""))
    if not host:
        return []
    base = str(document.get("basePath", "") or "")
    schemes = [str(scheme) for scheme in document.get("schemes", []) or ()] or ["https"]
    return [f"{scheme}://{host}{base}" for scheme in schemes]


def _service_name(document: Mapping[str, Any], dialect: str, fallback: str) -> str:
    """The host the API is served from, which is what makes it *a* service."""
    for url in _base_urls(document, dialect):
        without_scheme = url.split("://", 1)[-1]
        host = without_scheme.split("/", 1)[0].strip()
        if host and "{" not in host:
            return host
    return fallback


def _schemas(document: Mapping[str, Any], dialect: str) -> Iterator[tuple[str, Any]]:
    if dialect.startswith("openapi"):
        components = document.get("components")
        container = components.get("schemas") if isinstance(components, Mapping) else None
    else:
        container = document.get("definitions")
    if isinstance(container, Mapping):
        for name, schema in container.items():
            yield str(name), schema


# --- $ref ----------------------------------------------------------------


def _references(node: Any, *, depth: int = 0) -> Iterator[str]:
    """Every `$ref` string in a subtree, in document order, deduplicated."""
    seen: set[str] = set()
    for reference in _walk_refs(node, depth=depth):
        if reference not in seen:
            seen.add(reference)
            yield reference


def _walk_refs(node: Any, *, depth: int = 0) -> Iterator[str]:
    if depth > 24:
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.strip():
                yield value.strip()
            else:
                yield from _walk_refs(value, depth=depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_refs(item, depth=depth + 1)


def _ref_identity(reference: str, scope: str) -> Urn:
    """`#/components/schemas/Order` is local; `common.yaml#/…/Money` is not.

    An external ref is scoped by the file it names, so two specifications that
    both define `Money` do not collapse into one node.
    """
    file_part, _, pointer = reference.partition("#")
    name = (pointer or file_part).rstrip("/").rsplit("/", 1)[-1] or "schema"
    if file_part.strip():
        return schema_urn(slug(_basename(file_part)), name)
    return schema_urn(scope, name)


def _basename(uri: str) -> str:
    return uri.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or uri


def _first_line(source: Source, needles: tuple[str, ...]) -> int:
    for needle in needles:
        if not needle:
            continue
        line = source.line(needle)
        if line:
            return line
    return 1


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.openapi", discover_openapi,
        ("openapi.json", "openapi.yaml", "openapi.yml",
         "swagger.json", "swagger.yaml", "swagger.yml",
         "*.openapi.json", "*.openapi.yaml", "*.openapi.yml",
         "api-spec.yaml", "api-spec.yml", "api-spec.json"),
        ("owns", "references", "declares"),
        (EvidenceKind.MANIFEST_DECLARED, EvidenceKind.CONFIG_REFERENCE),
    ),
)
