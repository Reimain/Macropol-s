"""Python packaging — pyproject, requirements, Poetry and uv locks.

Read with `tomllib` from the standard library, which is why the Python floor is
3.11. A third-party TOML parser would have breached the zero-dependency rule to
read the very file that declares dependencies.

PyPI names normalise: `Typing_Extensions`, `typing-extensions` and
`typing.extensions` are one distribution. That normalisation happens in
:func:`slpie.domain.identity` so the same rule applies everywhere, and it is why
a requirements.txt and a poetry.lock spelling the same package differently still
produce one node.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, workspace_purl

EXTRACTOR = "slpie.python"

#: PEP 508: `name[extra1,extra2] >= 1.0, < 2.0 ; python_version < "3.12"`
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*(?P<spec>(?:[<>=!~^][^;#]*)?)"
    r"(?:;\s*(?P<marker>[^#]*))?"
)

#: Lines a requirements file may contain that are not requirements.
_DIRECTIVE = ("-r", "-c", "--", "-e", "#", "git+", "http:", "https:")


def python_purl(name: str, version: str = "") -> Purl:
    return Purl.create("pypi", name, version=version)


# --- pyproject.toml ------------------------------------------------------


def discover_pyproject(source: Source) -> DiscoveryResult:
    """PEP 621 dependencies, plus Poetry's own layout where present."""
    try:
        document = tomllib.loads(source.text)
    except tomllib.TOMLDecodeError as error:
        return result((), errors=[f"{source.uri}: not valid TOML ({error})"])

    project = document.get("project") or {}
    poetry = (document.get("tool") or {}).get("poetry") or {}
    name = str(project.get("name") or poetry.get("name") or source.element or "project")
    version = str(project.get("version") or poetry.get("version") or "")
    subject = workspace_purl("pypi", name, version).to_string()

    line = source.line(f'name = "{name}"') or source.line("[project]")
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package",
            license=_license(project, poetry),
            requires_python=str(project.get("requires-python", "")),
            ecosystem="pypi",
        )
    ]

    for requirement in project.get("dependencies") or ():
        observations.extend(_requirement(source, subject, str(requirement), scope="runtime"))

    for extra, requirements in (project.get("optional-dependencies") or {}).items():
        for requirement in requirements:
            observations.extend(_requirement(
                source, subject, str(requirement), scope=str(extra), qualifier="optional",
            ))

    # Poetry keeps dependencies as a table rather than a list of PEP 508 strings.
    for dependency, constraint in (poetry.get("dependencies") or {}).items():
        if dependency == "python":
            continue
        observations.extend(_poetry_dependency(source, subject, str(dependency), constraint))
    for group, body in (poetry.get("group") or {}).items():
        for dependency, constraint in (body.get("dependencies") or {}).items():
            observations.extend(_poetry_dependency(
                source, subject, str(dependency), constraint, qualifier=str(group),
            ))

    if requires_python := str(project.get("requires-python") or poetry.get("python") or ""):
        runtime_line = source.line("requires-python") or source.line("python =")
        observations.append(depends(
            subject, Purl.create("generic", "python").to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=runtime_line, excerpt=source.excerpt(runtime_line),
            ),
            qualifier="runtime", range=requires_python, ecosystem="generic",
        ))

    return result(observations)


def _license(project: Mapping[str, Any], poetry: Mapping[str, Any]) -> str:
    stated = project.get("license") or poetry.get("license") or ""
    if isinstance(stated, Mapping):
        return str(stated.get("text") or stated.get("file") or "")
    return str(stated)


def _requirement(
    source: Source, subject: str, requirement: str, *, scope: str, qualifier: str = ""
) -> list[Observation]:
    parsed = _REQUIREMENT.match(requirement)
    if not parsed or not parsed.group("name"):
        return []

    name = parsed.group("name")
    line = source.line(requirement) or source.line(name)
    return [depends(
        subject, python_purl(name).to_string(),
        evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line) or requirement, digest=source.digest,
        ),
        qualifier=qualifier,
        range=(parsed.group("spec") or "").strip(),
        marker=(parsed.group("marker") or "").strip(),
        extras=(parsed.group("extras") or "").strip(),
        scope=scope,
        ecosystem="pypi",
    )]


def _poetry_dependency(
    source: Source, subject: str, name: str, constraint: Any, *, qualifier: str = ""
) -> list[Observation]:
    if isinstance(constraint, Mapping):
        declared = str(constraint.get("version", "*"))
        optional = bool(constraint.get("optional", False))
    else:
        declared = str(constraint)
        optional = False

    line = source.line(f'{name} = ')
    return [depends(
        subject, python_purl(name).to_string(),
        evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        qualifier=qualifier or ("optional" if optional else ""),
        range=declared, ecosystem="pypi", scope="poetry",
    )]


# --- requirements.txt ----------------------------------------------------


def discover_requirements(source: Source) -> DiscoveryResult:
    """A flat list of requirements, pinned or ranged.

    An `==` pin here is *not* a lockfile pin. A requirements file is still a
    declaration — nothing has resolved it against an index — so it keeps
    `manifest_declared`, and only a real lockfile reaches certainty.
    """
    subject = workspace_purl("pypi", source.element or "project").to_string()
    observations: list[Observation] = []

    for number, raw in enumerate(source.text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(_DIRECTIVE):
            continue

        parsed = _REQUIREMENT.match(line)
        if not parsed or not parsed.group("name"):
            continue

        name = parsed.group("name")
        specifier = (parsed.group("spec") or "").strip()
        pinned = specifier.startswith("==")
        version = specifier[2:].strip() if pinned else ""

        evidence = evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=number, excerpt=line, digest=source.digest,
        )
        target = python_purl(name, version)
        if pinned:
            observations.append(declares(
                target.to_string(), evidence, node_kind="package", ecosystem="pypi",
            ))
        observations.append(depends(
            subject, target.to_string(), evidence,
            range=specifier, marker=(parsed.group("marker") or "").strip(),
            ecosystem="pypi", scope="requirements",
        ))

    return result(observations)


# --- lockfiles -----------------------------------------------------------


def discover_poetry_lock(source: Source) -> DiscoveryResult:
    """poetry.lock — resolved, and therefore a genuine pin."""
    try:
        document = tomllib.loads(source.text)
    except tomllib.TOMLDecodeError as error:
        return result((), errors=[f"{source.uri}: not valid TOML ({error})"])

    subject = workspace_purl("pypi", source.element or "project").to_string()
    observations: list[Observation] = []

    for entry in document.get("package") or ():
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name", ""))
        version = str(entry.get("version", ""))
        if not name or not version:
            continue

        line = source.line(f'name = "{name}"')
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.LOCKFILE_PIN, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )
        purl = python_purl(name, version).to_string()
        observations.append(declares(
            purl, evidence, node_kind="package", ecosystem="pypi",
            optional=bool(entry.get("optional", False)),
        ))
        observations.append(depends(
            subject, purl, evidence, version=version, ecosystem="pypi", resolved=True,
        ))

    return result(observations)


def discover_uv_lock(source: Source) -> DiscoveryResult:
    """uv.lock — the same shape as poetry.lock for our purposes."""
    return discover_poetry_lock(source)


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.python.pyproject", discover_pyproject,
        ("pyproject.toml",), ("depends_on", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.python.requirements", discover_requirements,
        ("requirements.txt", "requirements-*.txt", "requirements/*.txt"),
        ("depends_on", "declares"), (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.python.poetry-lock", discover_poetry_lock,
        ("poetry.lock",), ("depends_on", "declares"), (EvidenceKind.LOCKFILE_PIN,),
    ),
    (
        "slpie.python.uv-lock", discover_uv_lock,
        ("uv.lock",), ("depends_on", "declares"), (EvidenceKind.LOCKFILE_PIN,),
    ),
)
