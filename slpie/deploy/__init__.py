"""Deployment as a declaration — the same move the environment manifest makes.

SLPIE already reads Terraform, Helm and Kubernetes (phase 8) and already models
deployment topology as code (phase 12). This closes the loop: it deploys itself
from that same model. One model, three uses — discovered from your
infrastructure, generated as your architecture, applied as ours.

── Why this is ring 0 ───────────────────────────────────────────────────

§18's prose and its file list disagreed, and the prose was right: *emitting is
text generation*. Three things follow from putting the model, the plan and the
emitters here rather than in ring 1:

* **`deploy` can be a verb.** `slpie/compose/registry.py` is ring 0 and cannot
  import ring 1, so a deploy that lived entirely above it would have no CLI
  subcommand, no route, no manual page and no planner entry — a capability the
  platform has and no surface can reach, which is the definition of drift §24
  was written to prevent and §29 measured.
* **The air-gapped operator is the one who most needs `render`.** Producing a
  compose file or a systemd unit needs no network, no cloud account and no
  toolchain. Withholding it without the enterprise extras would take the
  offline path away from the only console that runs offline.
* **Applying is genuinely different**, and it stays in ring 1: it needs
  binaries and cloud credentials, which are the operator's business and not the
  kernel's.

── Nothing here touches anything ────────────────────────────────────────

Every module in this package is pure: a manifest in, a model or text out. The
dangerous half — `apply` — goes through `slpie/binding/guard.py`, the same gate
that refuses an unconfirmed live binding, because it is the same class of action
and a second implementation of a gate is a hole waiting to be found.
"""

from .manifest import (
    Budget,
    Cloud,
    Component,
    Deployment,
    DeployTarget,
    Elasticity,
    Persistence,
    Platform,
    Regions,
    Store,
)
from .schema import SECTIONS, validate

__all__ = [
    "Budget", "Cloud", "Component", "Deployment", "DeployTarget", "Elasticity",
    "Persistence", "Platform", "Regions", "SECTIONS", "Store", "validate",
]
