"""What a crawled package looks like once the registry's dialect is gone.

Every registry describes a package differently — PyPI has `info.summary` and
classifiers, npm has `description` and `keywords`, GitHub has a topic list and
no version at all. `Artifact` is the shape they all normalise into, so
everything downstream reasons about packages rather than about JSON.

Two properties are carried deliberately and are easy to lose:

* **Provenance on every artifact.** Which source said this, at which URL, when,
  and whether that was an official API or a guess. An artifact whose origin is
  unrecorded cannot be re-checked, and a reuse recommendation nobody can
  re-check is a recommendation nobody should act on.
* **Absence distinguished from zero.** `stars=None` means "this source does not
  report stars"; `stars=0` means "nobody has starred it". Collapsing the two
  makes an unmeasured package look unpopular, which is exactly the error that
  buries a good, quiet library.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import quote

from .policy import CrawlError


class SourceError(CrawlError):
    """A source could not answer, or answered in a shape it does not promise."""


#: Registries whose data comes from a documented, versioned API. Anything else
#: is scraped, and the distinction travels with the artifact so ranking can
#: discount it rather than treating a parsed web page as equal to an API.
OFFICIAL = "official"
SCRAPED = "scraped"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where one artifact came from, precisely enough to go back and check."""

    source: str
    url: str
    fetched_at: float = 0.0
    api: str = OFFICIAL
    from_cache: bool = False

    @property
    def official(self) -> bool:
        return self.api == OFFICIAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "url": self.url,
            "fetched_at": self.fetched_at, "api": self.api,
            "from_cache": self.from_cache,
        }

    def __str__(self) -> str:
        return f"{self.source} <{self.url}>"


@dataclass(frozen=True, slots=True)
class Artifact:
    """One reusable thing, as a source described it."""

    ecosystem: str
    name: str
    version: str = ""
    summary: str = ""
    licence: str = ""
    homepage: str = ""
    repository: str = ""
    keywords: tuple[str, ...] = ()
    requires_runtime: str = ""
    dependencies: tuple[str, ...] = ()
    downloads: int | None = None
    stars: int | None = None
    released_at: float | None = None
    deprecated: bool = False
    yanked: bool = False
    origin: Origin | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SourceError("an artifact must be named")
        if not self.ecosystem.strip():
            raise SourceError(f"artifact {self.name!r} has no ecosystem")

    @property
    def purl(self) -> str:
        """Package URL — the identity SBOM tools and advisory feeds already use.

        Emitted rather than invented so a crawled candidate can be matched
        against an OSV advisory or a CycloneDX component without a translation
        table in between.
        """
        namespace, _, bare = self.name.rpartition("/")
        parts = f"pkg:{self.ecosystem}/"
        if namespace:
            parts += quote(namespace, safe="") + "/"
        parts += quote(bare, safe="")
        return parts + (f"@{self.version}" if self.version else "")

    @property
    def described(self) -> bool:
        """Whether there is enough here to judge it at all."""
        return bool(self.summary or self.keywords or self.repository)

    @property
    def age_days(self) -> float | None:
        if self.released_at is None:
            return None
        return max((time.time() - self.released_at) / 86400.0, 0.0)

    def text(self) -> str:
        """Everything a concept extractor should look at, concatenated once."""
        return " ".join(
            part for part in (self.name.replace("-", " ").replace("_", " "),
                              self.summary, " ".join(self.keywords))
            if part
        )

    def merge(self, other: "Artifact") -> "Artifact":
        """Combine two sources' views, preferring whichever actually knows.

        A GitHub record has stars and no version; a PyPI record has the version
        and no stars. Merging is how one candidate ends up with both, and the
        rule is simply that a stated value beats an absent one — never that the
        later source wins, which would make the result depend on crawl order.
        """
        if other.name != self.name or other.ecosystem != self.ecosystem:
            raise SourceError(
                f"refusing to merge {self.purl} with {other.purl}: different artifacts"
            )
        return Artifact(
            ecosystem=self.ecosystem,
            name=self.name,
            version=self.version or other.version,
            summary=self.summary or other.summary,
            licence=self.licence or other.licence,
            homepage=self.homepage or other.homepage,
            repository=self.repository or other.repository,
            keywords=self.keywords or other.keywords,
            requires_runtime=self.requires_runtime or other.requires_runtime,
            dependencies=self.dependencies or other.dependencies,
            downloads=self.downloads if self.downloads is not None else other.downloads,
            stars=self.stars if self.stars is not None else other.stars,
            released_at=(
                self.released_at if self.released_at is not None else other.released_at
            ),
            deprecated=self.deprecated or other.deprecated,
            yanked=self.yanked or other.yanked,
            origin=self.origin or other.origin,
            raw={**dict(other.raw), **dict(self.raw)},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "purl": self.purl,
            "ecosystem": self.ecosystem,
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "licence": self.licence,
            "homepage": self.homepage,
            "repository": self.repository,
            "keywords": list(self.keywords),
            "requires_runtime": self.requires_runtime,
            "dependencies": list(self.dependencies),
            "downloads": self.downloads,
            "stars": self.stars,
            "released_at": self.released_at,
            "deprecated": self.deprecated,
            "yanked": self.yanked,
            "origin": self.origin.to_dict() if self.origin else None,
        }

    def __str__(self) -> str:
        return self.purl


@runtime_checkable
class Source(Protocol):
    """One registry, behind the two questions the crawler asks of any of them."""

    name: str
    ecosystem: str

    def search(self, terms: Sequence[str], *, limit: int = 20) -> Sequence[Artifact]:
        """Candidates matching these concept terms, best first."""
        ...

    def describe(self, name: str) -> Artifact | None:
        """Everything known about one named package, or None if absent."""
        ...


class SourceRegistry:
    """The sources a crawl may consult, addressable by name or ecosystem."""

    def __init__(self, *sources: Source) -> None:
        self._sources: dict[str, Source] = {}
        for source in sources:
            self.register(source)

    def register(self, source: Source) -> None:
        if source.name in self._sources:
            raise SourceError(f"source {source.name!r} is already registered")
        self._sources[source.name] = source

    def get(self, name: str) -> Source | None:
        return self._sources.get(name)

    def for_ecosystem(self, ecosystem: str) -> tuple[Source, ...]:
        return tuple(s for s in self._sources.values() if s.ecosystem == ecosystem)

    def ecosystems(self) -> tuple[str, ...]:
        return tuple(sorted({s.ecosystem for s in self._sources.values()}))

    def __len__(self) -> int:
        return len(self._sources)

    def __iter__(self):
        return iter(tuple(self._sources.values()))

    def __contains__(self, name: object) -> bool:
        return name in self._sources

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SourceRegistry {sorted(self._sources)}>"
