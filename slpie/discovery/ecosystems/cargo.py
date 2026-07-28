"""Cargo — `Cargo.toml` declares, `Cargo.lock` resolves.

Both files are TOML, so both are read with `tomllib` from the standard library
and neither needs a parser of its own. The split is the same one npm makes and
for the same reason: a manifest says `serde = "1.0"`, a lockfile says
`1.0.197`, and those are two different facts about one crate rather than one
fact of middling accuracy. The manifest gets `manifest_declared`, the lockfile
gets `lockfile_pin`.

Three details of the format are easy to get wrong and are handled explicitly:

* A dependency may be renamed — ``foo = { package = "real-crate" }`` — and it is
  the ``package`` key that names the crate, not the table key.
* Dependencies also live under ``[target.'cfg(unix)'.dependencies]`` and
  ``[workspace.dependencies]``; only reading the top-level tables misses them.
* `Cargo.lock` v3 and v4 differ in how they spell the ``source`` field, not in
  their structure, so one reader serves both — and the lockfile records the
  edges *between* crates, which are worth keeping.
"""

from __future__ import annotations

import tomllib
from typing import Any, Mapping

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...domain.version import parse_range
from ...errors import VersionError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, workspace_purl

EXTRACTOR = "slpie.cargo"

#: Manifest tables that hold dependencies, and the qualifier each implies.
DEPENDENCY_TABLES = {
    "dependencies": "",
    "dev-dependencies": "dev",
    "build-dependencies": "build",
}


def cargo_purl(name: str, version: str = "") -> Purl:
    """crates.io has a flat namespace, so a crate is just a name."""
    return Purl.create("cargo", name, version=version)


def _range_facts(spec: str) -> dict[str, Any]:
    """What the shared range parser makes of a Cargo requirement.

    Reported, not judged: an unparseable requirement is recorded as
    unparseable rather than quietly widened to "any version".
    """
    try:
        parsed = parse_range(spec, ecosystem="cargo")
    except VersionError:
        return {"range": spec, "range_parsed": False, "range_exact": ""}
    exact = parsed.exact_version
    return {
        "range": spec,
        "range_parsed": True,
        "range_exact": exact.text if exact else "",
    }


# --- Cargo.toml ----------------------------------------------------------


def discover_cargo_toml(source: Source) -> DiscoveryResult:
    """Declared requirements, and the crate this manifest itself describes."""
    try:
        document = tomllib.loads(source.text)
    except tomllib.TOMLDecodeError as error:
        return result((), errors=[f"{source.uri}: not valid TOML ({error})"])

    package = document.get("package") or {}
    name = str(package.get("name") or source.element or "crate")
    version = _inherited(package.get("version"))
    subject = workspace_purl("cargo", name, version).to_string()

    line = source.line(f'name = "{name}"') or source.line("[package]")
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package",
            license=str(_inherited(package.get("license"))),
            edition=str(_inherited(package.get("edition"))),
            rust_version=str(_inherited(package.get("rust-version"))),
            ecosystem="cargo",
        )
    ]

    for table, qualifier in DEPENDENCY_TABLES.items():
        observations.extend(_table(
            source, subject, document.get(table), qualifier=qualifier, scope=table,
        ))

    # `[target.'cfg(unix)'.dependencies]` — conditional, but still declared.
    for target, body in (document.get("target") or {}).items():
        if not isinstance(body, Mapping):
            continue
        for table, qualifier in DEPENDENCY_TABLES.items():
            observations.extend(_table(
                source, subject, body.get(table),
                qualifier=qualifier, scope=table, cfg_target=str(target),
            ))

    # A workspace root declares versions its members inherit.
    workspace = document.get("workspace") or {}
    if isinstance(workspace, Mapping):
        observations.extend(_table(
            source, subject, workspace.get("dependencies"),
            qualifier="workspace", scope="workspace.dependencies",
        ))

    return result(observations)


def _inherited(value: Any) -> str:
    """`version = { workspace = true }` inherits — there is nothing to report."""
    if isinstance(value, Mapping):
        return ""
    return str(value or "")


def _table(
    source: Source,
    subject: str,
    table: Any,
    *,
    qualifier: str,
    scope: str,
    cfg_target: str = "",
) -> list[Observation]:
    if not isinstance(table, Mapping):
        return []

    observations: list[Observation] = []
    for key, constraint in table.items():
        crate, facts = _dependency(str(key), constraint)
        line = source.line(f"{key} = ") or source.line(str(key))
        observations.append(depends(
            subject,
            cargo_purl(crate).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            qualifier=qualifier,
            scope=scope,
            cfg_target=cfg_target,
            ecosystem="cargo",
            **facts,
        ))
    return observations


def _dependency(key: str, constraint: Any) -> tuple[str, dict[str, Any]]:
    """The crate a table entry names, and everything it says about it.

    A renamed dependency (`fast-json = { package = "simd-json" }`) is the crate
    named by ``package``; using the key would invent a crate that does not
    exist.
    """
    if not isinstance(constraint, Mapping):
        facts = _range_facts(str(constraint))
        return key, {**facts, "optional": False, "features": (), "renamed_from": ""}

    crate = str(constraint.get("package") or key)
    declared = constraint.get("version")
    facts = _range_facts(str(declared)) if declared is not None else {
        "range": "", "range_parsed": False, "range_exact": "",
    }
    return crate, {
        **facts,
        "optional": bool(constraint.get("optional", False)),
        "features": tuple(str(f) for f in constraint.get("features") or ()),
        "default_features": bool(constraint.get("default-features", True)),
        "path": str(constraint.get("path", "")),
        "git": str(constraint.get("git", "")),
        "renamed_from": key if crate != key else "",
    }


# --- Cargo.lock ----------------------------------------------------------


def discover_cargo_lock(source: Source) -> DiscoveryResult:
    """Resolved crates, and the edges the resolver recorded between them.

    v3 and v4 are the same shape; the reader does not need to know which it is
    holding, so it does not ask.
    """
    try:
        document = tomllib.loads(source.text)
    except tomllib.TOMLDecodeError as error:
        return result((), errors=[f"{source.uri}: not valid TOML ({error})"])

    lock_version = str(document.get("version", ""))
    root = workspace_purl("cargo", source.element or "workspace").to_string()
    observations: list[Observation] = []
    errors: list[str] = []

    entries = document.get("package")
    if not isinstance(entries, list):
        return result((), errors=[f"{source.uri}: no [[package]] entries to read"])

    pinned: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name", ""))
        version = str(entry.get("version", ""))
        if not name or not version:
            errors.append(f"{source.uri}: a [[package]] entry has no name and version")
            continue
        pinned[name] = version

    for entry in entries:
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
        purl = cargo_purl(name, version).to_string()
        # No `source` means the crate is a member of this workspace rather than
        # something fetched from a registry.
        registry = str(entry.get("source", ""))
        observations.append(declares(
            purl, evidence, node_kind="package", ecosystem="cargo",
            checksum=str(entry.get("checksum", "")),
            source=registry,
            local=not registry,
            lockfile_version=lock_version,
        ))
        observations.append(depends(
            root, purl, evidence, version=version, ecosystem="cargo", resolved=True,
        ))

        for dependency in entry.get("dependencies") or ():
            dependency_name, dependency_version = _lock_dependency(str(dependency), pinned)
            if not dependency_name:
                continue
            observations.append(depends(
                purl,
                cargo_purl(dependency_name, dependency_version).to_string(),
                evidence,
                version=dependency_version, ecosystem="cargo", resolved=True,
            ))

    return result(observations, errors=errors)


def _lock_dependency(entry: str, pinned: Mapping[str, str]) -> tuple[str, str]:
    """`"memchr"`, `"memchr 2.6.4"` and `"memchr 2.6.4 (registry+…)"` all name memchr.

    Older lockfiles spell out the version and registry; newer ones omit both
    when the name is unambiguous, in which case the pin is the one this same
    lockfile already recorded for that crate.
    """
    parts = entry.split()
    if not parts:
        return "", ""
    name = parts[0]
    version = parts[1] if len(parts) > 1 and not parts[1].startswith("(") else ""
    return name, version or pinned.get(name, "")


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.cargo.manifest", discover_cargo_toml,
        ("Cargo.toml",), ("depends_on", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.cargo.lockfile", discover_cargo_lock,
        ("Cargo.lock",), ("depends_on", "declares"),
        (EvidenceKind.LOCKFILE_PIN,),
    ),
)
