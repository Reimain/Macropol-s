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
