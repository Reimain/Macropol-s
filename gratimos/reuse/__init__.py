"""Reuse assessment — from crawled artifacts to a recommendation you can defend.

Reading order:

* `bridge` — the single, asserted seam where Gratimos imports SLPIE, for licence
  obligation analysis and version-range algebra.
* `candidate` — an artifact held against a need, with its concept matches
  resolved through the lattice.
* `gate` — the hard constraints. Failing one excludes; passing is never a score.
* `rank` — the soft dimensions, as named components that reconstruct the total.
* `assess` — the composition, and the only entry point most callers need.
"""

from __future__ import annotations

from .assess import Assessment, Recommendation, ReuseAssessor
from .bridge import licence_obligation, licence_ok, runtime_ok
from .candidate import MAX_GENERALITY, Candidate, ConceptMatch, build_candidate
from .gate import DISTRIBUTIONS, LINKAGES, Blocker, Gate, GateResult
from .rank import OBLIGATION_COST, WEIGHTS, Component, Ranker, Score

__all__ = [
    "Candidate", "ConceptMatch", "build_candidate", "MAX_GENERALITY",
    "Gate", "GateResult", "Blocker", "DISTRIBUTIONS", "LINKAGES",
    "Ranker", "Score", "Component", "WEIGHTS", "OBLIGATION_COST",
    "ReuseAssessor", "Assessment", "Recommendation",
    "licence_ok", "licence_obligation", "runtime_ok",
]
