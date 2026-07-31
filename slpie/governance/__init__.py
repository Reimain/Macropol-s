"""Governance — many findings, never one verdict.

A rule set that stopped at the first failure would report one problem and hide
the other forty, and a rule that raised would blind the platform to everything
after it. So `RuleSet.evaluate` runs every matching rule, and a rule that raises
abstains with a recorded error rather than aborting the evaluation.

The second principle is the one that governs what a report *means*: **a check
that did not run must never read as a check that passed.** A rule lacking the
data it needs — an advisory feed, a popularity list, a distribution context —
declines through `matches` rather than guessing, and both declining and
abstaining travel out as gaps on the flow. A findings list is read as
"everything that is wrong", and neither state supports that reading.

Five families, every one registered unconditionally:

===============  ==========================================================
`advisories`     known vulnerabilities, matched against a supplied OSV feed
`secrets`        credentials in source, by pattern and by entropy
`boundaries`     declared security zones, and what crosses them
`licenses`       obligations, judged in a stated distribution context
`supplychain`    typosquats, unmaintained packages, integrity, unpinned ranges
===============  ==========================================================

Nothing here fetches. Advisory documents, popularity lists and file contents are
handed to the rules as data, which is what makes a governance run reproducible
offline and identical in the simulator and against a real environment.

Reached through the `govern` verb, which builds a real in-memory graph from a
scan (`view.py`) so the rules run on a bare checkout with no database — and
cannot tell the difference, because it is the same store the engine uses.
"""

from __future__ import annotations

from . import builtins, policies, rules, view

__all__ = ["builtins", "policies", "rules", "view"]
