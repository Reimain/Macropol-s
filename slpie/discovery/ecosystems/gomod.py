"""Go modules — `go.mod` declares, `go.sum` records what was fetched.

`go.mod` has a small line-oriented grammar rather than a data format, so it is
read line by line. That is deliberate: the alternative is shelling out to `go
mod edit -json`, which needs a Go toolchain the platform is not entitled to
assume, and which would make discovery fail on a machine that merely holds the
source.

Four directives carry meaning and all four are read:

* ``require`` — in a block or on a single line, with ``// indirect`` marking a
  transitive requirement rather than one this module asked for.
* ``replace`` — the most important one to get right. A replaced module is *not*
  what gets built, so reporting only the requirement would name a dependency
  the binary does not contain. Both sides are reported, related.
* ``exclude`` — a version explicitly ruled out, which is a fact about the
  module even though it creates no dependency.
* ``module`` and ``go`` — the module's own identity and its language version.

Go module paths keep their slashes: ``github.com/spf13/cobra`` is namespace
``github.com/spf13`` and name ``cobra``, which is what the purl specification
says and what makes the identifier round-trip.
"""

from __future__ import annotations

import re
from typing import Any

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...domain.version import parse_range
from ...errors import VersionError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import Source, declares, depends, evidence_at, result, workspace_purl

EXTRACTOR = "slpie.gomod"

#: Directives that open a parenthesised block, and take one entry per line.
BLOCK_DIRECTIVES = ("require", "replace", "exclude", "retract")

_INDIRECT = re.compile(r"//\s*indirect\b")
#: A go.sum line: `module version[/go.mod] h1:hash`.
_SUM_LINE = re.compile(r"^(?P<module>\S+)\s+(?P<version>\S+?)(?P<suffix>/go\.mod)?\s+(?P<hash>\S+)\s*$")


def gomod_purl(module_path: str, version: str = "") -> Purl:
    """`github.com/spf13/cobra` keeps its slashes, split namespace from name.

    Putting the whole path in the name would percent-encode every separator and
    produce an identifier no advisory database would match.
    """
    cleaned = module_path.strip().strip("/")
    namespace, _, name = cleaned.rpartition("/")
    return Purl.create("golang", name or cleaned, namespace=namespace, version=version)


def _version_facts(spec: str) -> dict[str, Any]:
    """What the shared range parser makes of a Go version.

    Go's minimum version selection means a `require` is a floor rather than a
    pin, so the raw text is what gets reported; the parse only records whether
    the text was a version at all.
    """
    try:
        parse_range(spec, ecosystem="gomod")
    except VersionError:
        return {"range": spec, "range_parsed": False}
    return {"range": spec, "range_parsed": True}


def _strip_comment(line: str) -> str:
    head, _, _ = line.partition("//")
    return head.strip()


# --- go.mod --------------------------------------------------------------


def discover_go_mod(source: Source) -> DiscoveryResult:
    """Read the module's own path and every directive that names another one."""
    module_path = ""
    go_version = ""
    requires: list[tuple[int, str, str, bool]] = []
    replaces: list[tuple[int, str, str, str, str]] = []
    excludes: list[tuple[int, str, str]] = []
    errors: list[str] = []

    block = ""
    for number, raw in enumerate(source.text.splitlines(), start=1):
        indirect = bool(_INDIRECT.search(raw))
        line = _strip_comment(raw)
        if not line:
            continue

        if block:
            if line.startswith(")"):
                block = ""
                continue
            _collect(
                _entry(block, line, number, indirect, errors, source),
                requires, replaces, excludes,
            )
            continue

        parts = line.split(None, 1)
        head = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""

        if head == "module":
            module_path = rest.strip('"')
            continue
        if head == "go":
            go_version = rest
            continue
        if head == "toolchain":
            continue
        if head in BLOCK_DIRECTIVES:
            if rest == "(":
                block = head
                continue
            _collect(
                _entry(head, rest, number, indirect, errors, source),
                requires, replaces, excludes,
            )
            continue

        # An unrecognised directive is not a reason to stop reading the rest of
        # a file that is otherwise perfectly readable.
        errors.append(f"{source.uri}:{number}: unrecognised directive {head!r}")

    if not module_path:
        errors.append(f"{source.uri}: no module directive")
    subject = (
        gomod_purl(module_path).to_string() if module_path
        else workspace_purl("golang", source.element or "module").to_string()
    )

    line = source.line("module ")
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package", ecosystem="golang", go_version=go_version,
        )
    ]

    replaced = {path for _, path, _, _, _ in replaces}
    for number, path, version, indirect in requires:
        observations.append(depends(
            subject, gomod_purl(path).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=number, excerpt=source.excerpt(number), digest=source.digest,
            ),
            qualifier="indirect" if indirect else "",
            version=version, ecosystem="golang", scope="require",
            # A replaced requirement is not what gets built. Saying so here is
            # the difference between a graph that describes the binary and one
            # that describes the wish.
            replaced=path in replaced,
            **_version_facts(version),
        ))

    for number, path, _from_version, target, target_version in replaces:
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=number, excerpt=source.excerpt(number), digest=source.digest,
        )
        local = target.startswith((".", "/"))
        observations.append(depends(
            subject,
            gomod_purl(target, target_version).to_string() if not local
            else gomod_purl(path).to_string(),
            evidence,
            qualifier="replace",
            replaces=path, version=target_version, local_path=target if local else "",
            ecosystem="golang", scope="replace",
        ))

    for number, path, version in excludes:
        observations.append(depends(
            subject, gomod_purl(path).to_string(),
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=number, excerpt=source.excerpt(number), digest=source.digest,
            ),
            qualifier="exclude",
            version=version, ecosystem="golang", scope="exclude", excluded=True,
        ))

    return result(observations, errors=errors)


def _collect(
    parsed: tuple[str, tuple[Any, ...]] | None,
    requires: list[tuple[int, str, str, bool]],
    replaces: list[tuple[int, str, str, str, str]],
    excludes: list[tuple[int, str, str]],
) -> None:
    if parsed is None:
        return
    directive, payload = parsed
    if directive == "require":
        requires.append(payload)      # type: ignore[arg-type]
    elif directive == "replace":
        replaces.append(payload)      # type: ignore[arg-type]
    elif directive == "exclude":
        excludes.append(payload)      # type: ignore[arg-type]


def _entry(
    directive: str,
    entry: str,
    number: int,
    indirect: bool,
    errors: list[str],
    source: Source,
) -> tuple[str, tuple[Any, ...]] | None:
    """One line inside (or standing in for) a directive block."""
    if not entry:
        return None

    if directive == "require":
        parts = entry.split()
        if len(parts) < 2:
            errors.append(f"{source.uri}:{number}: require without a version: {entry!r}")
            return None
        return "require", (number, parts[0].strip('"'), parts[1].strip('"'), indirect)

    if directive == "replace":
        left, arrow, right = entry.partition("=>")
        if not arrow:
            errors.append(f"{source.uri}:{number}: replace without '=>': {entry!r}")
            return None
        left_parts, right_parts = left.split(), right.split()
        if not left_parts or not right_parts:
            errors.append(f"{source.uri}:{number}: replace names nothing: {entry!r}")
            return None
        return "replace", (
            number,
            left_parts[0].strip('"'),
            left_parts[1].strip('"') if len(left_parts) > 1 else "",
            right_parts[0].strip('"'),
            right_parts[1].strip('"') if len(right_parts) > 1 else "",
        )

    if directive == "exclude":
        parts = entry.split()
        if len(parts) < 2:
            errors.append(f"{source.uri}:{number}: exclude without a version: {entry!r}")
            return None
        return "exclude", (number, parts[0].strip('"'), parts[1].strip('"'))

    return None    # `retract` is about this module's own releases, not a dependency


# --- go.sum --------------------------------------------------------------


def discover_go_sum(source: Source) -> DiscoveryResult:
    """The hashes of every module version the build actually fetched.

    Two lines exist per module — one for the zip and one for its `go.mod` — and
    a module that appears only with the `/go.mod` suffix was consulted for its
    requirements without its code being used. That distinction is recorded
    rather than flattened.
    """
    root = workspace_purl("golang", source.element or "module").to_string()
    seen: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for number, raw in enumerate(source.text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _SUM_LINE.match(line)
        if not match:
            errors.append(f"{source.uri}:{number}: not a go.sum line: {line[:80]!r}")
            continue

        path = match.group("module")
        version = match.group("version")
        key = f"{path}@{version}"
        entry = seen.setdefault(key, {
            "path": path, "version": version, "line": number,
            "go_mod_only": True, "hash": "",
        })
        if match.group("suffix"):
            continue
        entry["go_mod_only"] = False
        entry["hash"] = match.group("hash")
        entry["line"] = number

    observations: list[Observation] = []
    for entry in seen.values():
        line = int(entry["line"])
        evidence = evidence_at(
            source.uri, kind=EvidenceKind.LOCKFILE_PIN, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        )
        purl = gomod_purl(str(entry["path"]), str(entry["version"])).to_string()
        observations.append(declares(
            purl, evidence, node_kind="package", ecosystem="golang",
            hash=entry["hash"], go_mod_only=entry["go_mod_only"],
        ))
        observations.append(depends(
            root, purl, evidence, version=entry["version"], ecosystem="golang",
            resolved=True, go_mod_only=entry["go_mod_only"],
        ))

    return result(observations, errors=errors)


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.gomod.manifest", discover_go_mod,
        ("go.mod",), ("depends_on", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
    (
        "slpie.gomod.lockfile", discover_go_sum,
        ("go.sum",), ("depends_on", "declares"),
        (EvidenceKind.LOCKFILE_PIN,),
    ),
)
