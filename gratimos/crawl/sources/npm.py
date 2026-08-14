"""npm, through the registry's own endpoints.

Unlike PyPI, npm publishes a real search API — `/-/v1/search` on
`registry.npmjs.org` — so this adapter does not have to guess names. It also
returns npm's own quality/popularity/maintenance scores, which are worth
keeping: they are computed from data the crawler cannot see (download curves,
issue response times), so discarding them and re-deriving a worse signal from
what is visible would be strictly worse.

They are kept as *evidence*, not as the answer. npm's `score.final` is a
popularity-weighted number and popularity is not fitness for a stated need; the
reuse ranker treats it as one input among several rather than as the ranking.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..fetch import Fetcher
from ..source import OFFICIAL, Artifact, Origin, SourceError

BASE = "https://registry.npmjs.org"

#: npm caps its search page size here; asking for more returns this anyway.
MAX_PAGE = 250


def _timestamp(text: str) -> float | None:
    if not text:
        return None
    try:
        moment = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


def _repository(value: Any) -> str:
    """npm's `repository` is a string in old packages and an object in new ones."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(value.get("url") or "")
    return ""


def _licence(value: Any) -> str:
    """Likewise `license`, which was an object with a `type` before SPDX."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("type") or "").strip()
    if isinstance(value, list) and value:
        # The legacy `licenses: [{type: …}]` array. An OR expression is the
        # honest reading: the publisher offered a choice.
        parts = [_licence(item) for item in value]
        chosen = [p for p in parts if p]
        return " OR ".join(chosen) if len(chosen) > 1 else (chosen[0] if chosen else "")
    return ""


class NpmSource:
    """Searches and describes npm packages from the official registry."""

    name = "npm"
    ecosystem = "npm"

    def __init__(self, fetcher: Fetcher, *, base: str = BASE) -> None:
        self.fetcher = fetcher
        self.base = base.rstrip("/")

    # -- the two questions ------------------------------------------------

    def search(self, terms: Sequence[str], *, limit: int = 20) -> Sequence[Artifact]:
        query = " ".join(t.strip() for t in terms if t.strip())
        if not query:
            return ()
        size = max(1, min(limit, MAX_PAGE))
        url = f"{self.base}/-/v1/search?text={quote(query)}&size={size}"
        response = self.fetcher.try_get(url)
        if response is None or not response.ok:
            return ()
        try:
            payload = response.json()
        except Exception:
            return ()
        objects = payload.get("objects") if isinstance(payload, Mapping) else None
        if not isinstance(objects, list):
            return ()

        origin = Origin(
            source=self.name, url=url, fetched_at=response.elapsed,
            api=OFFICIAL, from_cache=response.from_cache,
        )
        found: list[Artifact] = []
        for entry in objects[:size]:
            if not isinstance(entry, Mapping):
                continue
            package = entry.get("package")
            if not isinstance(package, Mapping) or not package.get("name"):
                continue
            found.append(self._from_search(package, entry, origin))
        return tuple(found)

    def describe(self, name: str) -> Artifact | None:
        url = f"{self.base}/{quote(name, safe='@/')}"
        response = self.fetcher.try_get(url)
        if response is None or not response.ok:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, Mapping) or not payload.get("name"):
            return None
        return self._from_packument(
            payload,
            Origin(
                source=self.name, url=url, fetched_at=response.elapsed,
                api=OFFICIAL, from_cache=response.from_cache,
            ),
        )

    # -- normalisation ----------------------------------------------------

    def _from_search(
        self,
        package: Mapping[str, Any],
        entry: Mapping[str, Any],
        origin: Origin,
    ) -> Artifact:
        score = entry.get("score")
        detail = score.get("detail") if isinstance(score, Mapping) else None
        return Artifact(
            ecosystem=self.ecosystem,
            name=str(package["name"]),
            version=str(package.get("version") or ""),
            summary=str(package.get("description") or ""),
            licence=_licence(package.get("license")),
            homepage=str(package.get("links", {}).get("homepage") or "")
            if isinstance(package.get("links"), Mapping) else "",
            repository=str(package.get("links", {}).get("repository") or "")
            if isinstance(package.get("links"), Mapping) else "",
            keywords=tuple(str(k).lower() for k in (package.get("keywords") or [])),
            released_at=_timestamp(str(package.get("date") or "")),
            origin=origin,
            raw={
                "search_score": (
                    score.get("final") if isinstance(score, Mapping) else None
                ),
                "quality": detail.get("quality") if isinstance(detail, Mapping) else None,
                "popularity": detail.get("popularity") if isinstance(detail, Mapping) else None,
                "maintenance": detail.get("maintenance") if isinstance(detail, Mapping) else None,
            },
        )

    def _from_packument(self, payload: Mapping[str, Any], origin: Origin) -> Artifact:
        tags = payload.get("dist-tags")
        latest = str(tags.get("latest") or "") if isinstance(tags, Mapping) else ""
        versions = payload.get("versions")
        manifest: Mapping[str, Any] = {}
        if isinstance(versions, Mapping) and latest in versions:
            candidate = versions[latest]
            if isinstance(candidate, Mapping):
                manifest = candidate

        times = payload.get("time")
        released_at = None
        if isinstance(times, Mapping) and latest:
            released_at = _timestamp(str(times.get(latest) or ""))

        engines = manifest.get("engines")
        runtime = ""
        if isinstance(engines, Mapping) and engines.get("node"):
            runtime = f"node{engines['node']}"

        deprecated = bool(manifest.get("deprecated")) or bool(payload.get("deprecated"))

        return Artifact(
            ecosystem=self.ecosystem,
            name=str(payload["name"]),
            version=latest,
            summary=str(manifest.get("description") or payload.get("description") or ""),
            licence=_licence(manifest.get("license") or payload.get("license")
                             or payload.get("licenses")),
            homepage=str(manifest.get("homepage") or payload.get("homepage") or ""),
            repository=_repository(manifest.get("repository") or payload.get("repository")),
            keywords=tuple(
                str(k).lower()
                for k in (manifest.get("keywords") or payload.get("keywords") or [])
            ),
            requires_runtime=runtime,
            dependencies=tuple(sorted(manifest.get("dependencies") or {})),
            released_at=released_at,
            deprecated=deprecated,
            origin=origin,
            raw={"dist_tags": dict(tags) if isinstance(tags, Mapping) else {}},
        )

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<NpmSource {self.base}>"
