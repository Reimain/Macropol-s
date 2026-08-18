"""API management, WSO2-shaped, in ring 0.

WSO2 splits an API platform into a Publisher, a Developer Portal, a Gateway, a
Key Manager, a throttling tier and analytics. The split is the right one and
every part of it already has a counterpart here, so the work is wiring rather
than invention:

===================  =========================================================
WSO2 component       what it is built from
===================  =========================================================
Publisher            `slpie/compose/registry.py` — each verb group is an API,
                     so adding a verb adds an operation with no file edited
Developer Portal     `slpie/manual/render.py:as_dict()`, already annotated as
                     what the clients fetch
Gateway              an ordered chain in front of `Api.handle`, taking its
                     shape from `slpie/capture/firewall.py`, which took its
                     shape from `slpie/rbac/engine.py`
Key Manager          `slpie/identity/providers.py:ApiKeyProvider` composed with
                     the subscription ledger below
Throttling           declared in the `slpie/governance/policies.py` shape,
                     decided with `slpie/workspace/quota.py`'s admit/headroom
Analytics            aggregates on the event bus
===================  =========================================================

**Three policy-as-data patterns already exist and a fourth is not invented.**
They answer three different questions, which is why all three are used:

* **authorisation** — `rbac/engine.py`, reused with no changes at all. It is the
  only place a yes/no access decision is made, and `Decision.explain()` is the
  403 body verbatim rather than a second sentence about the same rule.
* **mediation** — priority-ordered `Situation → Verdict`, for the decisions that
  are not yes/no: which version to route to, whether a deprecation header
  belongs on the response, whether a payload is too large. Ported into
  `chain.py` rather than imported from `gratimos/policy/rules.py`, deliberately
  — see that module.
* **the file format** — `governance/policies.py`'s parser, imported directly. A
  closed operator vocabulary, fnmatch rather than regex so a policy file cannot
  be a ReDoS, and a malformed file that records an error while the rest load.

Stdlib only, like everything in ring 0. No FastAPI, no Redis, no token bucket
library — a throttle is a deque and a clock.
"""

from __future__ import annotations

from .action import ActionMap, action_for
from .analytics import Analytics, Bucket
from .application import Application
from .catalog import ApiCatalog, ApiDefinition, Operation
from .chain import Chain, Rule, Situation, Verdict
from .credential import Credential, CredentialStore
from .errors import ApimError, LifecycleRefused, SubscriptionRefused, ThrottleRefused
from .gateway import Admission, Gateway
from .lifecycle import TRANSITIONS, ApiState, advance
from .subscription import Subscription, SubscriptionLedger
from .throttle import TIERS, ThrottleDecision, ThrottlePolicy, Throttler

__all__ = [
    "ActionMap", "action_for",
    "ApiCatalog", "ApiDefinition", "Operation",
    "ApiState", "TRANSITIONS", "advance",
    "ApimError", "LifecycleRefused", "SubscriptionRefused", "ThrottleRefused",
    "Application",
    "Admission", "Analytics", "Bucket", "Gateway",
    "Chain", "Rule", "Situation", "Verdict",
    "Credential", "CredentialStore",
    "Subscription", "SubscriptionLedger",
    "ThrottleDecision", "ThrottlePolicy", "Throttler", "TIERS",
]
