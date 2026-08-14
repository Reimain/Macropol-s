"""Canonical purl form — the function that makes two discoverers agree.

:mod:`slpie.domain.identity` already applies the purl specification's own
normalisation rules. This module is the layer above: the ecosystem-specific
*name shaping* that has to happen before those rules are even applicable, and
which the specification leaves to the ecosystem.

The rules that actually matter, and why each one is not optional:

* **PyPI** folds runs of ``-``, ``_`` and ``.`` to a single ``-`` and lowercases
  (PEP 503). ``Typing_Extensions``, ``typing.extensions`` and
  ``typing-extensions`` are one distribution, and a graph that made them three
  nodes would report three copies of every vulnerability against them.
* **npm** does *not* fold separators — ``node_fetch`` and ``node-fetch`` are
  different packages — but a scope is a namespace, not part of the name.
  ``@types/node`` is namespace ``@types``, name ``node``; collapsing it to one
  string makes every scoped package collide with a differently-scoped one.
* **Go** module paths keep their slashes: ``github.com/spf13/cobra`` is namespace
  ``github.com/spf13``, name ``cobra``. Separator folding here would be
  catastrophic, since the path is a URL.
* **Maven** coordinates arrive as ``group:artifact``, which splits to namespace
  and name. Nothing is lowercased — Maven group ids are case-sensitive.
* **NuGet** package ids are case-insensitive, so they lowercase.

Namespaces are percent-encoded on the way out and decoded on the way in by
:meth:`Purl.to_string` / :meth:`Purl.parse`. :func:`round_trips` asserts that is
lossless, and the test suite asserts it for every shape this module produces —
an identifier that does not survive its own rendering is not an identifier.

One rule this module does *not* have: case folding for Go. The purl
specification lowercases the ``golang`` type, and
:func:`slpie.domain.identity.Purl.create` applies that for us. Deviating here
would mean a discoverer that built its purl through the domain and one that came
through this module produced different node ids — which is exactly the failure
normalisation exists to prevent.
"""

from __future__ import annotations

import re
from typing import Mapping

from ..domain.identity import Purl, node_id
from ..errors import InvalidPurl
from .versions import canonical_ecosystem, normalize_version

#: The purl type for an ecosystem alias. Same table as versions, same answer.
canonical_type = canonical_ecosystem

_PYPI_SEPARATORS = re.compile(r"[-_.]+")


def split_name(ecosystem: str, raw: str) -> tuple[str, str]:
    """Split a raw package name into ``(namespace, name)`` for one ecosystem.

    The input is whatever a discoverer read: ``@types/node``,
    ``com.google.guava:guava``, ``github.com/spf13/cobra``, ``Typing_Extensions``.
    """
    kind = canonical_type(ecosystem)
    text = (raw or "").strip().strip("/")
    if not text:
        return "", ""

    if kind == "pypi":
        return "", _PYPI_SEPARATORS.sub("-", text).lower()

    if kind == "npm":
        if text.startswith("@") and "/" in text:
            namespace, _, bare = text.partition("/")
            return namespace, bare
        return "", text

    if kind in ("golang", "github", "gitlab", "bitbucket", "oci", "composer"):
        parts = [part for part in text.split("/") if part]
        if len(parts) == 1:
            return "", parts[0]
        return "/".join(parts[:-1]), parts[-1]

    if kind == "maven":
        if ":" in text:
            group, _, artifact = text.partition(":")
            # `group:artifact:version` reaches here from a native coordinate;
            # anything past the artifact is not part of the name.
            return group.strip(), artifact.partition(":")[0].strip()
        if "/" in text:
            namespace, _, name = text.rpartition("/")
            return namespace, name
        return "", text

    if kind == "nuget":
        return "", text.lower()

    if kind in ("cargo", "gem", "hex"):
        return "", text.lower()

    if "/" in text:
        namespace, _, name = text.rpartition("/")
        return namespace, name
    return "", text


def canonical_purl(
    value: Purl | str,
    *,
    ecosystem: str = "",
    version: str = "",
    qualifiers: Mapping[str, str] | None = None,
    subpath: str = "",
) -> Purl:
    """The canonical purl for anything a discoverer can hand us.

    Accepts an existing :class:`Purl`, a ``pkg:`` string, or a bare package name
    plus an ecosystem. Idempotent: normalising an already-canonical purl returns
    an equal purl, which is what lets the resolver compare two spellings by
    running both through here.
    """
    if isinstance(value, Purl):
        kind = canonical_type(ecosystem or value.type)
        raw = f"{value.namespace}/{value.name}" if value.namespace else value.name
        version = version or value.version
        if qualifiers is None:
            qualifiers = value.qualifiers
        subpath = subpath or value.subpath
    else:
        text = str(value or "").strip()
        if not text:
            raise InvalidPurl(str(value), "empty identifier")
        if text.startswith("pkg:"):
            return canonical_purl(
                Purl.parse(text), ecosystem=ecosystem, version=version,
                qualifiers=qualifiers, subpath=subpath,
            )
        kind = canonical_type(ecosystem)
        raw = text

    namespace, name = split_name(kind, raw)
    if not name:
        raise InvalidPurl(str(value), "no package name survived normalisation")

    return Purl.create(
        kind,
        name,
        namespace=namespace,
        version=normalize_version(version, ecosystem=kind),
        qualifiers=dict(qualifiers or {}),
        subpath=subpath,
    )


def canonical_string(value: Purl | str, **options: object) -> str:
    """The canonical purl, rendered — what gets hashed into a node id."""
    return canonical_purl(value, **options).to_string()  # type: ignore[arg-type]


def canonical_coordinate(value: Purl | str, **options: object) -> str:
    """The version-independent identity: what "the same package" means."""
    return canonical_purl(value, **options).coordinate  # type: ignore[arg-type]


def canonical_node_id(value: Purl | str, **options: object) -> str:
    """The node id two dialects of the same package must agree on."""
    return node_id(canonical_purl(value, **options))  # type: ignore[arg-type]


def round_trips(purl: Purl) -> bool:
    """Whether rendering and re-parsing a purl returns the same purl.

    Percent-encoding a namespace is only safe if it is reversible. This is the
    check, and it is asserted rather than assumed.
    """
    try:
        return Purl.parse(purl.to_string()) == purl
    except InvalidPurl:
        return False
