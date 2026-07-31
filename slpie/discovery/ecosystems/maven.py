"""Maven — `pom.xml`, read with the namespace it actually declares.

Every real POM lives in `http://maven.apache.org/POM/4.0.0`, so
``root.find("dependencies")`` returns ``None`` on essentially every file in the
world. That is the classic bug in POM readers and it fails silently: the
discoverer reports nothing and looks like it simply found a project with no
dependencies. So nothing here matches on a qualified tag at all — every lookup
goes through :func:`local`, which compares the local part and ignores whichever
namespace the file happens to use.

Four other things a POM does that a naive read misses:

* ``<properties>`` — ``<version>${jackson.version}</version>`` is the normal way
  to write a POM, and an unsubstituted ``${…}`` is not a version.
* ``<dependencyManagement>`` — declares versions without declaring dependencies.
  Reported, and marked as managed rather than required.
* ``<parent>`` — supplies the groupId and version this POM omits, and is itself
  a dependency of the build.
* ``<scope>`` — ``test`` and ``provided`` dependencies are *real*. They are on
  the compile or test classpath and they carry vulnerabilities like anything
  else, so scope is recorded as a qualifier and never used to drop an edge.

A truncated POM yields whatever elements closed before the truncation. Half a
file is still evidence about the half that parsed, and losing it because the
last tag is missing would be a strange kind of purity.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping
from xml.etree import ElementTree as ET

from ...domain.evidence import EvidenceKind
from ...domain.identity import Purl
from ...domain.version import parse_range
from ...errors import VersionError
from ...plugins.protocol import DiscoveryResult, Observation
from ..base import (
    Source,
    declares,
    depends,
    evidence_at,
    find_line,
    result,
    workspace_purl,
)

EXTRACTOR = "slpie.maven"

#: How deep `${a}` -> `${b}` -> `1.0` chains are followed before giving up.
MAX_PROPERTY_DEPTH = 8


# --- namespace-blind XML -------------------------------------------------


def local(tag: str) -> str:
    """`{http://maven.apache.org/POM/4.0.0}dependency` -> `dependency`."""
    return tag.rpartition("}")[2]


def children(element: ET.Element, name: str) -> Iterator[ET.Element]:
    """Direct children with this local name, whatever namespace they carry."""
    for child in element:
        if isinstance(child.tag, str) and local(child.tag) == name:
            yield child


def child(element: ET.Element, name: str) -> ET.Element | None:
    return next(children(element, name), None)


def text_of(element: ET.Element | None, name: str, default: str = "") -> str:
    if element is None:
        return default
    found = child(element, name)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def descendants(element: ET.Element, name: str) -> Iterator[ET.Element]:
    for node in element.iter():
        if isinstance(node.tag, str) and local(node.tag) == name:
            yield node


def parse_xml(text: str) -> tuple[ET.Element | None, tuple[ET.Element, ...], str]:
    """Parse, and on failure salvage every element that did closed.

    Returns ``(root, salvaged, error)``. ``root`` is present only for a
    well-formed document; ``salvaged`` holds the elements that completed before
    the parser gave up, in the order they closed.
    """
    try:
        return ET.fromstring(text), (), ""
    except ET.ParseError as error:
        reason = str(error)

    # XMLPullParser stores the parse error and re-raises it from
    # `read_events()`, not from `feed()` or `close()` — so guarding only the
    # feed leaves the salvage path raising on exactly the input it exists to
    # salvage. A scanner meets binary files and half-written XML routinely, and
    # neither may take the run down.
    parser = ET.XMLPullParser(events=("end",))
    salvaged: tuple[Any, ...] = ()
    try:
        parser.feed(text)
        parser.close()
    except (ET.ParseError, ValueError, TypeError):
        pass
    try:
        salvaged = tuple(element for _event, element in parser.read_events())
    except (ET.ParseError, ValueError):
        salvaged = ()
    return None, salvaged, reason


# --- coordinates ---------------------------------------------------------


def maven_purl(group: str, artifact: str, version: str = "") -> Purl:
    """groupId is the namespace, artifactId the name — both case-preserving.

    Maven group ids are case-sensitive in practice, and purl does not normalise
    them, so nothing here lowercases anything.
    """
    return Purl.create("maven", artifact, namespace=group, version=version)


class _Lines:
    """Successive occurrences of a needle, so repeated artifacts cite their own line.

    An artifactId that appears in both `<dependencyManagement>` and
    `<dependencies>` must not have both observations pointing at the first one.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self._seen: dict[str, int] = {}

    def find(self, needle: str) -> int:
        if not needle:
            return 0
        start = self._seen.get(needle, 0)
        index = self.text.find(needle, start)
        if index < 0:
            index = self.text.find(needle)
        if index < 0:
            return 0
        self._seen[needle] = index + len(needle)
        return find_line(self.text, needle, start=index)


def _resolve(value: str, properties: Mapping[str, str], *, depth: int = 0) -> str:
    """Substitute `${foo.version}`, or leave it alone and say so.

    Leaving an unresolved placeholder in place is the honest outcome: inventing
    a version for it would put a made-up number behind a real file and line.
    """
    if "${" not in value or depth >= MAX_PROPERTY_DEPTH:
        return value
    out = value
    for key, replacement in properties.items():
        out = out.replace("${" + key + "}", replacement)
    if out == value:
        return out
    return _resolve(out, properties, depth=depth + 1)


def _range_facts(spec: str) -> dict[str, Any]:
    """What the shared range parser makes of a Maven version.

    A bare Maven version is a *soft* requirement, not a pin, and the shared
    parser already knows that — which is exactly why it is used here instead of
    a second opinion written locally.
    """
    if not spec or "${" in spec:
        return {"range": spec, "range_parsed": False, "range_exact": ""}
    try:
        parsed = parse_range(spec, ecosystem="maven")
    except VersionError:
        return {"range": spec, "range_parsed": False, "range_exact": ""}
    exact = parsed.exact_version
    return {
        "range": spec, "range_parsed": True,
        "range_exact": exact.text if exact else "",
    }


# --- pom.xml -------------------------------------------------------------


def discover_pom(source: Source) -> DiscoveryResult:
    """Read a POM's own coordinates and everything it depends on."""
    root, salvaged, error = parse_xml(source.text)
    errors = [f"{source.uri}: malformed XML ({error})"] if error else []
    lines = _Lines(source.text)

    if root is None:
        return _from_salvage(source, salvaged, lines, errors)

    parent = child(root, "parent")
    properties = _properties(root)
    group = text_of(root, "groupId") or text_of(parent, "groupId")
    artifact = text_of(root, "artifactId") or source.element or "project"
    version = _resolve(
        text_of(root, "version") or text_of(parent, "version"), properties
    )
    properties.setdefault("project.version", version)
    properties.setdefault("pom.version", version)
    properties.setdefault("project.groupId", group)
    properties.setdefault("pom.groupId", group)

    subject = maven_purl(group or "unknown", artifact, version).to_string()
    line = lines.find(f"<artifactId>{artifact}</artifactId>")
    observations: list[Observation] = [
        declares(
            subject,
            evidence_at(
                source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                line=line, excerpt=source.excerpt(line), digest=source.digest,
            ),
            node_kind="package", ecosystem="maven",
            packaging=text_of(root, "packaging", "jar"),
            modules=tuple(
                (node.text or "").strip()
                for node in descendants(root, "module") if node.text
            ),
        )
    ]

    if parent is not None:
        parent_group = text_of(parent, "groupId")
        parent_artifact = text_of(parent, "artifactId")
        parent_version = _resolve(text_of(parent, "version"), properties)
        if parent_artifact:
            parent_line = lines.find(f"<artifactId>{parent_artifact}</artifactId>")
            observations.append(depends(
                subject,
                maven_purl(parent_group or "unknown", parent_artifact).to_string(),
                evidence_at(
                    source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
                    line=parent_line, excerpt=source.excerpt(parent_line),
                    digest=source.digest,
                ),
                qualifier="parent", scope="parent", ecosystem="maven",
                relative_path=text_of(parent, "relativePath"),
                **_range_facts(parent_version),
            ))

    management = child(root, "dependencyManagement")
    if management is not None:
        for node in _dependency_nodes(management):
            observations.extend(_dependency(
                source, lines, subject, node, properties, managed=True,
            ))

    for container in children(root, "dependencies"):
        for node in children(container, "dependency"):
            observations.extend(_dependency(
                source, lines, subject, node, properties, managed=False,
            ))

    return result(observations, errors=errors)


def _from_salvage(
    source: Source,
    salvaged: tuple[ET.Element, ...],
    lines: _Lines,
    errors: list[str],
) -> DiscoveryResult:
    """Whatever a broken POM managed to close before it broke.

    The project's own coordinates are usually part of the wreckage, so the
    subject falls back to the element name rather than being guessed at.
    """
    properties: dict[str, str] = {}
    for element in salvaged:
        if local(element.tag) == "properties":
            properties.update(_property_table(element))

    subject = workspace_purl("maven", source.element or "project").to_string()
    observations: list[Observation] = []
    for element in salvaged:
        if local(element.tag) != "dependency":
            continue
        observations.extend(_dependency(
            source, lines, subject, element, properties, managed=False,
        ))
    return result(observations, errors=errors)


def _dependency_nodes(management: ET.Element) -> Iterator[ET.Element]:
    for container in children(management, "dependencies"):
        yield from children(container, "dependency")


def _properties(root: ET.Element) -> dict[str, str]:
    table: dict[str, str] = {}
    for container in children(root, "properties"):
        table.update(_property_table(container))
    return table


def _property_table(container: ET.Element) -> dict[str, str]:
    return {
        local(node.tag): (node.text or "").strip()
        for node in container
        if isinstance(node.tag, str)
    }


def _dependency(
    source: Source,
    lines: _Lines,
    subject: str,
    node: ET.Element,
    properties: Mapping[str, str],
    *,
    managed: bool,
) -> list[Observation]:
    group = _resolve(text_of(node, "groupId"), properties)
    artifact = _resolve(text_of(node, "artifactId"), properties)
    if not artifact:
        return []

    declared = text_of(node, "version")
    version = _resolve(declared, properties)
    scope = text_of(node, "scope") or "compile"

    line = lines.find(f"<artifactId>{artifact}</artifactId>")
    return [depends(
        subject,
        maven_purl(group or "unknown", artifact).to_string(),
        evidence_at(
            source.uri, kind=EvidenceKind.MANIFEST_DECLARED, extractor=EXTRACTOR,
            line=line, excerpt=source.excerpt(line), digest=source.digest,
        ),
        # A test-scoped dependency is a dependency. Scope says which classpath,
        # not whether the edge exists.
        qualifier="managed" if managed else scope,
        scope=scope,
        managed=managed,
        optional=text_of(node, "optional").lower() == "true",
        classifier=text_of(node, "classifier"),
        type=text_of(node, "type", "jar"),
        declared_version=declared,
        unresolved_property="${" in version,
        exclusions=tuple(
            text_of(excluded, "artifactId")
            for excluded in descendants(node, "exclusion")
        ),
        ecosystem="maven",
        **_range_facts(version),
    )]


# --- registration --------------------------------------------------------

DISCOVERERS = (
    (
        "slpie.maven.pom", discover_pom,
        ("pom.xml",), ("depends_on", "declares"),
        (EvidenceKind.MANIFEST_DECLARED,),
    ),
)
