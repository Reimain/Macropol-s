"""Role-based access control: embedded, deterministic, and explainable.

Keycloak solves this with a Java server, a database, an admin console and an
evaluation engine whose answers you discover by turning on debug logging. That
is a lot of operational surface for a question that is, at bottom, "does this
principal hold a rule that permits this action on this resource?".

Here it is a role graph and an append-only binding log, embedded in the process.
No server, no database, no console — which means the same authorisation runs in
a CLI, a worker and an API and returns identical answers, a property a separate
policy service cannot offer because only one of the three can reach it.

What it does that Keycloak does not:

* **Every decision explains itself**, including the refusals, naming the rule
  and the role. `"denied"` is useless at 3am.
* **Separation of duties is enforced at assignment**, over the inheritance
  closure, not discovered in a quarterly review.
* **Break-glass cannot become permanent** — an elevation must state a reason and
  expire, capped at four hours.
* **The access review is computed**, not conducted: ten checks producing ranked
  findings with evidence and remediation.
* **Customisation is a config file**, so a customer's roles survive an upgrade
  without a fork, and custom logic plugs in as a named condition rather than as
  a patch.

Reading order: `model` (permissions, resources, scopes), `role` (inheritance and
separation), `engine` (bindings and decisions), `policy` (config files and the
shipped roles), `audit` (the review).
"""

from __future__ import annotations

from .audit import Finding, Kind, Report, Severity, review
from .engine import (
    MAX_ELEVATION,
    PERMANENT,
    AccessEngine,
    Binding,
    Condition,
    Decision,
    Outcome,
)
from .model import (
    Effect,
    Permission,
    RbacError,
    Scope,
    allow,
    deny,
    matches_action,
    matches_resource,
)
from .policy import (
    PolicyError,
    dump_policy,
    load_file,
    load_policy,
    parse_rule,
    system_roles,
)
from .role import MAX_INHERITANCE_DEPTH, Role, RoleGraph, Separation

__all__ = [
    "Permission", "Effect", "Scope", "RbacError", "allow", "deny",
    "matches_action", "matches_resource",
    "Role", "RoleGraph", "Separation", "MAX_INHERITANCE_DEPTH",
    "AccessEngine", "Binding", "Decision", "Outcome", "Condition",
    "PERMANENT", "MAX_ELEVATION",
    "load_policy", "load_file", "dump_policy", "parse_rule", "system_roles",
    "PolicyError",
    "review", "Report", "Finding", "Kind", "Severity",
]
