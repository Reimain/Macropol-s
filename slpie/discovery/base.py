"""What a discoverer is, and the helpers every one of them needs.

A discoverer reads one source and returns observations, each carrying evidence.
It never touches the graph, never assigns confidence, and never decides what a
relationship means — it reports what it read and where it read it, and the
layers above turn that into structure.

The `observe` helpers exist because getting evidence right is the part it would
be easy to get subtly wrong forty times over. Every discoverer cites a uri and a
line through the same function, so an explanation always terminates somewhere a
human can open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..binding.connector import Connector, Resource
from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.identity import Purl, Urn
from ..plugins.protocol import DiscoveryResult, Observation


def evidence_at(
    uri: str,
    *,
    kind: EvidenceKind,
    extractor: str,
    line: int = 0,
    excerpt: str = "",
    version: str = "1",
    digest: str = "",
) -> Evidence:
    """One piece of evidence, cited properly.

    ``excerpt`` is trimmed rather than dropped: an explanation that cannot show
    the line it read is not an explanation, and the UI renders it verbatim.
    """
    return Evidence(
        kind=kind,
        location=SourceLocation(uri, line=line),
        extractor=extractor,
        extractor_version=version,
        excerpt=excerpt.strip()[:300],
        content_digest=digest,
    )


def depends(
    source: str, target: str, evidence: Evidence, *, qualifier: str = "", **properties: Any
) -> Observation:
    return Observation(
        kind="depends_on", subject=source, object=target,
        evidence=evidence, qualifier=qualifier, properties=properties,
    )


def imports(source: str, target: str, evidence: Evidence, **properties: Any) -> Observation:
    return Observation(
        kind="imports", subject=source, object=target,
        evidence=evidence, properties=properties,
    )


def declares(subject: str, evidence: Evidence, **properties: Any) -> Observation:
    """A node with no relationship — "this thing exists, and here is proof"."""
    return Observation(
        kind="declares", subject=subject, evidence=evidence, properties=properties,
    )


def find_line(text: str, needle: str, *, start: int = 0) -> int:
    """The 1-based line a substring first appears on, or 0.

    Line numbers are what make an explanation checkable, so a discoverer that
    can find one should. Zero is honest when it cannot.
    """
    if not needle:
        return 0
    index = text.find(needle, start)
    if index < 0:
        return 0
    return text.count("\n", 0, index) + 1


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


def excerpt_at(text: str, line: int) -> str:
    """The literal line, for the explanation to quote."""
    if line < 1:
        return ""
    lines = text.splitlines()
    return lines[line - 1].strip() if line <= len(lines) else ""


@dataclass(frozen=True, slots=True)
class Source:
    """One file handed to a discoverer, with its logical identity intact."""

    uri: str
    text: str
    digest: str = ""
    element: str = ""

    @property
    def name(self) -> str:
        return self.uri.rsplit("/", 1)[-1]

    def line(self, needle: str) -> int:
        return find_line(self.text, needle)

    def excerpt(self, line: int) -> str:
        return excerpt_at(self.text, line)

    @classmethod
    def from_resource(cls, resource: Resource, *, element: str = "") -> "Source":
        return cls(
            uri=resource.uri, text=resource.text,
            digest=resource.digest, element=element,
        )


def scope_of(element: str, default: str = "workspace") -> str:
    """An element URN reduced to a name that can appear *inside* an identity.

    `element` is a URN — `urn:slpie:codebase:payments`. Several discoverers need
    a fallback name for something the artifact does not name itself: a Gradle
    build with no `rootProject.name`, an unnamed compose stack, a Dockerfile
    image. Reaching for `source.element` directly is the obvious move and it is
    wrong: the URN gets percent-encoded into the purl, producing identities like
    `pkg:maven/urn%3Aslpie%3Acodebase%3Apayments`.

    That is not cosmetic. Node identity is a digest of the canonical string, so
    a package named this way can never converge with the same package reported
    by a lockfile reader — and convergence across discoverers is the single
    property the linking layer exists to provide. Anything that silently
    prevents it produces two nodes where the ecosystem has one, and every answer
    built on top is quietly wrong.

    So: last meaningful segment, or the stated default. One helper, used
    everywhere, rather than each discoverer inventing its own fallback.
    """
    text = (element or "").strip()
    if not text:
        return default
    if text.startswith("urn:"):
        # urn:slpie:codebase:payments/api → payments/api → api
        tail = text.split(":")[-1]
    else:
        tail = text
    segment = tail.rstrip("/").rsplit("/", 1)[-1]
    return segment or default


def module_urn(element: str, path: str) -> Urn:
    """The identity of a source module inside an element."""
    cleaned = path.lstrip("./").replace("\\", "/")
    return Urn.create("module", element or "root", cleaned)


def workspace_purl(ecosystem: str, name: str, version: str = "") -> Purl:
    """The package a workspace itself publishes."""
    return Purl.create(ecosystem, name or "workspace", version=version)


def empty(reason: str = "") -> DiscoveryResult:
    return DiscoveryResult(errors=(reason,) if reason else ())


def result(
    observations: Iterable[Observation], *, errors: Iterable[str] = ()
) -> DiscoveryResult:
    return DiscoveryResult(
        observations=tuple(observations), errors=tuple(errors),
    )
