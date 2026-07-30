"""Suggestion intelligence — the non-deterministic block, made accountable.

The deterministic half is finished: the nouns are the graph's nodes, the grammar is
the verb registry's type graph, both registered and ledgered, so a semantic query
has a correct answer and can prove it. Adding more verbs widens that surface
without making it more useful.

What was missing is **suggestion**, on this axiom:

    If the user touches one thing they cannot understand as a reviewer, it fires
    smart suggestion paths with deterministic guidance.

A reviewer opens a finding, an unfamiliar term, an error — and the honest answer is
not a definition. **Pointing at a dictionary is not enough**: a definition says what
a word means and not what to do, and a reviewer who does not know what to do next
stops reviewing. The useful answer is a *path* they can run right now, with what it
will reveal stated up front.

The split that keeps the learned part safe:

==================== ============= =========
                     deterministic   learned
==================== ============= =========
what may be offered      yes            no
the guidance attached    yes            no
which comes first         no           yes
==================== ============= =========

Candidates come from the type graph, so a suggestion can never be something the
platform cannot do and never a pipeline that would not run. Only the *order* is
learned, so the worst a mis-learned ranking achieves is putting a useful path
second.

:mod:`~slpie.suggest.attention` decides *what* was touched, from dwell,
re-approach, repeat clicks and text selection rather than from clicks alone —
somebody who hovers four seconds and clicks nothing did not find what they needed,
and that is invisible to a system listening only for clicks. It stores no trails:
raw coordinates never reach the kernel, only per-subject aggregates.

:mod:`~slpie.suggest.learn` records acceptance and dismissal as facts, which makes
the ranking explainable and rebuildable — a suggestion the reviewer cannot
interrogate is one they stop trusting the first time it is wrong.
:mod:`~slpie.suggest.routine` is the payoff: at the end of a session the reviewer
**claims** what they did, and thereafter types two letters instead of composing
four stages.
"""

from .attention import (
    DWELL_CEILING,
    DWELL_FLOOR,
    FOCUS_FLOOR,
    STUCK_AT,
    Attention,
    Focus,
    Signal,
    SignalKind,
    from_events,
)
from .engine import STUCK_WIDTH, WIDTH, SuggestionEngine, suggest
from .gain import COST, STUCK_FLOOR, Factors, for_touch, rank
from .learn import MAX_WEIGHT, MIN_WEIGHT, RETIRE_AFTER, History, Reaction
from .routine import KEY, Routine, RoutineError, Routines
from .suggestion import Suggestion, Suggestions, mint_key
from .touch import (
    SITUATION_KEYS,
    Touch,
    TouchKind,
    on_empty,
    on_error,
    on_finding,
    on_gap,
    on_node,
    on_term,
)

__all__ = [
    "COST",
    "DWELL_CEILING",
    "DWELL_FLOOR",
    "FOCUS_FLOOR",
    "KEY",
    "MAX_WEIGHT",
    "MIN_WEIGHT",
    "RETIRE_AFTER",
    "SITUATION_KEYS",
    "STUCK_AT",
    "STUCK_FLOOR",
    "STUCK_WIDTH",
    "WIDTH",
    "Attention",
    "Factors",
    "Focus",
    "History",
    "Reaction",
    "Routine",
    "RoutineError",
    "Routines",
    "Signal",
    "SignalKind",
    "Suggestion",
    "SuggestionEngine",
    "Suggestions",
    "Touch",
    "TouchKind",
    "for_touch",
    "from_events",
    "mint_key",
    "on_empty",
    "on_error",
    "on_finding",
    "on_gap",
    "on_node",
    "on_term",
    "rank",
    "suggest",
]
