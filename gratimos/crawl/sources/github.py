"""GitHub, through the REST API — repositories rather than packages.

The other two sources answer "what is published?". This one answers "who
maintains it, and is it alive?", which is the question that decides whether a
technically-suitable library is a good idea. Stars, archived status, last push
and open-issue count are not available from a package registry at all.

Two things are handled carefully:

* **Authentication is optional and never invented.** With `GITHUB_TOKEN` set the
  rate limit is 5000/hour; without it, 60. The adapter reads the environment if
  a token was not passed, and otherwise proceeds unauthenticated rather than
  failing — a reduced rate limit is a smaller problem than a crawler that will
  not start.
* **The rate-limit headers are read and reported.** GitHub tells you exactly how
  much budget is left; ignoring that and discovering the limit by being blocked
  is the impolite way to find out.

`search()` uses the repository search endpoint, which is documented, rate-limited
separately, and returns a ranked list — so unlike PyPI there is no name guessing
here.
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..fetch import Fetcher
from ..source import OFFICIAL, Artifact, Origin

BASE = "https://api.github.com"

#: GitHub versions its REST API by header, and pinning it is the difference
#: between a stable adapter and one that breaks on a Tuesday.
API_VERSION = "2022-11-28"

#: The search endpoint refuses anything above this per page.
MAX_PAGE = 100

_SPDX_UNKNOWN = frozenset({"", "noassertion", "other"})


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


class GitHubSource:
    """Describes repositories, so a candidate can be judged on its upkeep."""

    name = "github"
    ecosystem = "github"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        base: str = BASE,
        token: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.base = base.rstrip("/")
        env = environ if environ is not None else os.environ
        self.token = token if token is not None else env.get("GITHUB_TOKEN", "")
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: int | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _note_limits(self, response: Any) -> None:
        remaining = response.header("x-ratelimit-remaining")
        reset = response.header("x-ratelimit-reset")
        if remaining.isdigit():
            self.rate_limit_remaining = int(remaining)
        if reset.isdigit():
            self.rate_limit_reset = int(reset)

    # -- the two questions ------------------------------------------------

    def search(self, terms: Sequence[str], *, limit: int = 20) -> Sequence[Artifact]:
        query = " ".join(t.strip() for t in terms if t.strip())
        if not query:
            return ()
        size = max(1, min(limit, MAX_PAGE))
        url = (
            f"{self.base}/search/repositories"
            f"?q={quote(query)}&sort=stars&order=desc&per_page={size}"
        )
        response = self.fetcher.try_get(url, headers=self._headers())
        if response is None or not response.ok:
            return ()
        self._note_limits(response)
        try:
            payload = response.json()
        except Exception:
            return ()
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            return ()

        origin = Origin(
            source=self.name, url=url, fetched_at=response.elapsed,
            api=OFFICIAL, from_cache=response.from_cache,
        )
        return tuple(
            self._artifact(item, origin)
            for item in items[:size]
            if isinstance(item, Mapping) and item.get("full_name")
        )

    def describe(self, name: str) -> Artifact | None:
        """`name` is `owner/repo`, or a URL one can be recovered from."""
        slug = self.slug(name)
        if not slug:
            return None
        url = f"{self.base}/repos/{slug}"
        response = self.fetcher.try_get(url, headers=self._headers())
        if response is None or not response.ok:
            return None
        self._note_limits(response)
        try:
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, Mapping) or not payload.get("full_name"):
            return None
        return self._artifact(
            payload,
            Origin(
                source=self.name, url=url, fetched_at=response.elapsed,
                api=OFFICIAL, from_cache=response.from_cache,
            ),
        )

    @staticmethod
    def slug(reference: str) -> str:
        """`owner/repo` out of anything that carries one, or empty.

        Package registries record repository links in half a dozen shapes —
        `git+https://github.com/o/r.git`, `git://…`, `https://github.com/o/r/tree/main`
        — and every one of them has to reduce to the same two segments or the
        upkeep signal is silently missing for most candidates.
        """
        text = reference.strip()
        if not text:
            return ""
        for prefix in ("git+", "git://", "ssh://git@", "git@"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        text = text.replace("github.com:", "github.com/")
        marker = "github.com/"
        if marker in text:
            text = text.split(marker, 1)[1]
        elif text.count("/") != 1:
            return ""
        text = text.split("#", 1)[0].split("?", 1)[0]
        parts = [p for p in text.split("/") if p]
        if len(parts) < 2:
            return ""
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{owner}/{repo}" if owner and repo else ""

    # -- normalisation ----------------------------------------------------

    def _artifact(self, payload: Mapping[str, Any], origin: Origin) -> Artifact:
        licence_block = payload.get("license")
        spdx = ""
        if isinstance(licence_block, Mapping):
            candidate = str(licence_block.get("spdx_id") or "").strip()
            spdx = "" if candidate.lower() in _SPDX_UNKNOWN else candidate

        pushed = _timestamp(str(payload.get("pushed_at") or ""))
        archived = bool(payload.get("archived")) or bool(payload.get("disabled"))

        return Artifact(
            ecosystem=self.ecosystem,
            name=str(payload["full_name"]),
            version=str(payload.get("default_branch") or ""),
            summary=str(payload.get("description") or ""),
            licence=spdx,
            homepage=str(payload.get("homepage") or ""),
            repository=str(payload.get("html_url") or ""),
            keywords=tuple(str(t).lower() for t in (payload.get("topics") or [])),
            requires_runtime="",
            stars=int(payload.get("stargazers_count") or 0),
            released_at=pushed,
            deprecated=archived,
            origin=origin,
            raw={
                "archived": archived,
                "forks": payload.get("forks_count"),
                "open_issues": payload.get("open_issues_count"),
                "language": payload.get("language"),
                "pushed_at": payload.get("pushed_at"),
            },
        )

    def status(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset": self.rate_limit_reset,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<GitHubSource {self.base} auth={self.authenticated}>"
