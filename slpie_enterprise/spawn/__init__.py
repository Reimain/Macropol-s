"""Runtimes that make a workspace real.

Each implements `slpie.workspace.Spawner`. The kernel decides what a workspace
is entitled to; these decide how to build it, and neither knows about the other
beyond that protocol.
"""

from .kubernetes import DEFAULT_IMAGE, KubernetesSpawner, namespace_of

__all__ = ["DEFAULT_IMAGE", "KubernetesSpawner", "namespace_of"]
