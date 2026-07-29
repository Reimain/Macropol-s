"""The layered pipeline — where evidence becomes understanding, traceably.

Ten layers are planned; phase 9 delivers the first four, which are the ones the
rest stand on:

===== ======================== =========================================
L1    :mod:`~slpie.reasoning.l1_discovery`      what was read, indexed by evidence id
L2    :mod:`~slpie.reasoning.l2_normalization`  two dialects become one thing
L3    :mod:`~slpie.reasoning.l3_graph`          observations become nodes and edges
L4    :mod:`~slpie.reasoning.l4_linking`        the joins that span two files
===== ======================== =========================================

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

from .l1_discovery import DiscoveryLayer
from .l2_normalization import NormalizationLayer
from .l3_graph import GraphConstructionLayer
from .l4_linking import SemanticLinkingLayer
from .layer import BaseLayer, Layer, LayerContext, LayerError, LayerResult
from .pipeline import Pipeline, PipelineResult

__all__ = [
    "BaseLayer",
    "DiscoveryLayer",
    "GraphConstructionLayer",
    "Layer",
    "LayerContext",
    "LayerError",
    "LayerResult",
    "NormalizationLayer",
    "Pipeline",
    "PipelineResult",
    "SemanticLinkingLayer",
]
