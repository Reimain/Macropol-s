"""The built-in verbs, registered through the path a plugin uses.

`register_builtins` calls `VerbRegistry.register_provider` for each family, which
is the identical entry point a third-party plugin takes (invariant 6). The seam is
therefore proven by its own use rather than asserted — if registration were
broken for plugins it would be broken for the built-ins too, and the suite would
fail on the first verb.

Six families, split by what they need rather than by what they are about:

* `analysis` — needs nothing. Works the moment the package is installed.
* `shaping` — the polymorphic shell filters.
* `environment` — needs a manifest, a ledger and a graph.
* `dispatch` — reaches an external tool instead of reimplementing it (§27).
* `capture` — identify by content, then the firewall (§26).
* `guidance` — suggestion and routines: what to look at next, and keeping it.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..verb import Verb
from . import analysis, capture, dispatch, environment, guidance, shaping


class _Family:
    """Adapts a module's `verbs()` to the `VerbProvider` protocol."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self.name = module.__name__.rpartition(".")[2]

    def verbs(self) -> Sequence[Verb]:
        return self._module.verbs()


FAMILIES = (
    _Family(analysis), _Family(capture), _Family(shaping),
    _Family(guidance), _Family(dispatch), _Family(environment),
)


def register_builtins(registry: Any) -> Any:
    """Register every built-in family. Returns the registry, for chaining."""
    for family in FAMILIES:
        registry.register_provider(family, origin=f"builtin:{family.name}")
    return registry


__all__ = ["FAMILIES", "register_builtins"]
