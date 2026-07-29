"""Governance — many findings, never one verdict.

A rule set that stopped at the first failure would report one problem and hide
the other forty, and a rule that raised would blind the platform to everything
after it. So `RuleSet.evaluate` runs every matching rule, and a rule that raises
abstains with a recorded error rather than aborting the evaluation.

**Incomplete.** `rules`, `policies` and `security.advisories` are written;
`security.licenses`, `security.secrets`, `security.supplychain` and
`security.boundaries` are not yet — see the phase-11 note in the plan.
"""

from __future__ import annotations

from . import policies, rules

__all__ = ["rules", "policies"]
