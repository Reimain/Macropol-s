"""Gradle — build scripts read for their literals, and version catalogs read properly.

A `build.gradle` is a Groovy program and a `build.gradle.kts` is a Kotlin one.
Neither is a data file, and a regex over either reads only the parts that happen
to be literal: `implementation 'com.google.guava:guava:32.1.3-jre'` is legible,
while a coordinate assembled from a variable, a function call, or a loop is not.
Anything a plugin adds at configuration time is invisible here too, because it
does not exist until the build runs.

That is why this discoverer claims **`build_config`** evidence rather than
`manifest_declared`. A POM is a declaration — the file *is* the dependency list.
A build script merely contains one, in a language that could have computed it
differently, and the confidence the platform derives should say so. Getting the
truth requires running `gradle dependencies`, which discovery is not entitled to
do; until something does, these are literals read out of a program.

`gradle/libs.versions.toml` is the exception. A version catalog *is* a data file
— TOML, read with `tomllib` — so what it says is exactly what it means, though a
catalogued library is only an available coordinate until a build script asks for
it, which is recorded as such.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.version import parse_range
from ...errors import VersionError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import (
    Source,
    declares,
    depends,
    evidence_at,
    result,
    scope_of,
    workspace_purl,
)
from .maven import maven_purl

EXTRACTOR = "slpie.gradle"

#: Configurations a dependency can be declared in. The qualifier keeps them
#: apart the way Maven scopes do, and for the same reason: a testImplementation
#: dependency is on the test classpath, not absent.
CONFIGURATIONS = (
    "implementation", "api", "compileOnly", "compileOnlyApi", "runtimeOnly",
    "testImplementation", "testCompileOnly", "testRuntimeOnly",
    "androidTestImplementation", "debugImplementation", "releaseImplementation",
    "annotationProcessor", "kapt", "ksp", "classpath", "compile", "testCompile",
    "providedCompile", "providedRuntime", "developmentOnly", "runtimeClasspath",
)

_CONFIGURATION = "|".join(CONFIGURATIONS)

#: `implementation 'g:a:v'`, `api("g:a:v")`, `implementation platform('g:a:v')`.
_STRING_FORM = re.compile(
    r"^\s*(?P<configuration>" + _CONFIGURATION + r")\s*"
    r"(?:\(\s*)?"
    r"(?:(?P<wrapper>platform|enforcedPlatform|project|files|fileTree)\s*\(\s*)?"
    r"(?P<quote>['\"])(?P<coordinate>[^'\"]+)(?P=quote)",
    re.M,
)

#: `implementation group: 'g', name: 'a', version: 'v'` in any key order.
_MAP_FORM = re.compile(
    r"^\s*(?P<configuration>" + _CONFIGURATION + r")\s*\(?\s*"
    r"(?P<body>(?:\w+\s*[:=]\s*['\"][^'\"]*['\"]\s*,?\s*)+)",
    re.M,
)
_MAP_ENTRY = re.compile(r"(?P<key>\w+)\s*[:=]\s*['\"](?P<value>[^'\"]*)['\"]")

#: `implementation(libs.retrofit)` — a reference into the version catalog.
_CATALOG_REF = re.compile(
    r"^\s*(?P<configuration>" + _CONFIGURATION + r")\s*\(?\s*"
    r"(?P<alias>libs(?:\.[A-Za-z0-9_]+)+)",
    re.M,
)

#: A coordinate that is not a literal at all — `"$group:$name:$version"`.
_INTERPOLATED = re.compile(r"[$]\{?\w")


def _range_facts(spec: str) -> dict[str, Any]:
    """What the shared range parser makes of a Gradle version.

    Gradle accepts Maven interval notation as well as `1.+` dynamic versions,
    and the shared parser already knows the first; the second is recorded as
    unparsed rather than resolved to a guess.
    """
    if not spec:
        return {"range": "", "range_parsed": False, "range_exact": ""}
    try:
        parsed = parse_range(spec, ecosystem="gradle")
    except VersionError:
        return {"range": spec, "range_parsed": False, "range_exact": ""}
    exact = parsed.exact_version
    return {
        "range": spec, "range_parsed": True,
        "range_exact": exact.text if exact else "",
    }


def dependency_blocks(text: str) -> Iterator[tuple[int, str]]:
    """Every `dependencies { … }` block, with the offset it starts at.

    Braces are matched rather than counted to a fixed depth, because a real
    build script nests closures inside the block and a naive `}` search stops
    at the first one.
    """
    for match in re.finditer(r"\bdependencies\s*\{", text):
        start = match.end()
        depth = 1
        index = start
        while index < len(text) and depth:
            character = text[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            index += 1
        yield start, text[start:index - 1 if depth == 0 else len(text)]


def _strip_comments(text: str) -> str:
    """Blank out comments, preserving offsets so line numbers stay true."""
    def blank(match: re.Match[str]) -> str:
        return re.sub(r"\S", " ", match.group(0))

    return re.sub(r"//[^\n]*|/\*.*?\*/", blank, text, flags=re.S)


# --- build.gradle / build.gradle.kts -------------------------------------


def discover_build_gradle(source: Source) -> DiscoveryResult:
    """Literal coordinates inside the dependency blocks, and nothing invented."""
    text = _strip_comments(source.text)
    subject = workspace_purl("maven", scope_of(source.element, "project")).to_string()
    kotlin = source.name.endswith(".kts")

    line = source.line("dependencies")
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.BUILD_CONFIG, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package", ecosystem="maven",
            build_system="gradle",
            language="kotlin" if kotlin else "groovy",
        )
    ]

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    catalog_references: list[str] = []
    for offset, block in dependency_blocks(text):
        for match in _STRING_FORM.finditer(block):
            coordinate = match.group("coordinate")
            wrapper = match.group("wrapper") or ""
            if wrapper in ("project", "files", "fileTree"):
                continue    # a module or a jar on disk, not a published package
            if _INTERPOLATED.search(coordinate):
                errors.append(
                    f"{source.uri}: coordinate {coordinate!r} is interpolated at build "
                    f"time and cannot be read from the script"
                )
                continue
            group, artifact, version = _split_coordinate(coordinate)
            if not artifact:
                continue
            number = _line_at(source.text, offset + match.start())
            observations.append(_dependency(
                source, subject, group, artifact, version,
                configuration=match.group("configuration"), line=number,
                form="platform" if wrapper else "string",
            ))
            seen.add((group, artifact))

        for match in _MAP_FORM.finditer(block):
            entries = {
                entry.group("key"): entry.group("value")
                for entry in _MAP_ENTRY.finditer(match.group("body"))
            }
            group = entries.get("group", "")
            artifact = entries.get("name", "")
            if not artifact or (group, artifact) in seen:
                continue
            number = _line_at(source.text, offset + match.start())
            observations.append(_dependency(
                source, subject, group, artifact, entries.get("version", ""),
                configuration=match.group("configuration"), line=number, form="map",
            ))
            seen.add((group, artifact))

        for match in _CATALOG_REF.finditer(block):
            catalog_references.append(match.group("alias"))

    if catalog_references:
        # The alias names a catalog entry; `libs.versions.toml` says which
        # coordinate it is. Resolving it from here would be a guess, so the
        # references are recorded and the catalog discoverer reads the rest.
        first = source.line(catalog_references[0])
        observations.append(declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.BUILD_CONFIG, extractor=EXTRACTOR,
                line=first, excerpt=source.excerpt(first), digest=source.digest,
            ),
            node_kind="package", ecosystem="maven",
            catalog_references=tuple(dict.fromkeys(catalog_references)),
        ))

    return result(observations, errors=errors)


def _dependency(
    source: Source,
    subject: str,
    group: str,
    artifact: str,
    version: str,
    *,
    configuration: str,
    line: int,
    form: str,
) -> Observation:
    return depends(
        subject,
        maven_purl(group or "unknown", artifact).to_string(),
        evidence_at(
            source.uri, kind=EvidenceKind.BUILD_CONFIG, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        qualifier=configuration,
        configuration=configuration,
        form=form,
        # An omitted version means a BOM or the catalog supplies it.
        version_managed=not version,
        ecosystem="maven",
        **_range_facts(version),
    )


def _split_coordinate(coordinate: str) -> tuple[str, str, str]:
    """`group:artifact:version` — the version is optional, the rest is not."""
    parts = coordinate.split(":")
    if len(parts) == 1:
        return "", "", ""
    group, artifact = parts[0], parts[1]
    version = parts[2] if len(parts) > 2 else ""
    return group.strip(), artifact.strip(), version.strip()


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


# --- gradle/libs.versions.toml -------------------------------------------


def discover_version_catalog(source: Source) -> DiscoveryResult:
    """A version catalog is TOML, so it says exactly what it means.

    Still `build_config`: a catalogued library is a coordinate the build *may*
    use, and only a build script asking for the alias makes it a dependency.
    """
    try:
        document = tomllib.loads(source.text)
    except tomllib.TOMLDecodeError as error:
        return result((), errors=[f"{source.uri}: not valid TOML ({error})"])

    versions = {
        str(key): _catalog_version(value, {})
        for key, value in (document.get("versions") or {}).items()
    }
    subject = workspace_purl("maven", scope_of(source.element, "project")).to_string()
    observations: list[Observation] = []
    errors: list[str] = []

    for alias, entry in (document.get("libraries") or {}).items():
        group, artifact, version, problem = _catalog_entry(entry, versions)
        if problem:
            errors.append(f"{source.uri}: catalog entry {alias!r}: {problem}")
            continue
        line = source.line(f"{alias} = ") or source.line(str(alias))
        observations.append(depends(
            subject,
            maven_purl(group or "unknown", artifact).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.BUILD_CONFIG, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            qualifier="catalog",
            alias=str(alias), scope="version-catalog", ecosystem="maven",
            **_range_facts(version),
        ))

    for alias, entry in (document.get("plugins") or {}).items():
        identifier, version = _catalog_plugin(entry, versions)
        if not identifier:
            continue
        line = source.line(f"{alias} = ") or source.line(str(alias))
        observations.append(depends(
            subject,
            # A Gradle plugin is published as `<id>:<id>.gradle.plugin`, which
            # is the coordinate a resolver actually fetches.
            maven_purl(identifier, f"{identifier}.gradle.plugin").to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.BUILD_CONFIG, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            qualifier="plugin",
            alias=str(alias), scope="version-catalog", ecosystem="maven",
            **_range_facts(version),
        ))

    return result(observations, errors=errors)


def _catalog_entry(
    entry: Any, versions: Mapping[str, str]
) -> tuple[str, str, str, str]:
    """`module = "g:a"` plus `version.ref`, or `group`/`name`, or a bare string."""
    if isinstance(entry, str):
        group, artifact, version = _split_coordinate(entry)
        if not artifact:
            return "", "", "", f"{entry!r} is not a group:artifact:version coordinate"
        return group, artifact, version, ""

    if not isinstance(entry, Mapping):
        return "", "", "", "expected a string or a table"

    module = str(entry.get("module", ""))
    if module:
        group, _, artifact = module.partition(":")
    else:
        group, artifact = str(entry.get("group", "")), str(entry.get("name", ""))
    if not artifact:
        return "", "", "", "names no artifact"
    return group, artifact, _catalog_version(entry.get("version"), versions), ""


def _catalog_version(declared: Any, versions: Mapping[str, str]) -> str:
    """`version = "1.0"`, or `version.ref = "alias"` which TOML nests."""
    if isinstance(declared, Mapping):
        reference = str(declared.get("ref", ""))
        if reference:
            return versions.get(reference, "")
        return str(
            declared.get("require")
            or declared.get("strictly")
            or declared.get("prefer")
            or ""
        )
    return str(declared or "")


def _catalog_plugin(entry: Any, versions: Mapping[str, str]) -> tuple[str, str]:
    if isinstance(entry, str):
        identifier, _, version = entry.partition(":")
        return identifier, version
    if not isinstance(entry, Mapping):
        return "", ""
    return str(entry.get("id", "")), _catalog_version(entry.get("version"), versions)


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.gradle.build", discover_build_gradle,
        ("build.gradle", "build.gradle.kts"),
        ("depends_on", "declares"), (EvidenceKind.BUILD_CONFIG,),
    ),
    (
        "slpie.gradle.catalog", discover_version_catalog,
        ("libs.versions.toml", "gradle/*.versions.toml"),
        ("depends_on", "declares"), (EvidenceKind.BUILD_CONFIG,),
    ),
)
