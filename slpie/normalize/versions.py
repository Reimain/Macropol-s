"""Per-ecosystem version normalisation — adapters, not a second version algebra.

:mod:`slpie.domain.version` already knows how to *compare* versions across every
ecosystem the platform reads. What it deliberately does not know is the textual
canonicalisation each ecosystem applies before two spellings count as the same
release, and that is a normalisation concern rather than an ordering one:

* **Go** writes ``v1.2.3`` in ``go.mod`` and ``1.2.3`` in a vendor manifest, and
  appends ``+incompatible`` to pre-modules majors.
* **Maven** spells the same snapshot ``1.0-SNAPSHOT``, ``1.0.SNAPSHOT`` and
  ``1.0-snapshot``.
* **NuGet** normalises four-part versions with a zero fourth part down to three,
  and treats pre-release labels case-insensitively.
* **PEP 440** folds ``1.0.RC1``, ``1.0-rc-1`` and ``1.0rc1`` together, and carries
  an epoch that ``0!`` renders as nothing at all.

Every function here ends in :class:`~slpie.domain.version.Version` or
:class:`~slpie.domain.version.VersionRange` from the domain. Nothing here
reimplements comparison — a second ordering would be a second answer.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from ..domain.version import Version, VersionRange, parse_range

#: Every alias the platform meets, folded onto the canonical purl type.
#:
#: Gradle publishes into Maven coordinates and reads Maven ranges, so it is not
#: a separate ecosystem for identity purposes — it is a different front end onto
#: the same one.
_ECOSYSTEMS: dict[str, str] = {
    "": "generic",
    "generic": "generic",
    "pypi": "pypi", "python": "pypi", "pip": "pypi", "pep508": "pypi",
    "poetry": "pypi", "uv": "pypi", "setuptools": "pypi",
    "npm": "npm", "node": "npm", "nodejs": "npm", "yarn": "npm", "pnpm": "npm",
    "golang": "golang", "go": "golang", "gomod": "golang", "go.mod": "golang",
    "maven": "maven", "gradle": "maven", "mvn": "maven", "jvm": "maven", "java": "maven",
    "nuget": "nuget", "dotnet": "nuget", "csharp": "nuget", "net": "nuget",
    "cargo": "cargo", "crates": "cargo", "crates.io": "cargo", "rust": "cargo",
    "gem": "gem", "rubygems": "gem", "ruby": "gem",
    "composer": "composer", "php": "composer",
    "hex": "hex", "elixir": "hex",
    "oci": "oci", "docker": "oci", "container": "oci", "image": "oci",
    "github": "github", "gitlab": "gitlab", "bitbucket": "bitbucket",
}

#: Characters that can only appear in a *constraint*, never in a bare version.
_OPERATOR_CHARACTERS = frozenset("<>=!~^,|[]() ")

_NUGET = re.compile(r"^(?P<release>\d+(?:\.\d+){0,3})(?P<rest>[-+].*)?$")
_SNAPSHOT_MARKERS = ("-snapshot", ".snapshot", "_snapshot")

_PEP440 = re.compile(
    r"^v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d+)?)?"
    r"(?:-(?P<post_implicit>\d+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n>\d+)?)?"
    r"(?:[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>\d+)?)?"
    r"(?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?$",
    re.IGNORECASE,
)

#: PEP 440 spells every pre-release marker one of exactly three ways.
_PRE_RELEASE_ALIAS = {
    "alpha": "a", "a": "a",
    "beta": "b", "b": "b",
    "c": "rc", "pre": "rc", "preview": "rc", "rc": "rc",
}


def canonical_ecosystem(ecosystem: str) -> str:
    """Fold an ecosystem alias onto the canonical purl type.

    Unknown names pass through lowercased rather than becoming ``generic``: a
    type SLPIE has never heard of is still an identity, and silently relabelling
    it would merge two ecosystems that share a package name.
    """
    key = (ecosystem or "").strip().lower().removeprefix("pkg:")
    return _ECOSYSTEMS.get(key, key or "generic")


def normalize_version(raw: str, *, ecosystem: str = "generic") -> str:
    """The canonical spelling of one version string, for one ecosystem.

    Returns text rather than a :class:`Version` because this is what goes into a
    purl, and therefore into a node id. Two dialects of the same release must
    produce byte-identical output here or they will land on different nodes.
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        return ""
    kind = canonical_ecosystem(ecosystem)
    if kind == "golang":
        return _golang(text)
    if kind == "maven":
        return _maven(text)
    if kind == "nuget":
        return _nuget(text)
    if kind == "pypi":
        return _pep440(text)
    return _plain(text)


def _plain(text: str) -> str:
    """Strip the decorations every ecosystem agrees are not part of a version."""
    text = text.lstrip("=").strip()
    if len(text) > 1 and text[0] in "vV" and text[1].isdigit():
        text = text[1:]
    return text


def _golang(text: str) -> str:
    """Go: the ``v`` is syntax, and ``+incompatible`` is a resolver annotation.

    A pseudo-version (``0.0.0-20191109021931-daa7c04131f5``) is left intact —
    the timestamp and the commit prefix *are* the version.
    """
    text = _plain(text)
    if text.lower().endswith("+incompatible"):
        text = text[: -len("+incompatible")]
    return text


def _maven(text: str) -> str:
    text = _plain(text)
    lowered = text.lower()
    for marker in _SNAPSHOT_MARKERS:
        if lowered.endswith(marker):
            # `-SNAPSHOT` is the one spelling Maven itself treats as special.
            return text[: -len(marker)] + "-SNAPSHOT"
    return text


def _nuget(text: str) -> str:
    """NuGet's own normalisation: pad to three parts, drop a zero fourth."""
    text = _plain(text)
    match = _NUGET.match(text)
    if not match:
        return text.lower()
    parts = [int(part) for part in match.group("release").split(".")]
    while len(parts) < 3:
        parts.append(0)
    if len(parts) == 4 and parts[3] == 0:
        parts = parts[:3]
    return ".".join(str(part) for part in parts) + (match.group("rest") or "").lower()


def _pep440(text: str) -> str:
    """PEP 440 canonical form, including the epoch and the ``.postN`` shorthand."""
    match = _PEP440.match(text.strip())
    if not match:
        return _plain(text).lower()

    out = ""
    epoch = int(match.group("epoch") or 0)
    if epoch:
        out += f"{epoch}!"
    out += ".".join(str(int(part)) for part in match.group("release").split("."))

    if marker := match.group("pre_l"):
        out += _PRE_RELEASE_ALIAS[marker.lower()] + str(int(match.group("pre_n") or 0))

    implicit = match.group("post_implicit")
    if implicit is not None:
        out += ".post" + str(int(implicit))
    elif match.group("post_l"):
        out += ".post" + str(int(match.group("post_n") or 0))

    if match.group("dev_l"):
        out += ".dev" + str(int(match.group("dev_n") or 0))

    if local := match.group("local"):
        out += "+" + re.sub(r"[-_.]+", ".", local.lower())
    return out


def parse_version(raw: str, *, ecosystem: str = "generic") -> Version:
    """Normalise, then hand straight to the domain's comparable ``Version``."""
    normalized = normalize_version(raw, ecosystem=ecosystem)
    return Version.parse(normalized or raw)


def normalize_range(spec: str, *, ecosystem: str = "generic") -> VersionRange:
    """Parse an ecosystem's constraint dialect into the shared range type.

    NuGet borrowed Maven's interval notation wholesale, so a bracketed NuGet
    range is handed to the Maven parser — and then relabelled, because the range
    still belongs to NuGet and a report that said otherwise would be wrong.
    """
    kind = canonical_ecosystem(ecosystem)
    text = (spec or "").strip()
    dialect = "maven" if kind in ("maven", "nuget") and ("[" in text or "(" in text) else kind
    parsed = parse_range(text, ecosystem=dialect)
    return parsed if parsed.ecosystem == kind else replace(parsed, ecosystem=kind)


def is_exact(spec: str, *, ecosystem: str = "generic") -> bool:
    """Whether a constraint names exactly one version."""
    text = (spec or "").strip()
    if not text:
        return False
    if not any(character in _OPERATOR_CHARACTERS for character in text):
        return True
    try:
        return normalize_range(text, ecosystem=ecosystem).is_exact
    except Exception:  # an unparseable constraint is certainly not an exact pin
        return False


def exact_version(spec: str, *, ecosystem: str = "generic") -> str:
    """The single version a constraint pins, or ``""`` when it pins a window."""
    if not is_exact(spec, ecosystem=ecosystem):
        return ""
    text = (spec or "").strip().lstrip("=").strip()
    return normalize_version(text, ecosystem=ecosystem)


def compare(left: str, right: str, *, ecosystem: str = "generic") -> int:
    """-1 / 0 / +1, delegating every ordering decision to the domain."""
    first = parse_version(left, ecosystem=ecosystem)
    second = parse_version(right, ecosystem=ecosystem)
    if first == second:
        return 0
    return -1 if first < second else 1


def highest(versions: Iterable[str], *, ecosystem: str = "generic") -> str:
    """The greatest of a set of raw version strings, in canonical spelling."""
    parsed = [
        (parse_version(raw, ecosystem=ecosystem), normalize_version(raw, ecosystem=ecosystem))
        for raw in versions
        if (raw or "").strip()
    ]
    return max(parsed, key=lambda pair: pair[0])[1] if parsed else ""


def is_prerelease(raw: str, *, ecosystem: str = "generic") -> bool:
    return parse_version(raw, ecosystem=ecosystem).is_prerelease
