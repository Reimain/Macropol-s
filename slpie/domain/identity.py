"""Identity — how anything in the ecosystem is named, exactly once.

Packages use **Package URL (purl)**, the identifier CycloneDX, SPDX and OSV all
already speak. Adopting it means SBOM export and advisory matching need no
translation layer, which is the single highest-leverage normalization decision
in the platform.

Everything that is not a package — services, APIs, tables, deployments, teams,
device classes — uses an SLPIE URN with the same discipline.

Node identifiers are the **full** BLAKE2b digest of the canonical identity
string. They are never derived from a slug: truncating or case-folding an
identifier is how `@types/node` and `types/node` silently become the same node,
and a knowledge graph that conflates two packages is worse than no graph.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence
from urllib.parse import quote, unquote

from ..errors import InvalidPurl, InvalidUrn

#: Digest size for node identifiers — 20 bytes / 40 hex chars.
ID_BYTES = 20

_TYPE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9.+-]*$")
_URN_KIND_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class _Normalization:
    """Per-type canonicalisation rules, straight from the purl specification."""

    lower_namespace: bool = False
    lower_name: bool = False
    underscore_to_dash: bool = False


#: Types whose canonical form differs from the literal input. Everything not
#: listed preserves case, which matters for Maven group ids and NuGet names.
_RULES: dict[str, _Normalization] = {
    "bitbucket": _Normalization(lower_namespace=True, lower_name=True),
    "composer": _Normalization(lower_namespace=True, lower_name=True),
    "github": _Normalization(lower_namespace=True, lower_name=True),
    "gitlab": _Normalization(lower_namespace=True, lower_name=True),
    "golang": _Normalization(lower_namespace=True, lower_name=True),
    "hex": _Normalization(lower_namespace=True, lower_name=True),
    "npm": _Normalization(lower_namespace=True, lower_name=True),
    "pypi": _Normalization(lower_namespace=True, lower_name=True, underscore_to_dash=True),
}

#: Characters left literal inside a version. Docker digests (`sha256:…`) and
#: Maven qualifiers (`1.0+build`) are unreadable otherwise, and both are legal.
_VERSION_SAFE = ":+"
_QUALIFIER_SAFE = ":/+"


def _encode(value: str, safe: str = "") -> str:
    return quote(value, safe=safe)


def _encode_path(value: str, safe: str = "") -> str:
    """Encode each segment, preserving the separators between them."""
    return "/".join(_encode(part, safe) for part in value.split("/"))


@dataclass(frozen=True, slots=True)
class Purl:
    """A Package URL: ``pkg:type/namespace/name@version?qualifiers#subpath``."""

    type: str
    name: str
    namespace: str = ""
    version: str = ""
    qualifiers: Mapping[str, str] = field(default_factory=dict)
    subpath: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            raise InvalidPurl(self.name, "type is required")
        if not _TYPE_RE.match(self.type):
            raise InvalidPurl(self.type, "type must start with a letter and contain only [a-zA-Z0-9.+-]")
        if not self.name:
            raise InvalidPurl(self.type, "name is required")

    # -- canonicalisation ------------------------------------------------

    @classmethod
    def create(
        cls,
        type: str,  # noqa: A002 - matches the purl field name
        name: str,
        *,
        namespace: str = "",
        version: str = "",
        qualifiers: Mapping[str, str] | None = None,
        subpath: str = "",
    ) -> "Purl":
        """Build a purl in canonical form, applying the type's normalisation."""
        kind = type.strip().lower()
        rule = _RULES.get(kind, _Normalization())

        namespace = namespace.strip().strip("/")
        name = name.strip().strip("/")
        if rule.lower_namespace:
            namespace = namespace.lower()
        if rule.lower_name:
            name = name.lower()
        if rule.underscore_to_dash:
            name = name.replace("_", "-")

        normalized: dict[str, str] = {}
        for key, value in (qualifiers or {}).items():
            if value == "" or value is None:
                continue  # a qualifier with an empty value is omitted, per spec
            normalized[str(key).strip().lower()] = str(value)

        return cls(
            type=kind,
            name=name,
            namespace=namespace,
            version=version.strip(),
            qualifiers=dict(sorted(normalized.items())),
            subpath="/".join(
                seg for seg in subpath.strip().strip("/").split("/")
                if seg not in ("", ".", "..")
            ),
        )

    @classmethod
    def parse(cls, value: str) -> "Purl":
        """Parse a purl string. Tolerant on input, canonical on output."""
        raw = (value or "").strip()
        if not raw:
            raise InvalidPurl(value, "empty")
        scheme, _, remainder = raw.partition(":")
        if scheme.lower() != "pkg":
            raise InvalidPurl(value, "scheme must be 'pkg'")
        # `pkg://type/…` appears in the wild; the spec says to tolerate it.
        remainder = remainder.lstrip("/")
        if not remainder:
            raise InvalidPurl(value, "nothing after the scheme")

        remainder, _, subpath = remainder.partition("#")
        remainder, _, query = remainder.partition("?")

        # Split the version from the right: names may not contain '@' unencoded,
        # but namespaces (npm scopes) legitimately do once decoded.
        head, at, version = remainder.rpartition("@")
        if not at:
            head, version = remainder, ""

        parts = [p for p in head.split("/") if p != ""]
        if len(parts) < 2:
            raise InvalidPurl(value, "expected at least a type and a name")
        kind = parts[0]
        name = unquote(parts[-1])
        namespace = "/".join(unquote(p) for p in parts[1:-1])

        qualifiers: dict[str, str] = {}
        for pair in query.split("&"):
            if not pair:
                continue
            key, _, val = pair.partition("=")
            if key:
                qualifiers[unquote(key)] = unquote(val)

        return cls.create(
            type=kind,
            name=name,
            namespace=namespace,
            version=unquote(version),
            qualifiers=qualifiers,
            subpath="/".join(unquote(s) for s in subpath.split("/")) if subpath else "",
        )

    # -- rendering -------------------------------------------------------

    def to_string(self) -> str:
        out = f"pkg:{self.type}"
        if self.namespace:
            out += "/" + _encode_path(self.namespace)
        out += "/" + _encode(self.name)
        if self.version:
            out += "@" + _encode(self.version, _VERSION_SAFE)
        if self.qualifiers:
            out += "?" + "&".join(
                f"{key}={_encode(val, _QUALIFIER_SAFE)}"
                for key, val in sorted(self.qualifiers.items())
            )
        if self.subpath:
            out += "#" + _encode_path(self.subpath)
        return out

    # -- comparison helpers ----------------------------------------------

    @property
    def coordinate(self) -> str:
        """The identity without a version — what "the same package" means."""
        return replace(self, version="", qualifiers={}, subpath="").to_string()

    def with_version(self, version: str) -> "Purl":
        return Purl.create(
            type=self.type, name=self.name, namespace=self.namespace,
            version=version, qualifiers=self.qualifiers, subpath=self.subpath,
        )

    def without_version(self) -> "Purl":
        return self.with_version("")

    @property
    def display(self) -> str:
        """Human-readable short form: `@scope/name@1.2.3`."""
        label = f"{self.namespace}/{self.name}" if self.namespace else self.name
        return f"{label}@{self.version}" if self.version else label

    def __str__(self) -> str:
        return self.to_string()

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.to_string(), "type": self.type, "namespace": self.namespace,
            "name": self.name, "version": self.version,
            "qualifiers": dict(self.qualifiers), "subpath": self.subpath,
        }


@dataclass(frozen=True, slots=True)
class Urn:
    """An SLPIE URN: ``urn:slpie:<kind>:<segment>/<segment>…``.

    Used for everything that is not a package. The kind is part of the
    identity, so a service and a package that share a name are never the same
    node.
    """

    kind: str
    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _URN_KIND_RE.match(self.kind):
            raise InvalidUrn(self.kind, "kind must match [a-z][a-z0-9-]*")
        if not self.segments or not any(s for s in self.segments):
            raise InvalidUrn(self.kind, "at least one non-empty segment is required")

    @classmethod
    def create(cls, kind: str, *segments: str) -> "Urn":
        flattened: list[str] = []
        for segment in segments:
            flattened.extend(part for part in str(segment).split("/") if part != "")
        return cls(kind=kind.strip().lower(), segments=tuple(flattened))

    @classmethod
    def parse(cls, value: str) -> "Urn":
        raw = (value or "").strip()
        parts = raw.split(":", 3)
        if len(parts) != 4 or parts[0].lower() != "urn" or parts[1].lower() != "slpie":
            raise InvalidUrn(value, "expected urn:slpie:<kind>:<path>")
        kind, path = parts[2], parts[3]
        if not path:
            raise InvalidUrn(value, "empty path")
        return cls(kind=kind.lower(), segments=tuple(unquote(s) for s in path.split("/") if s))

    def to_string(self) -> str:
        return f"urn:slpie:{self.kind}:" + "/".join(_encode(s) for s in self.segments)

    @property
    def name(self) -> str:
        return self.segments[-1]

    @property
    def namespace(self) -> str:
        return "/".join(self.segments[:-1])

    @property
    def display(self) -> str:
        return "/".join(self.segments)

    def child(self, *segments: str) -> "Urn":
        return Urn.create(self.kind, *self.segments, *segments)

    def __str__(self) -> str:
        return self.to_string()

    def to_dict(self) -> dict[str, Any]:
        return {"urn": self.to_string(), "kind": self.kind, "segments": list(self.segments)}


Identity = Purl | Urn


def parse_identity(value: str) -> Identity:
    """Parse either form, dispatching on the scheme."""
    raw = (value or "").strip()
    if raw.startswith("pkg:"):
        return Purl.parse(raw)
    if raw.startswith("urn:"):
        return Urn.parse(raw)
    raise InvalidUrn(value, "expected a 'pkg:' or 'urn:slpie:' identifier")


def node_id(identity: Identity | str) -> str:
    """The content-addressed id for an identity.

    Full digest — never truncated, never slugged. Two identities that differ by
    a single character produce unrelated ids, which is the whole point.
    """
    canonical = identity if isinstance(identity, str) else identity.to_string()
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=ID_BYTES).hexdigest()


def edge_id(kind: str, src: str, dst: str, *, qualifier: str = "") -> str:
    """Content-addressed id for a relationship.

    Includes the kind, so `imports` and `depends_on` between the same pair are
    distinct edges carrying their own evidence — which they are.
    """
    canonical = f"{kind}\x1f{src}\x1f{dst}\x1f{qualifier}"
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=ID_BYTES).hexdigest()


def digest(payload: Any, *, size: int = 16) -> str:
    """Stable content digest for arbitrary JSON-like payloads."""
    import json

    if isinstance(payload, (bytes, bytearray, memoryview)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=size).hexdigest()


def _canonical(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_canonical(v) for v in obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (Purl, Urn)):
        return obj.to_string()
    if hasattr(obj, "to_dict"):
        return _canonical(obj.to_dict())
    return repr(obj)
