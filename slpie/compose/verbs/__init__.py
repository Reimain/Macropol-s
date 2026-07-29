"""The built-in verbs, registered through the path a plugin uses.

`register_builtins` calls `VerbRegistry.register_provider` for each family, which
is the identical entry point a third-party plugin takes (invariant 6). The seam is
therefore proven by its own use rather than asserted — if registration were
broken for plugins it would be broken for the built-ins too, and the suite would
fail on the first verb.

Three families, split by what they need rather than by what they are about:

* `analysis` — needs nothing. Works the moment the package is installed.
* `shaping` — the polymorphic shell filters.
* `environment` — needs a manifest, a ledger and a graph.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..verb import Verb
from . import analysis, environment, shaping


class _Family:
    """Adapts a module's `verbs()` to the `VerbProvider` protocol."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self.name = module.__name__.rpartition(".")[2]

    def verbs(self) -> Sequence[Verb]:
        return self._module.verbs()


FAMILIES = (_Family(analysis), _Family(shaping), _Family(environment))


def register_builtins(registry: Any) -> Any:
    """Register every built-in family. Returns the registry, for chaining."""
    for family in FAMILIES:
        registry.register_provider(family, origin=f"builtin:{family.name}")
    return registry


__all__ = ["FAMILIES", "register_builtins"]
