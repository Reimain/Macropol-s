"""A conditional-request cache — the difference between re-crawling and re-asking.

Package metadata is mostly stable and occasionally not, which is the exact case
HTTP validators were designed for. Storing the `ETag` and `Last-Modified` of
every response and replaying them as `If-None-Match`/`If-Modified-Since` turns
the second crawl of a registry into a sequence of 304s: no payload transferred,
no parsing, and — importantly — no pretending the data is fresh when the server
never confirmed it.

The freshness rule is deliberately conservative. An entry within its TTL is
served without contacting anybody. An entry past its TTL is **revalidated, not
discarded**: it goes back to the origin carrying its validators, and a 304
refreshes the timestamp while keeping the body. Discarding a stale-but-valid
entry would throw away the one thing that makes recrawling cheap.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..errors import CrawlError
from .transport import Response

#: How long a stored response is served without asking the origin. Short enough
#: that a yanked release surfaces within the hour, long enough that a crawl of a
#: few hundred packages does not re-fetch its own working set.
DEFAULT_TTL = 3600.0

#: How long an *absence* is remembered. Much shorter than `DEFAULT_TTL`, and the
#: asymmetry is the whole point.
#:
#: PyPI publishes no search API, so resolving a concept to a distribution means
#: asking for plausible names and accepting that most will 404. Without any
#: memory of that, one crawl spends fifty requests learning the same fifty
#: absences it learned a minute ago. With a long memory, a package published
#: this morning stays invisible until the entry expires — and "we already
#: decided that does not exist" is the worst possible thing for a cache to be
#: confidently wrong about.
#:
#: Five minutes covers a crawl session and little more. Absences are also never
#: persisted: a fresh process starts by believing nothing is missing.
DEFAULT_NEGATIVE_TTL = 300.0

#: Statuses that mean "there is nothing here", as opposed to "you may not have
#: it" (403) or "ask again later" (503). Only these are remembered as absence.
ABSENT_STATUSES = frozenset({404, 410})


@dataclass(slots=True)
class Entry:
    """One cached response and the validators that can refresh it in place."""

    url: str
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    stored_at: float = 0.0
    revalidated_at: float = 0.0
    hits: int = 0

    @property
    def etag(self) -> str:
        return _header(self.headers, "etag")

    @property
    def last_modified(self) -> str:
        return _header(self.headers, "last-modified")

    @property
    def validatable(self) -> bool:
        """Whether the origin gave us anything to revalidate against.

        Without a validator a stale entry is worthless — re-requesting it costs
        exactly as much as it did the first time — so those are simply dropped.
        """
        return bool(self.etag or self.last_modified)

    def age(self, now: float) -> float:
        return max(now - max(self.stored_at, self.revalidated_at), 0.0)

    def fresh(self, now: float, ttl: float) -> bool:
        return self.age(now) < ttl

    def as_response(self, *, from_cache: bool = True) -> Response:
        return Response(
            url=self.url, status=self.status, body=self.body,
            headers=dict(self.headers), from_cache=from_cache,
        )

    def conditional_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        return headers


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def _key(url: str) -> str:
    return hashlib.blake2b(url.encode("utf-8"), digest_size=16).hexdigest()


class ResponseCache:
    """In-memory by default; durable when given a directory.

    Persistence matters more than it looks: a crawl that starts cold every time
    re-downloads the same registry index on every run, and the politeness budget
    is spent on data that has not changed.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        ttl: float = DEFAULT_TTL,
        negative_ttl: float = DEFAULT_NEGATIVE_TTL,
        max_entries: int = 4096,
    ) -> None:
        if ttl < 0:
            raise CrawlError("ttl cannot be negative")
        if negative_ttl < 0:
            raise CrawlError("negative_ttl cannot be negative")
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self.max_entries = max_entries
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, Entry] = {}
        self._absent: dict[str, float] = {}
        self.hits = 0
        self.revalidations = 0
        self.misses = 0
        self.absences = 0
        if self.directory is not None:
            self._load()

    # -- lookup ----------------------------------------------------------

    def get(self, url: str, *, now: float | None = None) -> Entry | None:
        """The stored entry, fresh or not. Freshness is the caller's question."""
        return self._entries.get(_key(url))

    def fresh(self, url: str, *, now: float | None = None) -> Response | None:
        """A response usable without contacting the origin, or None."""
        moment = time.time() if now is None else now
        entry = self._entries.get(_key(url))
        if entry is None:
            self.misses += 1
            return None
        if not entry.fresh(moment, self.ttl):
            return None
        entry.hits += 1
        self.hits += 1
        return entry.as_response()

    def conditional_headers(self, url: str) -> dict[str, str]:
        entry = self._entries.get(_key(url))
        return entry.conditional_headers() if entry is not None else {}

    # -- absence ---------------------------------------------------------

    def note_absent(self, url: str, *, now: float | None = None) -> None:
        """Remember that this URL had nothing behind it, briefly."""
        if self.negative_ttl <= 0:
            return
        self._absent[_key(url)] = time.time() if now is None else now

    def absent(self, url: str, *, now: float | None = None) -> bool:
        """Whether we recently learned there is nothing here.

        Expired entries are removed on the way past rather than by a sweep, so
        the negative cache cannot grow without bound across a long crawl.
        """
        key = _key(url)
        recorded = self._absent.get(key)
        if recorded is None:
            return False
        moment = time.time() if now is None else now
        if moment - recorded >= self.negative_ttl:
            del self._absent[key]
            return False
        self.absences += 1
        return True

    # -- storage ---------------------------------------------------------

    def store(self, response: Response, *, now: float | None = None) -> None:
        """Keep a successful response. Errors are never cached.

        Caching a 500 would turn a transient outage into a persistent one, and
        caching a 404 would make a package that appears later permanently
        invisible to this crawler.
        """
        if not response.ok:
            return
        moment = time.time() if now is None else now
        entry = Entry(
            url=response.url, status=response.status, body=response.body,
            headers=dict(response.headers), stored_at=moment, revalidated_at=moment,
        )
        self._entries[_key(response.url)] = entry
        self._evict()
        self._persist(entry)

    def refresh(self, url: str, *, now: float | None = None) -> Response | None:
        """Record that a 304 confirmed the stored entry, and return it."""
        entry = self._entries.get(_key(url))
        if entry is None:
            return None
        entry.revalidated_at = time.time() if now is None else now
        entry.hits += 1
        self.revalidations += 1
        self._persist(entry)
        return entry.as_response()

    def drop(self, url: str) -> bool:
        key = _key(url)
        self._absent.pop(key, None)
        if key not in self._entries:
            return False
        del self._entries[key]
        if self.directory is not None:
            path = self.directory / f"{key}.json"
            path.unlink(missing_ok=True)
        return True

    def clear(self) -> None:
        self._entries.clear()
        self._absent.clear()
        if self.directory is not None:
            for path in self.directory.glob("*.json"):
                path.unlink(missing_ok=True)

    def _evict(self) -> None:
        """Oldest first. A crawl walks forward, so recency is the right proxy."""
        while len(self._entries) > self.max_entries:
            oldest = min(
                self._entries.items(),
                key=lambda item: max(item[1].stored_at, item[1].revalidated_at),
            )[0]
            entry = self._entries.pop(oldest)
            if self.directory is not None:
                (self.directory / f"{oldest}.json").unlink(missing_ok=True)
            del entry

    # -- durability ------------------------------------------------------

    def _persist(self, entry: Entry) -> None:
        if self.directory is None:
            return
        path = self.directory / f"{_key(entry.url)}.json"
        payload = {
            "url": entry.url,
            "status": entry.status,
            "headers": entry.headers,
            "stored_at": entry.stored_at,
            "revalidated_at": entry.revalidated_at,
            "hits": entry.hits,
            "body": entry.body.decode("utf-8", errors="surrogateescape"),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8", errors="surrogateescape")
        temporary.replace(path)

    def _load(self) -> None:
        assert self.directory is not None
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8", errors="surrogateescape"))
            except (OSError, json.JSONDecodeError):
                # A half-written cache file is a nuisance, not an error. Drop it
                # and let the crawl re-fetch rather than failing to start.
                path.unlink(missing_ok=True)
                continue
            entry = Entry(
                url=payload["url"], status=payload["status"],
                body=payload["body"].encode("utf-8", errors="surrogateescape"),
                headers=dict(payload.get("headers", {})),
                stored_at=payload.get("stored_at", 0.0),
                revalidated_at=payload.get("revalidated_at", 0.0),
                hits=payload.get("hits", 0),
            )
            self._entries[_key(entry.url)] = entry

    # -- reporting -------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(tuple(self._entries.values()))

    def status(self) -> dict[str, Any]:
        looked_up = self.hits + self.revalidations + self.misses
        return {
            "entries": len(self._entries),
            "absent": len(self._absent),
            "hits": self.hits,
            "revalidations": self.revalidations,
            "misses": self.misses,
            "absences": self.absences,
            "hit_rate": round((self.hits + self.revalidations) / looked_up, 4) if looked_up else 0.0,
            "ttl": self.ttl,
            "negative_ttl": self.negative_ttl,
            "durable": self.directory is not None,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ResponseCache {len(self._entries)} entries, ttl={self.ttl:g}s>"
