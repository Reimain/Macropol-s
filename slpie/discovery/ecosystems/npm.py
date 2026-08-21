"""npm, pnpm and yarn — manifests and the lockfiles that resolve them.

A manifest declares a *range*; a lockfile records what that range actually
resolved to. They are different facts and they get different evidence kinds:
`manifest_declared` at 0.95 and `lockfile_pin` at 1.00, which is the only route
to certainty in the whole platform. That distinction is why `^4.17.20` in a
package.json and `4.17.21` in a package-lock.json produce two observations about
the same dependency rather than one confused average.

Three lockfile formats are read here because all three are in the wild:
package-lock v1/v2/v3, yarn.lock v1 (a bespoke format) and yarn berry / pnpm
(YAML-ish). Each is parsed for what it uniquely knows.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, workspace_purl

EXTRACTOR = "slpie.npm"


def npm_purl(name: str, version: str = "") -> Purl:
    """A scoped name becomes a namespace, which is what purl requires.

    `@types/node` is namespace `@types`, name `node` — not a single name with a
    slash in it. Getting this wrong makes every scoped package collide with a
    differently-scoped one of the same name.
    """
    if name.startswith("@") and "/" in name:
        namespace, _, bare = name.partition("/")
        return Purl.create("npm", bare, namespace=namespace, version=version)
    return Purl.create("npm", name, version=version)


# --- package.json --------------------------------------------------------

DEPENDENCY_SECTIONS = {
    "dependencies": "",
    "devDependencies": "dev",
    "optionalDependencies": "optional",
    "peerDependencies": "peer",
}


def discover_package_json(source: Source) -> DiscoveryResult:
    """Read declared ranges, and the workspace's own identity."""
    try:
        document = json.loads(source.text)
    except ValueError as error:
        return result((), errors=[f"{source.uri}: not valid JSON ({error})"])
    if not isinstance(document, dict):
        return result((), errors=[f"{source.uri}: expected an object"])

    name = str(document.get("name") or source.element or "workspace")
    version = str(document.get("version", ""))
    subject = workspace_purl("npm", name, version).to_string()

    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=source.line(f'"name"'), excerpt=f'"name": "{name}"',
                digest=source.digest,
            ),
            node_kind="package",
            license=str(document.get("license", "")),
            private=bool(document.get("private", False)),
        )
    ]

    for section, qualifier in DEPENDENCY_SECTIONS.items():
        entries = document.get(section)
        if not isinstance(entries, dict):
            continue
        for dependency, declared_range in entries.items():
            line = source.line(f'"{dependency}"')
            observations.append(depends(
                subject,
                npm_purl(str(dependency)).to_string(),
                evidence_at(
                    source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                    line=line, excerpt=source.excerpt(line), digest=source.digest,
                ),
                qualifier=qualifier,
                # The range, not a version: this is intent, and the lockfile
                # observation carries what it actually resolved to.
                range=str(declared_range),
                ecosystem="npm",
                scope=section,
            ))

    for engine, constraint in (document.get("engines") or {}).items():
        line = source.line(f'"{engine}"')
        observations.append(depends(
            subject,
            Purl.create("generic", str(engine)).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line),
            ),
            qualifier="runtime",
            range=str(constraint),
            ecosystem="generic",
        ))

    return result(observations)


# --- package-lock.json ---------------------------------------------------


def discover_package_lock(source: Source) -> DiscoveryResult:
    """Read resolved pins. The only evidence kind that reaches certainty."""
    try:
        document = json.loads(source.text)
    except ValueError as error:
        return result((), errors=[f"{source.uri}: not valid JSON ({error})"])

    version = int(document.get("lockfileVersion", 1) or 1)
    root_name = str(document.get("name") or source.element or "workspace")
    root = workspace_purl("npm", root_name, str(document.get("version", ""))).to_string()

    if version >= 2 and isinstance(document.get("packages"), dict):
        pins = _lock_v3(document["packages"])
    elif isinstance(document.get("dependencies"), dict):
        pins = _lock_v1(document["dependencies"])
    else:
        return result((), errors=[f"{source.uri}: no recognisable lockfile structure"])

    observations: list[Observation] = []
    for name, pinned, metadata in pins:
        line = source.line(f'"{name}"')
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.LOCKFILE_PIN, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )
        purl = npm_purl(name, pinned).to_string()
        observations.append(declares(
            purl, evidence, node_kind="package",
            license=metadata.get("license", ""),
            integrity=metadata.get("integrity", ""),
            resolved=metadata.get("resolved", ""),
            ecosystem="npm",
        ))
        observations.append(depends(
            root, purl, evidence,
            qualifier="dev" if metadata.get("dev") else "",
            version=pinned, ecosystem="npm", resolved=True,
        ))

    return result(observations)


def _lock_v3(packages: Mapping[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """lockfileVersion 2 and 3: a flat map keyed by install path."""
    for path, entry in packages.items():
        if not path or not isinstance(entry, dict):
            continue    # "" is the root project, described by package.json
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        version = str(entry.get("version", ""))
        if not version:
            continue
        yield str(name), version, dict(entry)


def _lock_v1(dependencies: Mapping[str, Any], *, depth: int = 0) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """lockfileVersion 1: nested, so the walk recurses."""
    if depth > 24:      # a pathological tree must not exhaust the stack
        return
    for name, entry in dependencies.items():
        if not isinstance(entry, dict):
            continue
        version = str(entry.get("version", ""))
        if version:
            yield str(name), version, dict(entry)
        nested = entry.get("dependencies")
        if isinstance(nested, dict):
            yield from _lock_v1(nested, depth=depth + 1)


# --- yarn.lock -----------------------------------------------------------

_YARN_ENTRY = re.compile(r'^"?([^"\n]+?)"?:\s*$', re.M)
_YARN_VERSION = re.compile(r'^\s+version:?\s+"?([^"\s]+)"?\s*$', re.M)
_YARN_RESOLUTION = re.compile(r'^\s+resolution:\s+"([^"]+)"\s*$', re.M)


def discover_yarn_lock(source: Source) -> DiscoveryResult:
    """Yarn v1's bespoke format and berry's YAML, both read for pins."""
    root = workspace_purl("npm", source.element or "workspace").to_string()
    observations: list[Observation] = []
    seen: set[str] = set()

    blocks = source.text.split("\n\n")
    for block in blocks:
        if not block.strip() or block.lstrip().startswith("#"):
            continue

        version_match = _YARN_VERSION.search(block)
        if not version_match:
            continue
        version = version_match.group(1)

        resolution = _YARN_RESOLUTION.search(block)
        if resolution:
            name = _name_from_resolution(resolution.group(1))
        else:
            header = block.strip().splitlines()[0]
            name = _name_from_header(header)
        if not name:
            continue

        purl = npm_purl(name, version).to_string()
        if purl in seen:
            continue
        seen.add(purl)

        line = source.line(version_match.group(0).strip())
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.LOCKFILE_PIN, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )
        observations.append(declares(purl, evidence, node_kind="package", ecosystem="npm"))
        observations.append(depends(
            root, purl, evidence, version=version, ecosystem="npm", resolved=True,
        ))

    return result(observations)


def _name_from_header(header: str) -> str:
    """`"lodash@^4.17.20":` and `lodash@^4.17.20, lodash@^4.0.0:` both name lodash."""
    first = header.split(",")[0].strip().strip('":')
    if first.startswith("@"):
        scope, _, rest = first[1:].partition("/")
        bare = rest.split("@")[0]
        return f"@{scope}/{bare}" if bare else ""
    return first.split("@")[0]


def _name_from_resolution(resolution: str) -> str:
    """Berry writes `lodash@npm:4.17.21`."""
    return _name_from_header(resolution)


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.npm.manifest", discover_package_json,
        ("package.json",), ("depends_on", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.npm.lockfile", discover_package_lock,
        ("package-lock.json", "npm-shrinkwrap.json"), ("depends_on", "declares"),
        (EvidenceKind.LOCKFILE_PIN,),
    ),
    (
        "slpie.yarn.lockfile", discover_yarn_lock,
        ("yarn.lock",), ("depends_on", "declares"),
        (EvidenceKind.LOCKFILE_PIN,),
    ),
)
