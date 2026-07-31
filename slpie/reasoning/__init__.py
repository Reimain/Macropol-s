"""The layered pipeline — where evidence becomes understanding, traceably.

Ten layers are planned; eight are built. Every one appends and none mutates a
prior layer's output:

===== ======================== =========================================
L1    :mod:`~slpie.reasoning.l1_discovery`      what was read, indexed by evidence id
L2    :mod:`~slpie.reasoning.l2_normalization`  two dialects become one thing
L3    :mod:`~slpie.reasoning.l3_graph`          observations become nodes and edges
L4    :mod:`~slpie.reasoning.l4_linking`        the joins that span two files
L5    :mod:`~slpie.reasoning.l5_validation`     declared versus observed, as deltas
L6    :mod:`~slpie.reasoning.l6_constraints`    do the ranges hold together
L7    :mod:`~slpie.reasoning.l7_impact`         what else is reached, how confidently
L8    :mod:`~slpie.reasoning.l8_optimize`       the options, with the cost of each
===== ======================== =========================================

:mod:`~slpie.reasoning.guidance` assembles what the layers concluded into a
:class:`~slpie.domain.reasoning.Guidance` — the answer, its reasoning, its gaps,
the questions worth asking next and the actions available. It is reached through
the ``ask`` verb, so the console and the CLI are one capability rather than two.

One constraint holds the design together: **a layer appends, it never mutates a
prior layer's output.** Every :class:`~slpie.domain.reasoning.Enrichment` cites
the ids it was derived from, so walking backwards from any conclusion terminates
in raw evidence with a file and a line. If a layer could overwrite what an
earlier one said, that walk would lead somewhere that no longer exists and "why
does the platform believe this?" would have no answer.

The second constraint is about failure. A layer that raises **abstains**: the
error is recorded, the pipeline continues, and the gap travels with every answer
built afterwards. Aborting would let one bad layer cost the other nine;
swallowing the error would produce an answer that looks complete and is not.

:mod:`~slpie.reasoning.constraints` is the solver, kept separate because it is
consumed at L6 and is independently useful — a conflict explanation naming both
requirements and both windows is the point of it.
"""

from .guidance import guidance_for, render
from .l1_discovery import DiscoveryLayer
from .l2_normalization import NormalizationLayer
from .l3_graph import GraphConstructionLayer
from .l4_linking import SemanticLinkingLayer
from .l5_validation import ArchitectureValidationLayer
from .l6_constraints import ConstraintSolvingLayer
from .l7_impact import ImpactLayer, Reach
from .l8_optimize import OptimizationLayer
from .layer import BaseLayer, Layer, LayerContext, LayerError, LayerResult
from .pipeline import Pipeline, PipelineResult

__all__ = [
    "ArchitectureValidationLayer",
    "BaseLayer",
    "ConstraintSolvingLayer",
    "DiscoveryLayer",
    "GraphConstructionLayer",
    "Layer",
    "LayerContext",
    "LayerError",
    "LayerResult",
    "ImpactLayer",
    "NormalizationLayer",
    "OptimizationLayer",
    "Reach",
    "Pipeline",
    "PipelineResult",
    "SemanticLinkingLayer",
    "guidance_for",
    "render",
]
