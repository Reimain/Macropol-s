"""OSV advisory matching, purl-keyed.

Advisories are **supplied to the matcher as parsed documents**. Nothing in this
module opens a socket, and nothing in this package does either: the advisory feed
is data the caller hands in, from a mirror, a cached file, or a test fixture.
That is what makes a governance run reproducible, auditable and identical whether
it fires at a simulated environment or a real one — and it is why an air-gapped
deployment is a configuration rather than a port.

Matching needs no translation layer because both sides already speak the same
identifier. OSV keys its ``affected`` entries by ecosystem and package name, from
which a Package URL falls out directly, and every node in the graph is already
identified by purl. The whole of the "which advisory applies to which package"
problem reduces to normalising both into :class:`~slpie.domain.identity.Purl` and
comparing coordinates.

Four details in the OSV format are where correctness actually lives:

* **Ranges are event streams, not intervals.** ``introduced`` opens an affected
  interval, ``fixed`` closes it *exclusively*, and ``last_affected`` closes it
  *inclusively*. A matcher that only understands ``fixed`` silently misses every
  advisory for an abandoned package, which is exactly the population that most
  needs the finding.
* **Withdrawn advisories must not fire.** A withdrawn entry is one the database
  has retracted. Reporting it is worse than reporting nothing: it burns the
  credibility that makes the real findings actionable.
* **Aliases are the same vulnerability.** GHSA-xxxx and CVE-2021-23337 are one
  advisory under two names, and a report listing both as separate findings
  doubles its own count.
* **An affected entry with neither ranges nor versions affects every version**,
  per the OSV specification. Rare, and easy to get backwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ...domain.evidence import Evidence, EvidenceKind, SourceLocation, strongest
from ...domain.finding import Finding, FindingKind, Remediation
from ...domain.identity import Purl
from ...domain.lifecycle import Severity
from ...domain.version import Version, VersionRange, parse_range
from ...errors import VersionError
from ..rules import Rule, RuleContext, RuleSet

#: OSV ecosystem → purl type. OSV writes ``PyPI``, purl writes ``pypi``; the
#: mapping is small, closed, and better stated than inferred.
ECOSYSTEMS: dict[str, str] = {
    "npm": "npm",
    "pypi": "pypi",
    "go": "golang",
    "maven": "maven",
    "crates.io": "cargo",
    "cargo": "cargo",
    "nuget": "nuget",
    "rubygems": "gem",
    "packagist": "composer",
    "hex": "hex",
    "pub": "pub",
    "hackage": "hackage",
    "cran": "cran",
    "conan": "conan",
    "swifturl": "swift",
    "github actions": "github",
    "debian": "deb",
    "ubuntu": "deb",
    "alpine": "apk",
    "rocky linux": "rpm",
    "red hat": "rpm",
    "linux": "generic",
    "oss-fuzz": "generic",
}

#: Range types whose events carry comparable versions. ``GIT`` ranges are commit
#: hashes: real, and not answerable from a version number, so they are skipped
#: rather than guessed at.
COMPARABLE_RANGE_TYPES = frozenset({"SEMVER", "ECOSYSTEM", ""})

_SEVERITIES = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "moderate": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
    "none": Severity.INFO,
}


def ecosystem_to_purl_type(ecosystem: str) -> str:
    """`Alpine:v3.14` → `apk`. The version suffix is not part of the ecosystem."""
    base = (ecosystem or "").split(":", 1)[0].strip().casefold()
    return ECOSYSTEMS.get(base, base.replace(" ", "-") or "generic")


def purl_for(ecosystem: str, name: str) -> Purl:
    """The purl an OSV package entry denotes.

    Maven writes ``group:artifact`` and npm writes ``@scope/name``; both become a
    namespace and a name, which is what makes the comparison against a graph node
    exact rather than fuzzy.
    """
    kind = ecosystem_to_purl_type(ecosystem)
    namespace, package = "", (name or "").strip()
    if kind == "maven" and ":" in package:
        namespace, _, package = package.partition(":")
    elif "/" in package:
        namespace, _, package = package.rpartition("/")
    return Purl.create(kind, package, namespace=namespace)


@dataclass(frozen=True, slots=True)
class AffectedRange:
    """One OSV range, kept as its events so the semantics stay visible."""

    type: str = "ECOSYSTEM"
    introduced: str = ""
    fixed: str = ""
    last_affected: str = ""
    ecosystem: str = "generic"

    @property
    def comparable(self) -> bool:
        return self.type.upper() in COMPARABLE_RANGE_TYPES

    @property
    def expression(self) -> str:
        """The interval as a range expression the version parser understands."""
        parts: list[str] = []
        if self.introduced and self.introduced != "0":
            parts.append(f">={self.introduced}")
        if self.fixed:
            parts.append(f"<{self.fixed}")
        elif self.last_affected:
            parts.append(f"<={self.last_affected}")
        return " ".join(parts)

    def to_range(self) -> VersionRange | None:
        """None when the range cannot be compared against a version at all."""
        if not self.comparable:
            return None
        expression = self.expression
        if not expression:
            # `introduced: 0` with no upper bound: every version, which is what
            # an unfixed vulnerability in a live package actually means.
            return VersionRange.any()
        return parse_range(expression, ecosystem=self.ecosystem)

    def contains(self, version: Version) -> bool:
        parsed = self.to_range()
        if parsed is None:
            return False
        if self.introduced and self.introduced != "0":
            try:
                if version < Version.parse(self.introduced):
                    return False
            except VersionError:
                return False
        return parsed.allows(version)

    def describe(self) -> str:
        introduced = self.introduced or "0"
        if self.fixed:
            return f"{introduced} <= affected < {self.fixed}"
        if self.last_affected:
            return f"{introduced} <= affected <= {self.last_affected} (no fix published)"
        return f"{introduced} <= affected, with no fixed or last-affected version"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type, "introduced": self.introduced,
            "fixed": self.fixed, "last_affected": self.last_affected,
        }


@dataclass(frozen=True, slots=True)
class Affected:
    """One affected package within an advisory."""

    purl: Purl
    ranges: tuple[AffectedRange, ...] = ()
    versions: tuple[str, ...] = ()
    ecosystem: str = ""

    @property
    def coordinate(self) -> str:
        return self.purl.coordinate

    @property
    def affects_every_version(self) -> bool:
        """Per the OSV specification, absent ranges *and* versions means all."""
        return not self.ranges and not self.versions

    def matched_range(self, version: Version) -> AffectedRange | None:
        return next((r for r in self.ranges if r.contains(version)), None)

    def matches_exact(self, raw: str, version: Version) -> bool:
        """The ``versions`` list, compared both literally and after parsing.

        `1.0` and `1.0.0` are the same release written two ways, and an advisory
        that listed one must still match a lockfile that pinned the other.
        """
        if raw in self.versions:
            return True
        for candidate in self.versions:
            try:
                if Version.parse(candidate) == version:
                    return True
            except VersionError:
                continue
        return False

    @property
    def fixed_versions(self) -> tuple[str, ...]:
        return tuple(r.fixed for r in self.ranges if r.fixed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.purl.to_string(),
            "ranges": [r.to_dict() for r in self.ranges],
            "versions": list(self.versions),
        }


@dataclass(frozen=True, slots=True)
class Advisory:
    """One OSV document, parsed into the parts matching actually needs."""

    id: str
    affected: tuple[Affected, ...] = ()
    aliases: tuple[str, ...] = ()
    summary: str = ""
    details: str = ""
    withdrawn: str = ""
    published: str = ""
    modified: str = ""
    severity: Severity = Severity.HIGH
    references: tuple[str, ...] = ()
    source: str = ""

    @property
    def is_withdrawn(self) -> bool:
        """A retracted advisory. It never fires, at any severity."""
        return bool(self.withdrawn)

    @property
    def names(self) -> tuple[str, ...]:
        """Every identifier this advisory answers to — its id and its aliases."""
        return (self.id, *self.aliases)

    @property
    def cve(self) -> str:
        """The CVE among the identifiers, if there is one. Reports lead with it."""
        return next((n for n in self.names if n.upper().startswith("CVE-")), "")

    def affecting(self, purl: Purl) -> Affected | None:
        coordinate = purl.without_version().coordinate
        return next(
            (a for a in self.affected if a.coordinate == coordinate), None
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "") -> "Advisory":
        affected: list[Affected] = []
        for entry in data.get("affected") or ():
            package = entry.get("package") or {}
            ecosystem = str(package.get("ecosystem", ""))
            declared_purl = package.get("purl")
            if declared_purl:
                identity = Purl.parse(str(declared_purl)).without_version()
            else:
                identity = purl_for(ecosystem, str(package.get("name", "")))
            affected.append(Affected(
                purl=identity,
                ranges=tuple(_ranges(entry.get("ranges") or (), ecosystem)),
                versions=tuple(str(v) for v in entry.get("versions") or ()),
                ecosystem=ecosystem,
            ))

        return cls(
            id=str(data.get("id", "")),
            affected=tuple(affected),
            aliases=tuple(str(a) for a in data.get("aliases") or ()),
            summary=str(data.get("summary", "")),
            details=str(data.get("details", "")),
            withdrawn=str(data.get("withdrawn", "") or ""),
            published=str(data.get("published", "")),
            modified=str(data.get("modified", "")),
            severity=_severity_of(data),
            references=tuple(
                str(r.get("url", "")) for r in data.get("references") or ()
                if isinstance(r, Mapping)
            ),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "aliases": list(self.aliases), "summary": self.summary,
            "withdrawn": self.withdrawn, "severity": self.severity.value,
            "affected": [a.to_dict() for a in self.affected],
        }

    def __str__(self) -> str:
        return f"{self.id}: {self.summary or 'no summary'}"


def _ranges(entries: Iterable[Any], ecosystem: str) -> Iterator[AffectedRange]:
    """Fold each OSV event stream into the intervals it denotes.

    Events arrive as a flat list — ``introduced``, then ``fixed`` or
    ``last_affected``, possibly repeating. One range object can therefore carry
    several disjoint affected intervals, and flattening them into a single
    lower/upper pair would widen the range across the versions in between.
    """
    purl_ecosystem = ecosystem_to_purl_type(ecosystem)
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        range_type = str(entry.get("type", "ECOSYSTEM"))
        introduced = ""
        opened = False
        for event in entry.get("events") or ():
            if not isinstance(event, Mapping):
                continue
            if "introduced" in event:
                if opened:
                    yield AffectedRange(
                        type=range_type, introduced=introduced,
                        ecosystem=purl_ecosystem,
                    )
                introduced = str(event["introduced"])
                opened = True
            elif "fixed" in event:
                yield AffectedRange(
                    type=range_type, introduced=introduced,
                    fixed=str(event["fixed"]), ecosystem=purl_ecosystem,
                )
                introduced, opened = "", False
            elif "last_affected" in event:
                yield AffectedRange(
                    type=range_type, introduced=introduced,
                    last_affected=str(event["last_affected"]),
                    ecosystem=purl_ecosystem,
                )
                introduced, opened = "", False
        if opened:
            yield AffectedRange(
                type=range_type, introduced=introduced, ecosystem=purl_ecosystem,
            )


def _severity_of(data: Mapping[str, Any]) -> Severity:
    """The advisory's severity, from whichever field the feed used.

    CVSS *vectors* are deliberately not scored here. Turning
    ``CVSS:3.1/AV:N/AC:L/...`` into 9.8 is a scoring engine, and one embedded in a
    matcher would be the least-reviewed CVSS implementation in the industry. A
    feed that states a severity is believed; one that does not defaults to HIGH,
    because under-rating an unknown vulnerability is the more expensive mistake.
    """
    for source in (data.get("database_specific"), data.get("ecosystem_specific")):
        if isinstance(source, Mapping):
            stated = str(source.get("severity", "")).casefold()
            if stated in _SEVERITIES:
                return _SEVERITIES[stated]
    for entry in data.get("severity") or ():
        if isinstance(entry, Mapping):
            score = entry.get("score")
            if isinstance(score, (int, float)):
                return _from_score(float(score))
            if isinstance(score, str) and str(score).casefold() in _SEVERITIES:
                return _SEVERITIES[str(score).casefold()]
    return Severity.HIGH


def _from_score(score: float) -> Severity:
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    return Severity.LOW


@dataclass(frozen=True, slots=True)
class AdvisoryMatch:
    """One advisory that applies to one concrete package version."""

    advisory: Advisory
    affected: Affected
    purl: Purl
    reason: str
    matched_range: AffectedRange | None = None

    @property
    def fixed_in(self) -> str:
        if self.matched_range and self.matched_range.fixed:
            return self.matched_range.fixed
        fixes = self.affected.fixed_versions
        return fixes[0] if fixes else ""

    @property
    def code(self) -> str:
        """Report under the CVE when there is one, the database id otherwise."""
        return self.advisory.cve or self.advisory.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "advisory": self.advisory.id, "aliases": list(self.advisory.aliases),
            "purl": self.purl.to_string(), "reason": self.reason,
            "fixed_in": self.fixed_in, "severity": self.advisory.severity.value,
        }


class AdvisoryDatabase:
    """Advisories in memory, indexed by package coordinate and by alias.

    Construction takes documents. There is no ``fetch``, no ``update`` and no URL
    anywhere in this class — supplying the data is the caller's job, which keeps
    a governance run offline, deterministic, and replayable from the ledger.
    """

    def __init__(self, advisories: Iterable[Advisory] = ()) -> None:
        self._advisories: dict[str, Advisory] = {}
        self._by_coordinate: dict[str, list[Advisory]] = {}
        self._by_alias: dict[str, Advisory] = {}
        for advisory in advisories:
            self.add(advisory)

    # -- loading ---------------------------------------------------------

    def add(self, advisory: Advisory) -> Advisory:
        self._advisories[advisory.id] = advisory
        for name in advisory.names:
            self._by_alias[name.upper()] = advisory
        for affected in advisory.affected:
            self._by_coordinate.setdefault(affected.coordinate, []).append(advisory)
        return advisory

    @classmethod
    def from_documents(
        cls, documents: Iterable[Mapping[str, Any]], *, source: str = ""
    ) -> "AdvisoryDatabase":
        return cls(Advisory.from_dict(d, source=source) for d in documents)

    @classmethod
    def from_directory(cls, path: str | Path) -> "AdvisoryDatabase":
        """Load an OSV mirror already on disk. A read, never a fetch."""
        root = Path(path)
        database = cls()
        for file in sorted(root.rglob("*.json")):
            try:
                document = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt file in a mirror of forty thousand is not a reason to
                # have no advisory data at all.
                continue
            if isinstance(document, Mapping):
                database.add(Advisory.from_dict(document, source=file.as_uri()))
            elif isinstance(document, list):
                for entry in document:
                    if isinstance(entry, Mapping):
                        database.add(Advisory.from_dict(entry, source=file.as_uri()))
        return database

    # -- lookup ----------------------------------------------------------

    def by_alias(self, name: str) -> Advisory | None:
        """Resolve GHSA or CVE to the same advisory — they are one vulnerability."""
        return self._by_alias.get((name or "").upper())

    def matching(self, purl: Purl | str) -> tuple[AdvisoryMatch, ...]:
        """Every advisory that applies to this exact package version.

        Withdrawn advisories are filtered here, once, so no caller can forget.
        """
        identity = purl if isinstance(purl, Purl) else Purl.parse(purl)
        coordinate = identity.without_version().coordinate
        candidates = self._by_coordinate.get(coordinate, ())
        if not candidates or not identity.version:
            return ()

        try:
            version = Version.parse(identity.version)
        except VersionError:
            # An unparseable version cannot be placed in or out of a range, and
            # guessing in either direction is worse than reporting nothing.
            return ()

        matches: dict[str, AdvisoryMatch] = {}
        for advisory in candidates:
            if advisory.is_withdrawn:
                continue
            affected = advisory.affecting(identity)
            if affected is None:
                continue
            match = _match(advisory, affected, identity, version)
            if match is not None:
                matches.setdefault(advisory.id, match)
        return tuple(matches.values())

    @property
    def advisories(self) -> tuple[Advisory, ...]:
        return tuple(self._advisories.values())

    @property
    def withdrawn(self) -> tuple[Advisory, ...]:
        return tuple(a for a in self._advisories.values() if a.is_withdrawn)

    def __len__(self) -> int:
        return len(self._advisories)

    def __iter__(self) -> Iterator[Advisory]:
        return iter(self._advisories.values())

    def __contains__(self, name: object) -> bool:
        return str(name).upper() in self._by_alias

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<AdvisoryDatabase {len(self._advisories)} advisories>"


def _match(
    advisory: Advisory, affected: Affected, purl: Purl, version: Version
) -> AdvisoryMatch | None:
    if affected.matches_exact(purl.version, version):
        return AdvisoryMatch(
            advisory=advisory, affected=affected, purl=purl,
            reason=f"{purl.version} is listed explicitly as an affected version",
        )
    matched = affected.matched_range(version)
    if matched is not None:
        return AdvisoryMatch(
            advisory=advisory, affected=affected, purl=purl,
            reason=f"{purl.version} falls in the affected range {matched.describe()}",
            matched_range=matched,
        )
    if affected.affects_every_version:
        return AdvisoryMatch(
            advisory=advisory, affected=affected, purl=purl,
            reason="the advisory names no ranges or versions, so every version is affected",
        )
    return None


# --- the rule ------------------------------------------------------------


def advisory_evidence(match: AdvisoryMatch) -> Evidence:
    """The advisory document, cited as the reference it is.

    ``CONFIG_REFERENCE`` rather than an observation kind: the advisory is a
    third-party assertion about a version range, not something SLPIE watched
    happen. The evidence that the *package* is present comes from the node, and
    the two together are what the finding rests on.
    """
    return Evidence(
        kind=EvidenceKind.CONFIG_REFERENCE,
        location=SourceLocation(
            match.advisory.source or f"osv://{match.advisory.id}"
        ),
        extractor="governance.security.advisories",
        extractor_version="1",
        excerpt=f"{match.advisory.id}: {match.reason}",
        labels={
            "advisory": match.advisory.id,
            "aliases": ", ".join(match.advisory.aliases),
        },
    )


def advisory_rule(database: AdvisoryDatabase) -> Rule:
    """A rule that reports every known vulnerability in the graph's packages."""

    def matches(context: RuleContext) -> bool:
        return context.graph is not None and len(database) > 0

    def evaluate(context: RuleContext) -> list[Finding]:
        raised: list[Finding] = []
        for node in context.nodes(live=True):
            identity = node.identity
            if not isinstance(identity, Purl) or not identity.version:
                continue
            for match in database.matching(identity):
                raised.append(_finding(node, match))
        return raised

    return Rule(
        id="security.vulnerable-dependency",
        title="a dependency has a known vulnerability",
        kind=FindingKind.VULNERABLE_DEPENDENCY,
        severity=Severity.HIGH,
        evaluate=evaluate,
        matches=matches,
        remediation="upgrade to a fixed version, or remove the dependency",
        description="matches graph packages against a supplied OSV advisory feed",
        tags=("security", "osv"),
    )


def _finding(node: Any, match: AdvisoryMatch) -> Finding:
    advisory = match.advisory
    fixed = match.fixed_in
    if fixed:
        remediation = Remediation(
            summary=f"upgrade {node.name} to {fixed} or later",
            action="upgrade", target=fixed,
            breaking=_is_major_change(node.version, fixed),
        )
    else:
        remediation = Remediation(
            summary=(
                f"no fixed version has been published for {advisory.id}; "
                f"pin away from the affected range, isolate the dependency, "
                f"or replace it"
            ),
            action="replace", target=node.name,
        )

    aliases = ", ".join(n for n in advisory.names if n != match.code)
    return Finding(
        kind=FindingKind.VULNERABLE_DEPENDENCY,
        severity=advisory.severity,
        subject=node.id,
        title=f"{node.display} is affected by {match.code}",
        detail=(
            f"{advisory.summary or advisory.id}. {match.reason}."
            + (f" Also known as {aliases}." if aliases else "")
            + (f" Fixed in {fixed}." if fixed else " No fix has been published.")
        ),
        code=match.code,
        evidence=(advisory_evidence(match), strongest(node.evidence)),
        remediation=remediation,
        rule_id="security.vulnerable-dependency",
        related=(),
        properties={
            "advisory": advisory.id,
            "aliases": list(advisory.aliases),
            "fixed_in": fixed,
            "affected_range": match.matched_range.describe() if match.matched_range else "",
            "references": list(advisory.references),
        },
    )


def _is_major_change(current: str, target: str) -> bool:
    try:
        return Version.parse(current).major != Version.parse(target).major
    except VersionError:
        return False


def advisory_rules(database: AdvisoryDatabase) -> RuleSet:
    """The advisory rule family, as a set for registration."""
    return RuleSet((advisory_rule(database),), name="security.advisories")
