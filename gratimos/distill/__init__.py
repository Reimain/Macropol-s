"""Distillation — remembering what the agent said, so it need not be asked twice.

The agent is called when, and only when, the system does not already know. A
validated verdict is recorded, forward-chained into everything it implies, and
answers every later phrasing of the same need from memory.

Two guards keep that from becoming a machine for repeating stale answers: the
escalation floor is on *decayed* confidence rather than on presence, so an aged
verdict escalates as if it were absent; and a sample of hits is re-validated
anyway, because a base that is never checked can only ever confirm itself.
"""

from __future__ import annotations

from .knowledge import (
    ESCALATION_FLOOR,
    RECALL_CEILING,
    KnowledgeBase,
    Recall,
    Verdict,
    Volatility,
)
from .loop import (
    DEFAULT_AUDIT_RATE,
    DistillationLoop,
    Judgement,
    Route,
    Turn,
    Validator,
)

__all__ = [
    "KnowledgeBase", "Verdict", "Volatility", "Recall",
    "ESCALATION_FLOOR", "RECALL_CEILING",
    "DistillationLoop", "Validator", "Judgement", "Turn", "Route",
    "DEFAULT_AUDIT_RATE",
]
