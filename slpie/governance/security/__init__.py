"""The security rule families.

**Incomplete.** Only `advisories` (OSV matching, purl-keyed) is written so far.
Advisory data is *supplied* to the matcher as parsed documents; nothing in this
package fetches anything, so the kernel stays offline-capable and a governance
run is reproducible from its inputs.
"""

from __future__ import annotations

from . import advisories

__all__ = ["advisories"]
