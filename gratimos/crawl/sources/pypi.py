"""PyPI, through its documented JSON API.

`https://pypi.org/pypi/<name>/json` is a public, stable, cache-friendly
endpoint and it is the only one this adapter uses for description. That is a
constraint worth stating: PyPI has no supported search API — the XML-RPC one
was withdrawn precisely because crawlers abused it — so `search()` here does
**not** scrape the web search page. It resolves concept terms into plausible
distribution names and asks for each by name.

That is slower per hit and dramatically more polite, and it degrades honestly:
a term that names no package returns nothing rather than returning whatever a
scraped results page happened to rank first. Anything better needs an index
(PyPI's simple index, or a local mirror), which is a different component than a
crawler and is where it belongs.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Mapping, Sequence

from ..fetch import Fetcher
from ..source import OFFICIAL, Artifact, Origin, SourceError

BASE = "https://pypi.org"

#: Classifier prefix carrying the licence when the metadata field is empty,
#: which is the common case for anything packaged before PEP 639.
_LICENCE_CLASSIFIER = "License :: "

#: SPDX identifiers for the classifiers that actually appear. Deliberately
#: incomplete — an unmapped classifier yields an empty licence, which the reuse
#: gate treats as unknown and therefore blocking, rather than a guess that
#: happens to be permissive.
_CLASSIFIER_SPDX: dict[str, str] = {
    "OSI Approved :: MIT License": "MIT",
    "OSI Approved :: BSD License": "BSD-3-Clause",
    "OSI Approved :: Apache Software License": "Apache-2.0",
    "OSI Approved :: ISC License (ISCL)": "ISC",
    "OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
    "OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "OSI Approved :: Python Software Foundation License": "PSF-2.0",
}

#: How a concept term is turned into names worth asking for. Ordered, because
#: the first form that resolves wins and the bare term is the likeliest.
_NAME_FORMS = ("{term}", "py{term}", "python-{term}", "{term}-py", "{term}s")


def _timestamp(text: str) -> float | None:
    if not text:
        return None
    try:
        cleaned = text.replace("Z", "+00:00")
        moment = _dt.datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.timestamp()


class PyPISource:
    """Describes Python distributions from the official JSON endpoint."""

    name = "pypi"
    ecosystem = "pypi"

    def __init__(self, fetcher: Fetcher, *, base: str = BASE) -> None:
        self.fetcher = fetcher
        self.base = base.rstrip("/")

    # -- the two questions ------------------------------------------------

    def describe(self, name: str) -> Artifact | None:
        url = f"{self.base}/pypi/{name}/json"
        response = self.fetcher.try_get(url)
        if response is None or not response.ok:
            return None
        try:
            payload = response.json()
        except SourceError:
            return None
        except Exception:
            return None
        return self._artifact(
            payload,
            Origin(
                source=self.name, url=url, fetched_at=response.elapsed,
                api=OFFICIAL, from_cache=response.from_cache,
            ),
        )

    def search(self, terms: Sequence[str], *, limit: int = 20) -> Sequence[Artifact]:
        """Resolve concept terms to distributions by asking for each by name."""
        found: list[Artifact] = []
        seen: set[str] = set()
        for candidate in self.candidate_names(terms):
            if len(found) >= limit:
                break
            if candidate in seen:
                continue
            seen.add(candidate)
            artifact = self.describe(candidate)
            if artifact is not None:
                found.append(artifact)
        return tuple(found)

    def candidate_names(self, terms: Sequence[str]) -> tuple[str, ...]:
        """Concept terms → plausible distribution names, most likely first.

        Kept public and pure so the guessing is inspectable: when a crawl finds
        nothing, the question "what did it actually ask for?" has an answer that
        does not require reading a log.
        """
        names: list[str] = []
        for term in terms:
            cleaned = term.strip().lower().replace(" ", "-").replace("_", "-")
            if not cleaned:
                continue
            for form in _NAME_FORMS:
                name = form.format(term=cleaned)
                if name not in names:
                    names.append(name)
        return tuple(names)

    # -- normalisation ----------------------------------------------------

    def _artifact(self, payload: Mapping[str, Any], origin: Origin) -> Artifact:
        info = payload.get("info") or {}
        if not isinstance(info, Mapping) or not info.get("name"):
            raise SourceError(f"{origin.url} did not return a PyPI info object")

        version = str(info.get("version") or "")
        classifiers = tuple(str(c) for c in (info.get("classifiers") or []))

        released_at = None
        yanked = False
        releases = payload.get("urls")
        if isinstance(releases, list) and releases:
            first = releases[0]
            if isinstance(first, Mapping):
                released_at = _timestamp(str(first.get("upload_time_iso_8601") or ""))
                yanked = bool(first.get("yanked"))

        return Artifact(
            ecosystem=self.ecosystem,
            name=str(info["name"]),
            version=version,
            summary=str(info.get("summary") or ""),
            licence=self._licence(info, classifiers),
            homepage=str(info.get("home_page") or self._project_url(info) or ""),
            repository=self._repository(info),
            keywords=self._keywords(info, classifiers),
            requires_runtime=str(info.get("requires_python") or ""),
            dependencies=tuple(str(d) for d in (info.get("requires_dist") or [])),
            released_at=released_at,
            deprecated=self._deprecated(info, classifiers),
            yanked=yanked,
            origin=origin,
            raw={"info": dict(info)},
        )

    def _licence(self, info: Mapping[str, Any], classifiers: Sequence[str]) -> str:
        expression = str(info.get("license_expression") or "").strip()
        if expression:
            return expression
        for classifier in classifiers:
            if classifier.startswith(_LICENCE_CLASSIFIER):
                spdx = _CLASSIFIER_SPDX.get(classifier[len(_LICENCE_CLASSIFIER):])
                if spdx:
                    return spdx
        # The free-text field last, and only when short: a `license` field
        # holding the entire licence text is common, and passing 30KB of prose
        # to an SPDX parser produces a confident-looking nonsense answer.
        declared = str(info.get("license") or "").strip()
        return declared if 0 < len(declared) <= 64 else ""

    def _project_url(self, info: Mapping[str, Any]) -> str:
        urls = info.get("project_urls")
        if not isinstance(urls, Mapping):
            return ""
        for key in ("Homepage", "homepage", "Documentation"):
            if urls.get(key):
                return str(urls[key])
        return ""

    def _repository(self, info: Mapping[str, Any]) -> str:
        urls = info.get("project_urls")
        if isinstance(urls, Mapping):
            for key in ("Source", "Source Code", "Repository", "Code", "GitHub"):
                if urls.get(key):
                    return str(urls[key])
        home = str(info.get("home_page") or "")
        return home if "github.com" in home or "gitlab.com" in home else ""

    def _keywords(self, info: Mapping[str, Any], classifiers: Sequence[str]) -> tuple[str, ...]:
        raw = info.get("keywords") or ""
        if isinstance(raw, list):
            words = [str(w) for w in raw]
        else:
            words = [w for w in str(raw).replace(",", " ").split() if w]
        topics = [
            c.split("::")[-1].strip()
            for c in classifiers
            if c.startswith("Topic ::")
        ]
        seen: list[str] = []
        for word in (*words, *topics):
            cleaned = word.strip().lower()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen)

    def _deprecated(self, info: Mapping[str, Any], classifiers: Sequence[str]) -> bool:
        if any(c.startswith("Development Status :: 7") for c in classifiers):
            return True
        summary = str(info.get("summary") or "").lower()
        return "deprecated" in summary or "no longer maintained" in summary

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<PyPISource {self.base}>"
