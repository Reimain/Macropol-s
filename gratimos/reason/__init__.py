"""Forward and backward reasoning over a fact base.

Backward chaining answers a question, touching only the rules that bear on it.
Forward chaining propagates a discovery to a fixpoint, deriving conclusions
nobody has asked for yet. The pair is what makes the knowledge base an
inference engine rather than a cache — backward is what makes it answer,
forward is what makes it learn.
"""

from __future__ import annotations

from .engine import (
    MAX_DEPTH,
    MAX_PASSES,
    Derivation,
    Engine,
    ForwardReport,
    Proof,
)
from .facts import (
    INFERENCE_CEILING,
    Fact,
    FactBase,
    Pattern,
    Provenance,
    ReasoningError,
    combine_confidence,
    is_variable,
)
from .rules import Guard, Rule, RuleSet, reuse_rules

__all__ = [
    "Fact", "FactBase", "Pattern", "Provenance", "ReasoningError",
    "combine_confidence", "is_variable", "INFERENCE_CEILING",
    "Rule", "RuleSet", "Guard", "reuse_rules",
    "Engine", "Proof", "Derivation", "ForwardReport", "MAX_DEPTH", "MAX_PASSES",
]
