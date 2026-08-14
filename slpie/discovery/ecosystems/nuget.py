"""NuGet — MSBuild project files declare, `packages.lock.json` resolves.

A `.csproj` is MSBuild XML, and it comes in two shapes that are twenty years
apart. SDK-style projects have no XML namespace at all; legacy ones sit in
`http://schemas.microsoft.com/developer/msbuild/2003`. Both are in the wild in
the same repository, so the reading here is namespace-blind — the same helpers
the POM reader uses, for the same reason.

`<PackageReference>` also has two spellings, and both are common enough that
supporting only one loses real dependencies:

    <PackageReference Include="Serilog" Version="3.1.1" />
    <PackageReference Include="Serilog"><Version>3.1.1</Version></PackageReference>

A reference with no version at all is not malformed either: under Central
Package Management the version lives in `Directory.Packages.props` as a
`<PackageVersion>`, which this reader also understands.

NuGet package ids preserve their case — `Newtonsoft.Json` is not
`newtonsoft.json` — and purl does not normalise them, so nothing here does.
"""

from __future__ import annotations

import json
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...domain.version import parse_range
from ...errors import VersionError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, workspace_purl
from .maven import local, parse_xml, text_of

EXTRACTOR = "slpie.nuget"

#: Item types that name a package rather than a file or a sibling project.
PACKAGE_ITEMS = ("PackageReference", "PackageVersion", "GlobalPackageReference")


def nuget_purl(name: str, version: str = "") -> Purl:
    """A flat, case-preserving namespace."""
    return Purl.create("nuget", name, version=version)


def _range_facts(spec: str) -> dict[str, Any]:
    """What the shared range parser makes of a NuGet version range.

    NuGet's interval notation — `[1.0,2.0)`, `[13.0.3]` — is the same grammar
    Maven uses, so the shared parser is asked for it under that dialect rather
    than being taught a second one here. The raw text is always reported
    alongside; ``range_exact`` says only what the parser could pin down.
    """
    if not spec:
        return {"range": "", "range_parsed": False, "range_exact": ""}
    dialect = "maven" if spec.startswith(("[", "(")) else "nuget"
    try:
        parsed = parse_range(spec, ecosystem=dialect)
    except VersionError:
        return {"range": spec, "range_parsed": False, "range_exact": ""}
    exact = parsed.exact_version
    return {
        "range": spec, "range_parsed": True,
        "range_exact": exact.text if exact else "",
    }


# --- *.csproj / *.fsproj / *.vbproj --------------------------------------


def discover_project_file(source: Source) -> DiscoveryResult:
    """Every package a project file references, in either spelling."""
    root, salvaged, error = parse_xml(source.text)
    errors = [f"{source.uri}: malformed XML ({error})"] if error else []

    name = source.name.rsplit(".", 1)[0] or source.element or "project"
    subject = workspace_purl("nuget", name).to_string()

    if root is None:
        return result(
            _references(source, subject, salvaged, salvaged=True), errors=errors,
        )

    line = source.line("<Project") or 1
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package", ecosystem="nuget",
            sdk=root.get("Sdk", ""),
            target_frameworks=_frameworks(root),
        )
    ]
    observations.extend(_references(source, subject, tuple(root.iter()), salvaged=False))
    return result(observations, errors=errors)


def _frameworks(root: ET.Element) -> tuple[str, ...]:
    """`<TargetFramework>` or `<TargetFrameworks>`, singular and plural both."""
    found: list[str] = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = local(node.tag)
        if tag in ("TargetFramework", "TargetFrameworks") and node.text:
            found.extend(part.strip() for part in node.text.split(";") if part.strip())
    return tuple(dict.fromkeys(found))


def _references(
    source: Source,
    subject: str,
    elements: tuple[ET.Element, ...],
    *,
    salvaged: bool,
) -> list[Observation]:
    observations: list[Observation] = []
    seen: set[tuple[str, str]] = set()

    for element in elements:
        if not isinstance(element.tag, str):
            continue
        item = local(element.tag)
        if item not in PACKAGE_ITEMS:
            continue

        package = element.get("Include") or element.get("Update") or ""
        if not package:
            continue
        # The attribute form and the child-element form say the same thing.
        version = element.get("Version") or text_of(element, "Version")
        key = (item, package)
        if key in seen:
            continue
        seen.add(key)

        line = source.line(f'"{package}"') or source.line(package)
        observations.append(depends(
            subject,
            nuget_purl(package).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            # A `<PackageVersion>` sets a version centrally without asking for
            # the package; that is a different claim and gets its own qualifier.
            qualifier="central" if item == "PackageVersion" else (
                "global" if item == "GlobalPackageReference" else ""
            ),
            item_type=item,
            # No version here means Central Package Management supplies it.
            version_managed=not version,
            private_assets=element.get("PrivateAssets") or text_of(element, "PrivateAssets"),
            include_assets=element.get("IncludeAssets") or text_of(element, "IncludeAssets"),
            condition=element.get("Condition", ""),
            salvaged=salvaged,
            ecosystem="nuget",
            **_range_facts(version),
        ))

    return observations


# --- packages.lock.json --------------------------------------------------


def discover_packages_lock(source: Source) -> DiscoveryResult:
    """Resolved versions, per target framework — a genuine pin."""
    try:
        document = json.loads(source.text)
    except ValueError as error:
        return result((), errors=[f"{source.uri}: not valid JSON ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected an object"])

    frameworks = document.get("dependencies")
    if not isinstance(frameworks, dict):
        return result((), errors=[f"{source.uri}: no dependencies section to read"])

    root = workspace_purl("nuget", source.element or "project").to_string()
    observations: list[Observation] = []

    for framework, entries in frameworks.items():
        if not isinstance(entries, Mapping):
            continue
        for package, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            resolved = str(entry.get("resolved", ""))
            kind = str(entry.get("type", ""))
            if not resolved:
                # A `Project` entry is a sibling project, not a fetched package.
                continue

            line = source.line(f'"{package}"')
            evidence = evidence_at(
                source.uri, kind=EvidenceKind.LOCKFILE_PIN, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            )
            purl = nuget_purl(str(package), resolved).to_string()
            observations.append(declares(
                purl, evidence, node_kind="package", ecosystem="nuget",
                content_hash=str(entry.get("contentHash", "")),
                framework=str(framework),
            ))
            observations.append(depends(
                root, purl, evidence,
                qualifier="transitive" if kind.lower() == "transitive" else "",
                version=resolved, framework=str(framework),
                requested=str(entry.get("requested", "")),
                ecosystem="nuget", resolved=True,
            ))

    return result(observations)


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.nuget.project", discover_project_file,
        ("*.csproj", "*.fsproj", "*.vbproj", "Directory.Packages.props"),
        ("depends_on", "declares"), (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.nuget.lockfile", discover_packages_lock,
        ("packages.lock.json",), ("depends_on", "declares"),
        (EvidenceKind.LOCKFILE_PIN,),
    ),
)
