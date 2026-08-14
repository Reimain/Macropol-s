"""Native coordinates ↔ purl — the cross-walk convergence actually needs.

Every ecosystem names a package in its own notation, and discoverers read those
notations directly:

    com.acme:payments:1.0.0          Maven
    github.com/acme/payments v1.2.3  Go
    Newtonsoft.Json 13.0.3           NuGet
    PyYAML==6.0                      Python
    @scope/thing@1.2.3               npm
    serde 1.0.195                    Cargo

`purl.canonical_purl` normalises a purl once you *have* one. This module is the
step before: turning what a file actually says into one. Without it every
discoverer invents its own parsing, and they disagree on exactly the cases that
matter — a scoped npm name containing `@`, a Go path containing slashes, a Maven
coordinate whose third segment may be a version or a packaging type.

Two directions, and both are needed:

* `to_purl` — what a discoverer calls. A Maven POM and a Gradle build script
  spell the same dependency differently; both arrive here and leave identical.
* `to_native` — what a *report* calls, because telling a Go developer their
  problem is `pkg:golang/github.com/x/y@v1.2.3` when `go get` wants
  `github.com/x/y@v1.2.3` makes them do the translation themselves.

The round trip is asserted, not assumed: `to_native(to_purl(x))` must produce a
coordinate that parses back to the same purl. An identity that survives one
direction and not the other is not an identity.

**Ambiguity is refused rather than guessed.** `com.acme:payments:jar:1.0.0` has
a packaging type in the third position and `com.acme:payments:1.0.0` does not;
a parser that assumes one shape silently mislabels the other. Where the shape
cannot be determined, this raises and says why.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..domain.identity import InvalidPurl, Purl
from ..errors import IdentityError
from .purl import canonical_ecosystem, canonical_purl, split_name
from .versions import normalize_version

#: Maven packaging types that can appear in the third position of a coordinate,
#: which is what makes `group:artifact:X` ambiguous.
PACKAGING = frozenset({
    "jar", "war", "ear", "pom", "aar", "bundle", "zip", "tar.gz", "test-jar",
    "maven-plugin", "rar", "ejb",
})

#: Separators that mean "and here is the version", per ecosystem.
_PYTHON_SPECIFIER = re.compile(r"(===|==|~=|!=|>=|<=|>|<)")


class CoordinateError(IdentityError):
    """A native coordinate could not be read, and guessing would be worse."""


def to_purl(ecosystem: str, coordinate: str, *, version: str = "") -> Purl:
    """One ecosystem's own notation → a canonical purl.

    `version` overrides anything embedded in the coordinate. Discoverers often
    have the version separately — a `<version>` element, a lockfile column —
    and it is more trustworthy than re-parsing it out of a string.
    """
    kind = canonical_ecosystem(ecosystem)
    text = coordinate.strip()
    if not text:
        raise CoordinateError("an empty coordinate names nothing")

    name, embedded = _split_version(kind, text)
    resolved = version.strip() or embedded

    namespace, bare = split_name(kind, name)
    if not bare:
        raise CoordinateError(
            f"{coordinate!r} has no package name once its {kind} namespace is removed"
        )

    return canonical_purl(Purl.create(
        kind, bare, namespace=namespace,
        version=normalize_version(resolved, ecosystem=kind) if resolved else "",
    ))


def _split_version(kind: str, text: str) -> tuple[str, str]:
    """Separate name from version in one ecosystem's notation."""
    if kind == "maven":
        return _maven(text)

    if kind == "npm":
        # `@scope/thing@1.2.3` — the version `@` is the *last* one, never the
        # leading scope marker. Splitting on the first `@` loses the scope.
        if text.startswith("@"):
            at = text.rfind("@")
            if at > 0:
                return text[:at], text[at + 1:]
            return text, ""
        name, _, version = text.partition("@")
        return name, version

    if kind == "pypi":
        match = _PYTHON_SPECIFIER.search(text)
        if match:
            return text[: match.start()].strip(), text[match.end():].strip()
        return _whitespace(text)

    if kind in ("golang", "cargo", "nuget", "gem", "composer"):
        # `github.com/x/y v1.2.3`, `serde 1.0.195`, `Newtonsoft.Json 13.0.3`
        return _whitespace(text)

    if "@" in text:
        name, _, version = text.rpartition("@")
        return name, version
    return _whitespace(text)


def _whitespace(text: str) -> tuple[str, str]:
    parts = text.split()
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1]
    raise CoordinateError(
        f"{text!r} has {len(parts)} whitespace-separated parts; a coordinate is "
        f"a name and at most one version"
    )


def _maven(text: str) -> tuple[str, str]:
    """`group:artifact[:packaging[:classifier]]:version` — the ambiguous one.

    Three segments can mean `group:artifact:version` *or*
    `group:artifact:packaging`, and the two are told apart only by whether the
    third looks like a packaging type. Guessing wrong turns a version into a
    classifier or vice versa, so anything that cannot be resolved raises.
    """
    parts = text.split(":")
    if len(parts) < 2:
        raise CoordinateError(
            f"{text!r} is not a Maven coordinate; expected at least group:artifact"
        )
    if len(parts) == 2:
        return f"{parts[0]}:{parts[1]}", ""
    if len(parts) == 3:
        third = parts[2]
        if third in PACKAGING:
            return f"{parts[0]}:{parts[1]}", ""
        return f"{parts[0]}:{parts[1]}", third
    if len(parts) in (4, 5):
        # group:artifact:packaging:version or …:packaging:classifier:version
        return f"{parts[0]}:{parts[1]}", parts[-1]
    raise CoordinateError(
        f"{text!r} has {len(parts)} colon-separated segments; a Maven "
        f"coordinate has between two and five"
    )


def to_native(value: Purl | str) -> str:
    """A purl → the notation that ecosystem's own tooling accepts.

    Reports render this rather than the purl, because telling a Go developer
    their problem is `pkg:golang/github.com/x/y@v1.2.3` when `go get` wants
    `github.com/x/y@v1.2.3` leaves them to do the translation.
    """
    parsed = value if isinstance(value, Purl) else Purl.parse(value)
    kind = canonical_ecosystem(parsed.type)
    name = parsed.name
    namespace = parsed.namespace or ""
    version = parsed.version or ""

    if kind == "maven":
        group = namespace or ""
        base = f"{group}:{name}" if group else name
        return f"{base}:{version}" if version else base

    if kind == "npm":
        base = f"{namespace}/{name}" if namespace else name
        return f"{base}@{version}" if version else base

    if kind == "pypi":
        return f"{name}=={version}" if version else name

    if kind == "golang":
        base = f"{namespace}/{name}" if namespace else name
        if not version:
            return base
        # Go versions are canonically `vX.Y.Z`. Normalisation strips the `v`
        # for comparison, and `go.mod` will not accept it back without one.
        return f"{base} {version if version.startswith('v') else 'v' + version}"

    if kind in ("cargo", "nuget", "gem", "composer"):
        base = f"{namespace}/{name}" if namespace else name
        return f"{base} {version}" if version else base

    base = f"{namespace}/{name}" if namespace else name
    return f"{base}@{version}" if version else base


def round_trips(ecosystem: str, coordinate: str) -> bool:
    """Whether a coordinate survives both directions unchanged.

    The property that makes this module trustworthy: an identity good in one
    direction and lossy in the other is not an identity, it is a formatter.
    """
    try:
        first = to_purl(ecosystem, coordinate)
        return to_purl(ecosystem, to_native(first)) == first
    except (CoordinateError, InvalidPurl):
        return False


def same_package(left: Purl | str, right: Purl | str) -> bool:
    """Whether two references name one package, version aside.

    The question linking asks constantly — a manifest range and a lockfile pin
    are the same package at different versions, and must land on one node.
    """
    try:
        return (
            canonical_purl(left).coordinate == canonical_purl(right).coordinate
        )
    except InvalidPurl:
        return False


def describe(value: Purl | str) -> dict[str, Any]:
    """Both spellings plus the coordinate, for a report or an API response."""
    parsed = canonical_purl(value)
    return {
        "purl": parsed.to_string(),
        "native": to_native(parsed),
        "coordinate": parsed.coordinate,
        "ecosystem": parsed.type,
        "namespace": parsed.namespace or "",
        "name": parsed.name,
        "version": parsed.version or "",
    }
