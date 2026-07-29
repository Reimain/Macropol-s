"""Dependency resolution, with the failure explained rather than announced.

The plan ships backtracking and defers PubGrub, on two conditions that this
package holds itself to:

* the **model** is PubGrub-shaped (terms, an index protocol, failure as a
  value), so the better solver replaces one class rather than a subsystem;
* an unsatisfiable result **names the conflicting pair and the window each one
  demands**. "Resolution impossible" tells you that you have a problem and
  nothing about which problem, and the person reading it then bisects their
  dependency tree by hand — the exact work the solver existed to do.

:mod:`~slpie.reasoning.constraints.compat` answers the two questions the solver
asks constantly: whether two windows can both hold, and how big a step an
upgrade is. Both have a wrong answer that looks right — shared boundaries, and
treating every major bump as a certain break — and both are handled explicitly.
"""

from .compat import (
    Change,
    Interval,
    Overlap,
    UpgradeStep,
    classify,
    overlaps,
    safe_upgrades,
    to_intervals,
)
from .model import (
    Assignment,
    Conflict,
    PackageIndex,
    Requirement,
    Solution,
    StaticIndex,
    requirements_from,
)
from .solver import DEFAULT_MAX_STEPS, BacktrackingSolver, Solver, solve

__all__ = [
    "DEFAULT_MAX_STEPS",
    "Assignment",
    "BacktrackingSolver",
    "Change",
    "Conflict",
    "Interval",
    "Overlap",
    "PackageIndex",
    "Requirement",
    "Solution",
    "Solver",
    "StaticIndex",
    "UpgradeStep",
    "classify",
    "overlaps",
    "requirements_from",
    "safe_upgrades",
    "solve",
    "to_intervals",
]
