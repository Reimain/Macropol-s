"""The validation plane — the constraint, taken seriously.

*Conjecture Machines* argues that agents make ideas cheap while validation stays
"physical, human and institutional — and so, costly and slow", and that the gap
"in most disciplines is widening, not closing". This package is that argument
applied to this system, which has the same shape: the crawler proposes reuse
candidates faster than any agent confirms them, and the shell layer proposes
commands faster than anyone reviews them.

Three modules, answering the three things the paper asks for that generation
alone cannot supply:

* `calibration` — **does our confidence mean anything?** Every number in this
  system is derived by a stated rule, which has been treated as sufficient. It
  is not: a principled rule can be systematically wrong. Bucket predictions by
  stated confidence, compare each bucket to its observed rate, and refuse to
  report a figure at all below thirty observations.
* `record` — **what produced this?** Short records of the prompt, the model, the
  answer and the claims that came out, content-addressed and append-only, with
  credentials masked on the way in. `for_claim()` is the query a reviewer
  actually has.
* `budget` — **what should we check first?** Rank by expected information gain
  — uncertainty × leverage ÷ cost — and report the deferred queue as the
  headline rather than the leftovers, because a budgeted run that lists only its
  successes has hidden its own bottleneck.

The three compose: the ledger records what a validator was asked and said, the
calibrator learns from those outcomes whether the confidences were honest, and
the queue spends the next budget where the corrected numbers say it is worth
most.
"""

from __future__ import annotations

from .budget import (
    DEFAULT_COST,
    Budget,
    BudgetError,
    Candidate,
    Outcome,
    Queue,
    Report as BudgetReport,
    Unit,
)
from .calibration import (
    MIN_BIN_SAMPLES,
    MIN_SAMPLES,
    TOLERANCE,
    Bin,
    CalibrationError,
    Calibrator,
    Observation,
    Report as CalibrationReport,
    wilson,
)
from .record import (
    EXCERPT,
    Recorder,
    RecordError,
    Redaction,
    Run,
    RunLedger,
    entropy,
    redact,
)

__all__ = [
    "Calibrator", "Observation", "Bin", "CalibrationReport", "CalibrationError",
    "wilson", "MIN_SAMPLES", "MIN_BIN_SAMPLES", "TOLERANCE",
    "RunLedger", "Run", "Recorder", "Redaction", "RecordError",
    "redact", "entropy", "EXCERPT",
    "Queue", "Candidate", "Budget", "Unit", "Outcome", "BudgetReport",
    "BudgetError", "DEFAULT_COST",
]
