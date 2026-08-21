"""The robots.txt gate — asking permission before taking it.

`urllib.robotparser` already implements the parsing correctly, so this module
does not reimplement it. What it adds is the three things a crawler actually
needs around it and which the stdlib deliberately leaves out:

* **Caching per host**, so one `robots.txt` fetch covers the whole crawl of that
  host rather than one per URL.
* **A stated failure posture.** When `robots.txt` cannot be read, this allows.
  That is a considered choice and worth defending: the crawler is already
  restricted to an allow-list of official API hosts whose terms of use are the
  real authority, and treating an unreachable file as a blanket prohibition
  makes a transient network error silently produce an empty, apparently
  successful crawl. A 401 or 403 *on robots.txt itself* is different — that is
  the host answering, and it is answered with a refusal.
* **Crawl-delay**, which the stdlib parses but nothing consults. A host asking
  for ten seconds between requests gets ten seconds, overriding our own
  interval when it is longer.

Every decision is returned as a `RobotsDecision` carrying the reason, so a
skipped URL is explainable rather than merely absent from the results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .policy import CrawlPolicy
from .transport import Response, Transport


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Whether a URL may be fetched, why, and how long to wait first."""

    allowed: bool
    reason: str
    crawl_delay: float = 0.0

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed, "reason": self.reason,
            "crawl_delay": self.crawl_delay,
        }


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class RobotsGate:
    """Per-origin robots.txt, fetched once and consulted for every URL."""

    def __init__(self, transport: Transport, policy: CrawlPolicy) -> None:
        self.transport = transport
        self.policy = policy
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._refusals: dict[str, str] = {}
        self.fetches = 0

    def check(self, url: str) -> RobotsDecision:
        """Decide one URL. Cheap after the first call to a given origin."""
        if not self.policy.obey_robots:
            return RobotsDecision(True, "robots checking is disabled by policy")

        parts = urlsplit(url)
        if not parts.netloc:
            return RobotsDecision(False, f"{url!r} has no host to check robots.txt against")

        origin = origin_of(url)
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)

        refusal = self._refusals.get(origin)
        if refusal:
            return RobotsDecision(False, refusal)

        parser = self._parsers[origin]
        if parser is None:
            return RobotsDecision(
                True, f"{origin}/robots.txt could not be read; proceeding under crawl policy"
            )

        agent = self.policy.user_agent
        if not parser.can_fetch(agent, url):
            return RobotsDecision(False, f"{origin}/robots.txt disallows {parts.path or '/'}")

        delay = parser.crawl_delay(agent)
        return RobotsDecision(
            allowed=True,
            reason=f"{origin}/robots.txt permits {parts.path or '/'}",
            crawl_delay=float(delay) if delay else 0.0,
        )

    def delay_for(self, url: str) -> float:
        """The host's requested delay, or zero. Used by the rate limiter."""
        return self.check(url).crawl_delay

    def _load(self, origin: str) -> RobotFileParser | None:
        self.fetches += 1
        target = f"{origin}/robots.txt"
        try:
            response: Response = self.transport.get(
                target,
                {"User-Agent": self.policy.user_agent, "Accept": "text/plain"},
                self.policy.timeout,
            )
        except Exception:
            # Unreachable. Allow, per the posture documented above.
            return None

        if response.status in (401, 403):
            # The host answered, and the answer was no.
            self._refusals[origin] = (
                f"{target} returned {response.status}; treating the whole origin as disallowed"
            )
            return None
        if not response.ok:
            return None

        parser = RobotFileParser()
        parser.set_url(target)
        try:
            parser.parse(response.text().splitlines())
        except Exception:
            return None
        return parser

    def status(self) -> dict[str, Any]:
        return {
            "origins": len(self._parsers),
            "fetches": self.fetches,
            "refused_origins": sorted(self._refusals),
        }
