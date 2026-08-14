"""Dockerfiles — what an image is built *from*, and what is only built *here*.

The trap this module exists to avoid is the multi-stage build. A Dockerfile that
says ``FROM golang:1.22 AS builder`` and later ``FROM gcr.io/distroless/base``
plus ``COPY --from=builder`` names three things, and exactly two of them are
external images. Reporting ``builder`` as a dependency would invent a package
that does not exist, put it in the SBOM, and then fail to find advisories for it
forever. So every stage name declared with ``AS`` is remembered, and any later
reference to one — in a ``FROM`` or in ``COPY --from=`` — is recorded as an
internal stage reference and never as a dependency.

``ARG`` and ``ENV`` are substituted into ``FROM`` lines, because
``FROM python:${PYTHON_VERSION}`` is the normal way to write a parameterised
base image and an unsubstituted one is a dependency on a package named
``python:${PYTHON_VERSION}``.

Evidence is :data:`~slpie.domain.evidence.EvidenceKind.CONTAINER_MANIFEST`: a
Dockerfile states what *will* be pulled, which is one step weaker than a
lockfile recording what was.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, scope_of

EXTRACTOR = "slpie.dockerfile"

#: Not an image. `FROM scratch` inherits nothing, so there is nothing to depend on.
SCRATCH = "scratch"

_DIRECTIVE = re.compile(r"^\s*#\s*(escape|syntax)\s*=\s*(\S+)\s*$", re.I)
_STAGE_INDEX = re.compile(r"^\d+$")
_VARIABLE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?::?(?P<operator>[-+])(?P<alternate>[^}]*))?\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


# --- image references ----------------------------------------------------


def image_purl(reference: str) -> Purl | None:
    """Turn a Docker image reference into a purl, or None if it is not one.

    ``registry.example.com:5000/team/api:1.4@sha256:abc`` is a registry, a
    namespace, a name, a tag and a digest, and telling them apart is entirely
    positional: the first path segment is a registry only if it looks like a
    host. Getting that wrong turns ``team/api`` into a registry called ``team``.
    """
    text = (reference or "").strip()
    if not text or text.lower() == SCRATCH:
        return None
    if any(character in text for character in "${}"):
        # An unresolved variable is not an image name. Reporting one would put a
        # literal `${TAG}` — or a Helm template's `{{ .Values.image }}` — into
        # the SBOM, where nothing will ever match it.
        return None

    remainder, _, digest = text.partition("@")
    head, _, tail = remainder.rpartition(":")
    if head and "/" not in tail:
        repository, tag = head, tail
    else:
        repository, tag = remainder, ""

    segments = [part for part in repository.split("/") if part]
    if not segments:
        return None

    registry = ""
    if len(segments) > 1 and ("." in segments[0] or ":" in segments[0] or segments[0] == "localhost"):
        registry = segments[0]
        segments = segments[1:]
    if not segments:
        return None

    name = segments[-1]
    namespace = "/".join(segments[:-1])
    return Purl.create(
        "docker", name,
        namespace=namespace,
        version=digest or tag,
        qualifiers={"repository_url": registry} if registry else {},
    )


def built_image_purl(source: Source) -> Purl:
    """The identity of the image this Dockerfile builds.

    A Dockerfile does not name its own output, so the name comes from where the
    file lives: ``services/api/Dockerfile`` builds ``api``, and
    ``deploy/worker.Dockerfile`` builds ``worker``.
    """
    parts = [part for part in source.uri.replace("\\", "/").split("/") if part not in ("", ".")]
    name = parts[-1] if parts else "image"
    if name.endswith(".Dockerfile"):
        name = name[: -len(".Dockerfile")]
    elif name.startswith("Dockerfile."):
        name = name[len("Dockerfile.") :]
    elif name in ("Dockerfile", "Containerfile"):
        name = parts[-2] if len(parts) > 1 else scope_of(source.element, "image")
    return Purl.create("docker", name or "image")


# --- lexing --------------------------------------------------------------


def instructions(text: str, *, escape: str = "\\") -> Iterator[tuple[str, str, int]]:
    """Yield `(INSTRUCTION, argument, line)`, joining continuations.

    A continued instruction cites the line it *started* on, which is the line a
    reader would open to check it.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        start = index + 1
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        collected = [stripped]
        while collected[-1].endswith(escape) and index < len(lines):
            collected[-1] = collected[-1][: -len(escape)]
            nxt = lines[index].strip()
            index += 1
            if not nxt or nxt.startswith("#"):
                collected.append("")
                continue
            collected.append(nxt)

        joined = " ".join(part for part in collected if part).strip()
        if not joined:
            continue
        keyword, _, argument = joined.partition(" ")
        yield keyword.upper(), argument.strip(), start


def substitute(text: str, variables: dict[str, str]) -> str:
    """Expand `$VAR`, `${VAR}` and `${VAR:-default}` from build arguments."""

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare") or ""
        value = variables.get(name)
        operator, alternate = match.group("operator"), match.group("alternate")
        if operator == "+":
            return alternate or "" if value else ""
        if value:
            return value
        if operator == "-":
            return alternate or ""
        return match.group(0)

    return _VARIABLE.sub(replace, text)


def _split_assignment(argument: str) -> tuple[str, str]:
    name, _, value = argument.partition("=")
    return name.strip(), value.strip().strip("\"'")


# --- discovery -----------------------------------------------------------


def discover_dockerfile(source: Source) -> DiscoveryResult:
    """Read base images, stages, exposed ports and copied-from references."""
    text = source.text
    escape = "\\"
    for line in text.splitlines():
        directive = _DIRECTIVE.match(line)
        if directive and directive.group(1).lower() == "escape":
            escape = directive.group(2)
        elif line.strip() and not line.strip().startswith("#"):
            break

    image = built_image_purl(source).to_string()
    variables: dict[str, str] = {}
    stages: dict[str, int] = {}            # lowercased stage name -> ordinal
    bases: list[tuple[str, str, int]] = []  # (reference, stage, line)
    internal: list[tuple[str, str, int]] = []
    ports: list[str] = []
    labels: dict[str, str] = {}
    current_stage = ""
    errors: list[str] = []

    for keyword, argument, line in instructions(text, escape=escape):
        if keyword == "ARG":
            name, value = _split_assignment(argument)
            if name:
                variables.setdefault(name, value)
        elif keyword == "ENV":
            for name, value in _environment_pairs(argument):
                variables[name] = value
        elif keyword == "FROM":
            reference, current_stage = _parse_from(substitute(argument, variables))
            if not reference:
                errors.append(f"{source.uri}:{line}: FROM with no image")
                continue
            if _is_stage_reference(reference, stages):
                internal.append((reference, current_stage, line))
            else:
                bases.append((reference, current_stage, line))
            if current_stage:
                stages[current_stage.lower()] = len(stages)
            else:
                stages[str(len(stages))] = len(stages)
        elif keyword == "COPY" or keyword == "ADD":
            origin = _copy_from(argument)
            if origin:
                resolved = substitute(origin, variables)
                if _is_stage_reference(resolved, stages):
                    internal.append((resolved, current_stage, line))
                else:
                    bases.append((resolved, current_stage, line))
        elif keyword == "EXPOSE":
            ports.extend(part for part in substitute(argument, variables).split() if part)
        elif keyword == "LABEL":
            for name, value in _environment_pairs(argument):
                labels[name] = value

    observations: list[Observation] = [declares(
        image,
        evidence_at(
            source.uri, kind=EvidenceKind.CONTAINER_MANIFEST, extractor=EXTRACTOR,
            line=source.line("FROM") or 1,
            excerpt=source.excerpt(source.line("FROM") or 1),
            digest=source.digest,
        ),
        node_kind="package",
        ecosystem="docker",
        stages=[name for name in stages if not name.isdigit()],
        ports=ports,
        labels=labels,
        built_from=str(source.uri),
    )]

    seen: set[tuple[str, str]] = set()
    for reference, stage, line in bases:
        purl = image_purl(reference)
        if purl is None:
            # `scratch` and unresolved variables are recorded as read, not as
            # dependencies on a package that does not exist.
            observations.append(declares(
                image,
                evidence_at(
                    source.uri, kind=EvidenceKind.CONTAINER_MANIFEST, extractor=EXTRACTOR,
                    line=line, excerpt=source.excerpt(line), digest=source.digest,
                ),
                unresolved_base=reference,
            ))
            continue
        key = (purl.to_string(), stage)
        if key in seen:
            continue
        seen.add(key)
        observations.append(depends(
            image, purl.to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.CONTAINER_MANIFEST, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            qualifier=stage,
            ecosystem="docker",
            reference=reference,
            stage=stage,
        ))

    for reference, stage, line in internal:
        # Recorded so the explanation can say *why* `builder` is not a
        # dependency, rather than leaving a silent omission.
        observations.append(declares(
            image,
            evidence_at(
                source.uri, kind=EvidenceKind.CONTAINER_MANIFEST, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            internal_stage_reference=reference,
        ))

    return result(observations, errors=errors)


def _parse_from(argument: str) -> tuple[str, str]:
    """`golang:1.22 AS builder` and `--platform=linux/amd64 alpine` both parse."""
    tokens = [token for token in argument.split() if not token.startswith("--")]
    if not tokens:
        return "", ""
    reference = tokens[0]
    stage = ""
    for position, token in enumerate(tokens):
        if token.upper() == "AS" and position + 1 < len(tokens):
            stage = tokens[position + 1]
            break
    return reference, stage


def _copy_from(argument: str) -> str:
    for token in argument.split():
        if token.lower().startswith("--from="):
            return token.partition("=")[2].strip()
        if not token.startswith("--"):
            break
    return ""


def _is_stage_reference(reference: str, stages: dict[str, int]) -> bool:
    """A name declared by an earlier `AS`, or a bare stage index."""
    return reference.lower() in stages or bool(_STAGE_INDEX.match(reference))


def _environment_pairs(argument: str) -> Iterator[tuple[str, str]]:
    """`ENV A=1 B=2` and the legacy `ENV A 1` both appear in real files."""
    if "=" not in argument:
        name, _, value = argument.partition(" ")
        if name:
            yield name.strip(), value.strip().strip("\"'")
        return
    for token in _split_respecting_quotes(argument):
        if "=" in token:
            name, value = _split_assignment(token)
            if name:
                yield name, value


def _split_respecting_quotes(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    for character in text:
        if quote:
            if character == quote:
                quote = ""
            else:
                current.append(character)
        elif character in "\"'":
            quote = character
        elif character.isspace():
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(character)
    if current:
        parts.append("".join(current))
    return parts


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.dockerfile", discover_dockerfile,
        ("Dockerfile", "Dockerfile.*", "*.Dockerfile", "Containerfile", "Containerfile.*"),
        ("depends_on", "declares"),
        (EvidenceKind.CONTAINER_MANIFEST,),
    ),
)
