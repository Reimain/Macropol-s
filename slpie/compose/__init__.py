"""Composition — the philosophy, and the one registry every surface reads.

The model is the network shell: small verbs that each do one thing, piped together
to produce results no single verb could. `ls | grep | wc` is not three features,
it is a combinatorial space, and that is what this package reproduces.

Four things make it more than a metaphor:

**The pipe carries provenance, not bytes.** A :class:`~slpie.compose.flow.Flow`
holds a value, its kind, its reasoning path and its gaps. `discover | link |
findings` ends with findings that still carry the parse failure from `discover`
three stages back — invariant 5 holding *through* composition, which is what makes
a long pipeline trustworthy rather than merely convenient.

**Pipes are typed.** Each verb declares what it consumes and produces, and one
mechanism then buys three features: a composition is validated before anything
runs, the manual generates "what may follow this" as a query, and the offline
planner gets a search space instead of a guess.

**One registry, five projections.** The CLI, the HTTP contract, the manual, the
planner and every client read :func:`~slpie.compose.registry.registry`. A verb
added once appears in all of them, and the test suite asserts the projection is
total so a half-wired verb fails rather than drifts.

**Verbs are plugins.** Built-ins register through the identical path a third-party
takes, so a customer's verb reaches their CLI, manual, planner and UI with no
kernel change.

    >>> from slpie.compose import run
    >>> result = run("discover . | link | findings")
    >>> result.flow.kind.label
    'FINDINGS'
"""

from .flow import Flow, Kind
from .parse import ParseError, Stage, parse, render
from .pipeline import (
    Composition,
    CompositionError,
    Mismatch,
    Result,
    Validation,
    run,
)
from .registry import VerbRegistry, registry, reset
from .verb import Context, Param, Verb, VerbError, VerbProvider

__all__ = [
    "Composition",
    "CompositionError",
    "Context",
    "Flow",
    "Kind",
    "Mismatch",
    "Param",
    "ParseError",
    "Result",
    "Stage",
    "Validation",
    "Verb",
    "VerbError",
    "VerbProvider",
    "VerbRegistry",
    "parse",
    "registry",
    "render",
    "reset",
    "run",
]
