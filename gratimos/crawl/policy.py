"""How the crawler is allowed to behave — decided once, enforced everywhere.

Politeness is not an afterthought here because an impolite crawler is an
outage somebody else has to absorb. The policy is a value object handed to the
fetcher at construction, so there is no code path that fetches without one and
no per-call flag that can quietly turn the guards off.

Four things it fixes:

* **Rate.** One request per host per interval, enforced by a token bucket that
  sleeps rather than drops — a delayed fetch is correct, a skipped one is a gap.
* **Reach.** Host allow-list, page ceiling and depth ceiling. A crawler without
  a ceiling eventually walks the whole web, and the interesting failure is that
  it does so slowly enough that nobody notices until the bill arrives.
* **Identity.** A user agent that says who is calling and how to make it stop.
  Anonymous traffic is what gets whole IP ranges blocked.
* **Backoff.** ``Retry-After`` is obeyed literally. A server telling you to wait
  is the cheapest possible signal that you are the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import CrawlError


class PolicyViolation(CrawlError):
    """The crawler was asked to do something its policy forbids."""


class FetchError(CrawlError):
    """A request failed and could not be recovered by retrying."""


#: Identifies the crawler and points at something a sysadmin can act on. Used
#: verbatim; a blank user agent is refused at construction.
DEFAULT_USER_AGENT = (
    "gratimos-crawler/1.0 (+https://github.com/Reimain/Macropol-s; reuse discovery)"
)

#: One request per host per this many seconds. Deliberately slower than a
#: registry would tolerate — the crawler is anticipating reuse, not racing.
DEFAULT_INTERVAL = 1.0

#: Ceiling on retries for a *recoverable* status (429, 502, 503, 504). Beyond
#: this the fetch fails loudly rather than hammering a struggling host.
DEFAULT_RETRIES = 3

#: Never wait longer than this even if the server asks for more. A `Retry-After`
#: of an hour means "come back later", not "block the process for an hour".
MAX_BACKOFF = 60.0


@dataclass(frozen=True, slots=True)
class CrawlPolicy:
    """The rules every fetch is measured against."""

    user_agent: str = DEFAULT_USER_AGENT
    interval: float = DEFAULT_INTERVAL
    timeout: float = 15.0
    retries: int = DEFAULT_RETRIES
    max_pages: int = 500
    max_depth: int = 3
    max_bytes: int = 8 * 1024 * 1024
    obey_robots: bool = True
    allowed_hosts: frozenset[str] = frozenset()
    blocked_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise PolicyViolation(
                "a crawler must identify itself; set user_agent to something "
                "that names the operator and a way to contact them"
            )
        if self.interval < 0:
            raise PolicyViolation("interval cannot be negative")
        if self.timeout <= 0:
            raise PolicyViolation("timeout must be positive")
        if self.retries < 0:
            raise PolicyViolation("retries cannot be negative")
        if self.max_bytes <= 0:
            raise PolicyViolation("max_bytes must be positive")

    def permits_host(self, host: str) -> bool:
        """Whether this host may be contacted at all.

        An empty allow-list means "no host restriction", not "no hosts"; the
        block-list always wins, so adding a host to both blocks it.
        """
        name = host.lower()
        if name in {h.lower() for h in self.blocked_hosts}:
            return False
        if not self.allowed_hosts:
            return True
        return name in {h.lower() for h in self.allowed_hosts}

    def require_host(self, host: str) -> None:
        if not self.permits_host(host):
            raise PolicyViolation(f"host {host!r} is not permitted by this crawl policy")

    def backoff_for(self, attempt: int, retry_after: float | None = None) -> float:
        """How long to wait before retry number ``attempt`` (1-based).

        An explicit ``Retry-After`` beats our arithmetic — the server knows its
        own recovery time and we do not — but is still capped, so a hostile or
        misconfigured header cannot stall the crawl indefinitely.
        """
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, MAX_BACKOFF)
        return min(self.interval * (2 ** max(attempt - 1, 0)), MAX_BACKOFF)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_agent": self.user_agent,
            "interval": self.interval,
            "timeout": self.timeout,
            "retries": self.retries,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "max_bytes": self.max_bytes,
            "obey_robots": self.obey_robots,
            "allowed_hosts": sorted(self.allowed_hosts),
            "blocked_hosts": sorted(self.blocked_hosts),
        }


#: The policy used when a crawl is scoped to the official package registries.
#: Narrow on purpose: these hosts publish documented JSON APIs, so there is no
#: reason for the crawler to wander off them by default.
OFFICIAL_SOURCES = frozenset({
    "pypi.org",
    "registry.npmjs.org",
    "api.github.com",
    "crates.io",
    "repo1.maven.org",
})


def official_policy(**overrides: Any) -> CrawlPolicy:
    """A policy restricted to the registries that publish an API for this."""
    settings: dict[str, Any] = {"allowed_hosts": OFFICIAL_SOURCES}
    settings.update(overrides)
    return CrawlPolicy(**settings)
