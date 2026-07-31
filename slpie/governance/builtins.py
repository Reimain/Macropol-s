"""Every built-in rule family, as one set — registered the way a plugin is.

Two decisions live here, and both are about what a governance run is allowed to
leave out.

**Families are assembled, never hard-coded into the evaluator.** `RuleSet` knows
nothing about which rules exist; this module knows nothing about how they run.
That separation is what lets a customer's rule pack sit alongside the built-ins
with no kernel change — `register_rule_family` puts both through
`ExtensionPoint.RULE`, so the seam is proven by the built-ins' own use rather
than asserted (invariant 6).

**Every family is registered unconditionally.** There is no "enable security"
switch, because a rule set assembled without one is a report that looks complete
and is not. What a family *does* depend on is data — an advisory feed, a
popularity list, a distribution context — and that dependency is expressed by the
rule declining through `matches`, which is visible in the evaluation counts, not
by the rule being absent, which is not.

The difference matters at the surface: `1 rule declined` tells an operator that
something was skipped and lets them ask why. A missing rule tells them nothing at
all, and the report reads identically to one where the check passed.
"""

from __future__ import annotations

from typing import Any, Iterable

from .rules import RuleSet, register_rule_family
from .security.advisories import AdvisoryDatabase, advisory_rules
from .security.boundaries import boundary_rules
from .security.licenses import license_rules
from .security.secrets import secret_rules
from .security.supplychain import supplychain_rules

#: The families, in the order a report reads best: what is broken, then what is
#: leaking, then what is legally binding, then what is merely worrying.
FAMILIES = ("advisories", "secrets", "boundaries", "licenses", "supplychain")


def builtins(
    *,
    advisories: AdvisoryDatabase | None = None,
    only: Iterable[str] = (),
) -> RuleSet:
    """One rule set holding every built-in family.

    `advisories` is passed in rather than loaded, because nothing in this package
    fetches — an OSV feed is data the caller supplies, which is what keeps a
    governance run reproducible offline and identical between the simulator and a
    real environment.
    """
    wanted = frozenset(only) or frozenset(FAMILIES)
    merged = RuleSet(name="builtin")

    if "advisories" in wanted:
        # Constructed even with an empty database. The rule then declines through
        # `matches`, which is reported — where omitting the family would leave a
        # report that silently never checked for vulnerabilities at all.
        merged.extend(advisory_rules(advisories or AdvisoryDatabase()))
    if "secrets" in wanted:
        merged.extend(secret_rules())
    if "boundaries" in wanted:
        merged.extend(boundary_rules())
    if "licenses" in wanted:
        merged.extend(license_rules())
    if "supplychain" in wanted:
        merged.extend(supplychain_rules())

    return merged


def register_builtins(
    registry: Any,
    *,
    advisories: AdvisoryDatabase | None = None,
) -> Any:
    """Register the built-in families with the plugin registry.

    Each family registers separately so a deployment can quarantine one without
    losing the rest, and so `slpie plugins` lists them the way it lists a
    third-party rule pack — which is the point of routing built-ins through the
    public path.
    """
    register_rule_family(
        registry, advisory_rules(advisories or AdvisoryDatabase()),
        plugin_id="governance.advisories",
        description="known vulnerabilities, matched against a supplied OSV feed",
    )
    register_rule_family(
        registry, secret_rules(), plugin_id="governance.secrets",
        description="credentials in source, by pattern and by entropy",
    )
    register_rule_family(
        registry, boundary_rules(), plugin_id="governance.boundaries",
        description="declared security boundaries, and what crosses them",
    )
    register_rule_family(
        registry, license_rules(), plugin_id="governance.licenses",
        description="licence obligations in a stated distribution context",
    )
    register_rule_family(
        registry, supplychain_rules(), plugin_id="governance.supplychain",
        description="typosquats, unmaintained packages, integrity, unpinned ranges",
    )
    return registry
