"""Turning what discoverers said into what the graph holds.

Twenty-nine discoverers read the same repository and describe overlapping
things. A `pom.xml` and a `build.gradle` both name `com.acme:payments`. A
`package.json` states a range and `package-lock.json` states a pin. A
Kubernetes manifest and a Dockerfile both name `acme/payments:1.2.3`. The graph
must hold one node for each of those, with all the evidence attached — not three
nodes that happen to look similar in a UI.

This module is where that happens, and it has exactly one rule:

**Merge on identity, never on similarity.** Two observations converge when
their canonical coordinates are equal after normalisation. They do *not*
converge because their names look alike, because one is a prefix of the other,
or because an edit distance is small. Name-similarity merging feels helpful and
is how `requests` and `requests-oauthlib` become one node — and once merged,
nothing downstream can tell they were ever separate.

The second decision is what happens to *versions*. A manifest says `^1.2.0` and
a lockfile says `1.2.7`. Those are the same package, so they share a coordinate
and a node; but a range is not a pin, and collapsing them would throw away the
distinction the whole reconciliation layer depends on. So the node is keyed on
the coordinate, the resolved version is an attribute carrying its evidence, and
a contradiction between two *pins* is a finding rather than a silent overwrite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..domain.evidence import Evidence
from ..domain.identity import InvalidPurl, Purl, Urn, parse_identity
from ..errors import IdentityError
from ..normalize.coordinates import same_package
from ..normalize.purl import canonical_purl, node_id
from ..plugins.protocol import Observation

#: Observation kinds that assert a node rather than a relationship.
DECLARATIONS = frozenset({"declares"})

#: Property names a discoverer uses for a version range, in order of trust.
RANGE_KEYS = ("range", "version", "resolved", "requirement")


class ResolutionError(IdentityError):
    """An observation could not be resolved to an identity."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """One identity, and every observation that reached it."""

    identity: str                       # the canonical purl or urn string
    node_id: str
    coordinate: str                     # version-independent: what "same" means
    kind: str = ""
    observations: tuple[Observation, ...] = ()
    versions: tuple[str, ...] = ()
    pinned: str = ""
    ranges: tuple[str, ...] = ()

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            observation.evidence for observation in self.observations
            if observation.evidence is not None
        )

    @property
    def corroborated(self) -> bool:
        """Whether more than one independent source reached this identity.

        Independence is by *file*, not by observation: a discoverer reporting
        the same package twice from one lockfile is one source, and counting it
        twice would manufacture corroboration out of a loop.
        """
        return len({
            evidence.location.uri for evidence in self.evidence
        }) > 1

    @property
    def contradicted(self) -> bool:
        """Two lockfiles pinning the same package to different versions."""
        pins = {v for v in self.versions if v}
        return len(pins) > 1 and bool(self.pinned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity, "node_id": self.node_id,
            "coordinate": self.coordinate, "kind": self.kind,
            "observations": len(self.observations),
            "sources": sorted({e.location.uri for e in self.evidence}),
            "versions": list(self.versions), "pinned": self.pinned,
            "ranges": list(self.ranges),
            "corroborated": self.corroborated, "contradicted": self.contradicted,
        }

    def __str__(self) -> str:
        marks = []
        if self.corroborated:
            marks.append("corroborated")
        if self.contradicted:
            marks.append("CONTRADICTED")
        return f"{self.identity}" + (f" [{', '.join(marks)}]" if marks else "")


@dataclass(frozen=True, slots=True)
class Link:
    """One relationship, between two resolved identities."""

    kind: str
    source: str
    target: str
    evidence: Evidence
    qualifier: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        import hashlib

        material = f"{self.kind}\x1f{self.source}\x1f{self.target}\x1f{self.qualifier}"
        return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "source": self.source,
            "target": self.target, "qualifier": self.qualifier,
            "uri": self.evidence.location.uri, "line": self.evidence.location.line,
        }

    def __str__(self) -> str:
        return f"{self.source} --{self.kind}--> {self.target}"


@dataclass(frozen=True, slots=True)
class Resolution:
    """Everything the resolver made of one batch of observations."""

    resolved: tuple[Resolved, ...] = ()
    links: tuple[Link, ...] = ()
    unresolved: tuple[tuple[Observation, str], ...] = ()
    duration: float = 0.0

    @property
    def merged(self) -> int:
        """How many observations collapsed into fewer identities."""
        return sum(len(entry.observations) for entry in self.resolved) - len(self.resolved)

    def by_id(self) -> dict[str, Resolved]:
        return {entry.node_id: entry for entry in self.resolved}

    def contradictions(self) -> tuple[Resolved, ...]:
        return tuple(entry for entry in self.resolved if entry.contradicted)

    def corroborated(self) -> tuple[Resolved, ...]:
        return tuple(entry for entry in self.resolved if entry.corroborated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": [entry.to_dict() for entry in self.resolved],
            "links": [link.to_dict() for link in self.links],
            "unresolved": [
                {"subject": observation.subject, "reason": reason}
                for observation, reason in self.unresolved
            ],
            "merged": self.merged,
            "contradictions": len(self.contradictions()),
            "duration": round(self.duration, 4),
        }

    def __str__(self) -> str:
        return (
            f"{len(self.resolved)} identities from "
            f"{len(self.resolved) + self.merged} observations, "
            f"{len(self.links)} links"
            + (f", {len(self.unresolved)} unresolved" if self.unresolved else "")
        )


class Resolver:
    """Collapses observations onto canonical identities. Merges only on identity."""

    def __init__(self) -> None:
        self._identities: dict[str, str] = {}      # raw identity → canonical

    def canonicalise(self, identity: str) -> tuple[str, str, str]:
        """One identity string → (canonical, node id, coordinate).

        Purls go through normalisation so two dialects converge. URNs are
        already canonical by construction — they are minted by us — so they pass
        through, and their coordinate is themselves.
        """
        cached = self._identities.get(identity)
        text = cached or identity

        if text.startswith("pkg:"):
            try:
                parsed = canonical_purl(text)
            except InvalidPurl as error:
                raise ResolutionError(f"{identity!r}: {error}") from error
            canonical = parsed.to_string()
            self._identities[identity] = canonical
            return canonical, node_id(parsed), parsed.coordinate

        if text.startswith("urn:"):
            try:
                parsed_urn = Urn.parse(text)
            except Exception as error:  # noqa: BLE001 - Urn raises its own type
                raise ResolutionError(f"{identity!r}: {error}") from error
            rendered = parsed_urn.to_string()
            return rendered, node_id(parsed_urn), rendered

        raise ResolutionError(
            f"{identity!r} is neither a purl nor an SLPIE urn; a discoverer must "
            f"emit one or the other so the graph can hold it"
        )

    def resolve(self, observations: Iterable[Observation]) -> Resolution:
        """Collapse a batch onto identities, and turn the rest into links."""
        started = time.monotonic()
        buckets: dict[str, dict[str, Any]] = {}
        links: list[Link] = []
        unresolved: list[tuple[Observation, str]] = []

        def bucket(identity: str, observation: Observation) -> tuple[str, str] | None:
            try:
                canonical, node, coordinate = self.canonicalise(identity)
            except ResolutionError as error:
                unresolved.append((observation, str(error)))
                return None
            entry = buckets.setdefault(coordinate, {
                "identity": canonical, "node_id": node, "coordinate": coordinate,
                "kind": "", "observations": [], "versions": [], "ranges": [],
                "pinned": "",
            })
            # The most specific identity wins the display name: a pin carries a
            # version and a range does not, and a node labelled without one is
            # less useful in every view. Where two spellings are equally
            # specific — two lockfiles pinning different versions — the tie is
            # broken lexically so the label is at least deterministic. Which of
            # the two is *right* is not the resolver's call; that is what the
            # contradiction finding is for.
            current = str(entry["identity"])
            if _more_specific(canonical, current):
                entry["identity"] = canonical
                entry["node_id"] = node
            return coordinate, canonical

        for observation in observations:
            if observation.evidence is None:
                unresolved.append((observation, "no evidence was cited"))
                continue

            placed = bucket(observation.subject, observation)
            if placed is None:
                continue
            subject, subject_identity = placed

            entry = buckets[subject]
            entry["observations"].append(observation)
            entry["kind"] = entry["kind"] or str(
                observation.properties.get("node_kind") or ""
            )
            self._record_version(entry, observation, identity=subject_identity)

            if observation.kind in DECLARATIONS or not observation.object:
                continue

            placed_target = bucket(observation.object, observation)
            if placed_target is None:
                continue
            target, target_identity = placed_target
            target_entry = buckets[target]
            target_entry["observations"].append(observation)
            self._record_version(
                target_entry, observation, identity=target_identity, from_edge=True,
            )

            links.append(Link(
                kind=observation.kind,
                source=entry["node_id"],
                target=target_entry["node_id"],
                evidence=observation.evidence,
                qualifier=observation.qualifier,
                properties=dict(observation.properties),
            ))

        resolved = tuple(
            Resolved(
                identity=entry["identity"], node_id=entry["node_id"],
                coordinate=entry["coordinate"], kind=entry["kind"],
                observations=tuple(entry["observations"]),
                versions=tuple(dict.fromkeys(entry["versions"])),
                pinned=entry["pinned"], ranges=tuple(dict.fromkeys(entry["ranges"])),
            )
            for entry in buckets.values()
        )

        return Resolution(
            resolved=resolved, links=tuple(links), unresolved=tuple(unresolved),
            duration=time.monotonic() - started,
        )

    def _record_version(self, entry: dict[str, Any], observation: Observation,
                        *, identity: str, from_edge: bool = False) -> None:
        """Keep pins and ranges apart. Collapsing them loses reconciliation.

        `identity` is *this observation's* canonical form, not the bucket's
        display label. Reading the version off the bucket instead was a real
        bug: two lockfiles pinning the same package to different versions both
        recorded whichever pin happened to be labelled first, so the
        contradiction they exist to reveal silently disappeared.
        """
        from ..domain.evidence import EvidenceKind

        properties = observation.properties
        pinned = bool(properties.get("pinned")) or (
            observation.evidence is not None
            and observation.evidence.kind is EvidenceKind.LOCKFILE_PIN
        )

        value = ""
        for key in RANGE_KEYS:
            candidate = properties.get(key)
            if candidate:
                value = str(candidate)
                break
        if not value and not from_edge and "@" in identity:
            value = identity.rpartition("@")[2]
            pinned = True

        if not value:
            return
        if pinned:
            entry["versions"].append(value)
            entry["pinned"] = entry["pinned"] or value
        else:
            entry["ranges"].append(value)


def _more_specific(candidate: str, current: str) -> bool:
    """Whether one canonical spelling should label the node over another.

    A versioned identity beats an unversioned one; between two versioned ones
    the choice is arbitrary, so it is made lexically to keep node labels stable
    across runs rather than dependent on which file was read first.
    """
    if candidate == current:
        return False
    versioned = "@" in candidate
    if versioned != ("@" in current):
        return versioned
    return candidate < current


def merges_with(left: str, right: str) -> bool:
    """Whether two identities are the same thing. Identity only, never likeness.

    Exposed so the rule can be tested directly, and so nothing elsewhere is
    tempted to write its own — a second, laxer notion of "same package" is how
    a merge-on-similarity creeps back in.
    """
    if left == right:
        return True
    if left.startswith("pkg:") and right.startswith("pkg:"):
        return same_package(left, right)
    return False
