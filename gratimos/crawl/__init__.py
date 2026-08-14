"""The deep crawler — bounded by a need, polite by construction, offline-capable.

Reading order:

* `policy` — what the crawler is allowed to do. Constructed once, enforced
  everywhere; there is no unguarded fetch path.
* `transport` — the only place bytes come off a socket, and the seam that lets
  the whole crawler run from recorded payloads with no network at all.
* `cache` — conditional requests, so a second crawl is a sequence of 304s.
* `robots` — permission, cached per origin, with the failure posture stated.
* `fetch` — the composed door: policy, cache, robots, rate limit, retry.
* `source` — what a package looks like once the registry's dialect is gone.
* `crawler` — a need in, observed facts out.
"""

from __future__ import annotations

from .cache import (
    ABSENT_STATUSES,
    DEFAULT_NEGATIVE_TTL,
    DEFAULT_TTL,
    Entry,
    ResponseCache,
)
from .crawler import Crawler, CrawlResult
from .fetch import RETRYABLE, Fetcher, FetchReport, RateLimiter
from .policy import (
    DEFAULT_INTERVAL,
    DEFAULT_USER_AGENT,
    MAX_BACKOFF,
    OFFICIAL_SOURCES,
    CrawlError,
    CrawlPolicy,
    FetchError,
    PolicyViolation,
    official_policy,
)
from .robots import RobotsDecision, RobotsGate, origin_of
from .source import (
    OFFICIAL,
    SCRAPED,
    Artifact,
    Origin,
    Source,
    SourceError,
    SourceRegistry,
)
from .sources import GitHubSource, NpmSource, PyPISource
from .transport import RecordedTransport, Response, Transport, UrllibTransport

__all__ = [
    "CrawlPolicy", "official_policy", "CrawlError", "PolicyViolation", "FetchError",
    "DEFAULT_USER_AGENT", "DEFAULT_INTERVAL", "MAX_BACKOFF", "OFFICIAL_SOURCES",
    "Transport", "Response", "UrllibTransport", "RecordedTransport",
    "ResponseCache", "Entry", "DEFAULT_TTL", "DEFAULT_NEGATIVE_TTL", "ABSENT_STATUSES",
    "RobotsGate", "RobotsDecision", "origin_of",
    "Fetcher", "RateLimiter", "FetchReport", "RETRYABLE",
    "Artifact", "Origin", "Source", "SourceRegistry", "SourceError", "OFFICIAL", "SCRAPED",
    "PyPISource", "NpmSource", "GitHubSource",
    "Crawler", "CrawlResult",
]
