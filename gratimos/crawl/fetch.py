"""The fetcher — one function, and every guard the crawler has, applied to it.

`Fetcher.get()` is the only way anything above this module reaches a URL, which
is the point: policy, robots, rate limiting, retry and caching are composed here
once rather than being remembered correctly at each call site.

The order is deliberate and each step can short-circuit:

```
policy host check → cache: fresh? absent? → robots → rate limit
                                                        ↓
                      conditional GET ─── 304 ──→ refresh the stored entry
                              │
                        2xx ──┼── 429/5xx ──→ backoff, retry, then fail loudly
                              └── 404/410 ──→ remember the absence, briefly
```

Cache before robots is not an oversight. A response we already hold was already
permitted when it was fetched, and re-asking `robots.txt` for permission to read
our own memory would spend a request to learn nothing.

Time is injected. `clock` and `sleep` are constructor arguments so the rate
limiter and the backoff can be exercised at full speed in a test and still be
the same code that waits a real second in production — a rate limiter that is
stubbed out under test is a rate limiter nobody has run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .cache import ABSENT_STATUSES, ResponseCache
from .policy import CrawlPolicy, FetchError, PolicyViolation
from .robots import RobotsGate
from .transport import Response, Transport, UrllibTransport

#: Statuses worth trying again. Everything else is the server's final answer,
#: and retrying a 404 or a 400 is just noise on somebody else's access log.
RETRYABLE = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class FetchReport:
    """Running totals. The evidence behind "was this crawl polite?"."""

    requests: int = 0
    served_from_cache: int = 0
    known_absent: int = 0
    revalidated: int = 0
    retries: int = 0
    refused_by_robots: int = 0
    refused_by_policy: int = 0
    failures: int = 0
    waited: float = 0.0

    @property
    def network_requests(self) -> int:
        """Requests that actually left the process — the politeness number."""
        return self.requests - self.served_from_cache - self.known_absent

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "network_requests": self.network_requests,
            "served_from_cache": self.served_from_cache,
            "known_absent": self.known_absent,
            "revalidated": self.revalidated,
            "retries": self.retries,
            "refused_by_robots": self.refused_by_robots,
            "refused_by_policy": self.refused_by_policy,
            "failures": self.failures,
            "waited": round(self.waited, 3),
        }


class RateLimiter:
    """One request per host per interval, by sleeping rather than by dropping.

    Per host rather than globally, because the constraint being respected is the
    other server's capacity — crawling PyPI slowly is no reason to crawl npm
    slowly too, and a single global lane makes a multi-source crawl needlessly
    serial.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.interval = interval
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._last: dict[str, float] = {}
        self.total_wait = 0.0

    def wait(self, host: str, *, minimum: float = 0.0) -> float:
        """Block until this host may be contacted again. Returns time waited."""
        interval = max(self.interval, minimum)
        if interval <= 0:
            self._last[host] = self._clock()
            return 0.0

        now = self._clock()
        previous = self._last.get(host)
        delay = 0.0
        if previous is not None:
            delay = max(previous + interval - now, 0.0)
            if delay > 0:
                self._sleep(delay)
                self.total_wait += delay
        self._last[host] = self._clock()
        return delay

    def pause(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)
            self.total_wait += seconds


class Fetcher:
    """Polite, cached, retrying HTTP GET. The crawler's only door outward."""

    def __init__(
        self,
        policy: CrawlPolicy | None = None,
        *,
        transport: Transport | None = None,
        cache: ResponseCache | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.policy = policy if policy is not None else CrawlPolicy()
        self.transport = (
            transport if transport is not None
            else UrllibTransport(max_bytes=self.policy.max_bytes)
        )
        self.cache = cache if cache is not None else ResponseCache()
        self.robots = RobotsGate(self.transport, self.policy)
        self.limiter = RateLimiter(self.policy.interval, clock=clock, sleep=sleep)
        self._clock = clock if clock is not None else time.monotonic
        self.report = FetchReport()

    # -- the door --------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        revalidate: bool = False,
    ) -> Response:
        """Fetch one URL under the full policy. Raises rather than returning junk."""
        started = self._clock()
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            raise PolicyViolation(f"{url!r} has no host")

        if not self.policy.permits_host(host):
            self.report.refused_by_policy += 1
            raise PolicyViolation(f"host {host!r} is not permitted by this crawl policy")

        self.report.requests += 1

        if not revalidate:
            cached = self.cache.fresh(url)
            if cached is not None:
                self.report.served_from_cache += 1
                return cached
            if self.cache.absent(url):
                # Recently confirmed to hold nothing. Name-guessing against a
                # registry with no search API produces the same absences over
                # and over, and re-asking for each one is the difference between
                # a polite crawl and an abusive one.
                self.report.known_absent += 1
                raise FetchError(
                    f"{url} was absent when last asked, within the last "
                    f"{self.cache.negative_ttl:g}s"
                )

        decision = self.robots.check(url)
        if not decision.allowed:
            self.report.refused_by_robots += 1
            raise PolicyViolation(decision.reason)

        request_headers = self._headers(url, headers)
        attempt = 0
        while True:
            waited = self.limiter.wait(host, minimum=decision.crawl_delay)
            self.report.waited += waited

            response = self.transport.get(url, request_headers, self.policy.timeout)

            if response.not_modified:
                refreshed = self.cache.refresh(url)
                self.report.revalidated += 1
                if refreshed is not None:
                    return refreshed
                # A 304 with nothing stored means our validators came from
                # somewhere else. Ask again unconditionally rather than
                # returning an empty body as if it were the resource.
                request_headers = self._headers(url, headers, conditional=False)
                if attempt >= self.policy.retries:
                    raise FetchError(f"{url} returned 304 but nothing is cached for it")
                attempt += 1
                self.report.retries += 1
                continue

            if response.ok:
                self.cache.store(response)
                return Response(
                    url=response.url, status=response.status, body=response.body,
                    headers=response.headers, from_cache=False,
                    elapsed=self._clock() - started,
                )

            if response.status in RETRYABLE and attempt < self.policy.retries:
                attempt += 1
                self.report.retries += 1
                self.limiter.pause(
                    self.policy.backoff_for(attempt, response.retry_after)
                )
                continue

            if response.status in ABSENT_STATUSES:
                self.cache.note_absent(url)

            self.report.failures += 1
            raise FetchError(
                f"{url} returned {response.status}"
                + (f" after {attempt} retries" if attempt else "")
            )

    def json(self, url: str, *, headers: Mapping[str, str] | None = None) -> Any:
        """Fetch and parse. The shape every official registry API speaks."""
        return self.get(url, headers=headers).json()

    def try_get(self, url: str, **kwargs: Any) -> Response | None:
        """Fetch, or return None when it was refused or failed.

        For the many places in a crawl where an absent optional resource — a
        missing README, a repository with no releases — must not abort the run.
        """
        try:
            return self.get(url, **kwargs)
        except (FetchError, PolicyViolation):
            return None

    # -- internals -------------------------------------------------------

    def _headers(
        self,
        url: str,
        extra: Mapping[str, str] | None,
        *,
        conditional: bool = True,
    ) -> dict[str, str]:
        headers = {
            "User-Agent": self.policy.user_agent,
            "Accept": "application/json, text/plain;q=0.5, */*;q=0.1",
            "Accept-Encoding": "gzip, deflate",
        }
        if conditional:
            headers.update(self.cache.conditional_headers(url))
        headers.update(extra or {})
        return headers

    def status(self) -> dict[str, Any]:
        return {
            "policy": self.policy.to_dict(),
            "fetch": self.report.to_dict(),
            "cache": self.cache.status(),
            "robots": self.robots.status(),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return (
            f"<Fetcher {self.report.network_requests} network / "
            f"{self.report.requests} requests>"
        )
