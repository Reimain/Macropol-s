"""Registry adapters — one per place packages actually live.

Each is thin on purpose: fetch through the shared `Fetcher`, normalise into
`Artifact`, and stop. No adapter retries, rate-limits, or caches, because all
three belong to the fetcher and reimplementing them per source is how one
registry ends up polite and another does not.
"""

from __future__ import annotations

from .github import GitHubSource
from .npm import NpmSource
from .pypi import PyPISource

__all__ = ["PyPISource", "NpmSource", "GitHubSource"]
