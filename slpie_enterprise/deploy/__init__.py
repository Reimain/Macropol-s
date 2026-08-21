"""Applying — the half that needs binaries and credentials, and therefore ring 1.

Ring 0 renders. It produces `docker-compose.yaml`, a chart, HCL, units and CI
workflows, all deterministically and all offline, and then stops — because
running `terraform apply` needs terraform, a cloud account and somebody's
credentials, none of which are the kernel's business.

This package is the other half. It is a thin thing on purpose: everything
interesting already happened upstream, and what is left is writing the rendered
files to a directory and dispatching a command against them.

── It reuses two seams rather than growing a third ──────────────────────

* **`slpie/dispatch/`** for execution — argv lists, never `shell=True`, a
  normalised environment, and a missing binary reported as a capability gap
  rather than raised. §27 established all of that for `git` and `rg`; a deploy
  that shelled out its own way would be a second execution path with its own
  quoting bugs, and the one it would get wrong is the one running as root.
* **`slpie/binding/guard.py`** for permission — already checked in ring 0
  before this package is reached. Nothing here re-asks, and nothing here can be
  reached without having been asked.
"""

from .apply import TOOLS, DeploymentApplier

__all__ = ["DeploymentApplier", "TOOLS"]
