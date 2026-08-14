"""The planner — it writes a composition, and shows it before running it.

`slpie plan "what breaks if lodash 5 lands?"` prints the pipeline that would
answer, the reason each stage is there, and whether it needs an environment. You
see the reasoning while the decision is still free.

The offline backend is real rather than a placeholder, and it is real because the
pipe is typed. Intent recognition extracts only two things — what *kind* of answer
is wanted, and what it is about — and then
:meth:`~slpie.compose.registry.VerbRegistry.paths_to` supplies the composition,
since it already enumerates every pipeline producing that kind, shortest first. So
the planner cannot invent a verb and cannot emit an invalid pipeline: it only ever
selects routes the graph already contains.

That is what makes the no-key path honest. A planner that composed freely would
need a model to stay correct; this one is correct by construction and therefore
works in the simulator, in an air-gapped network, and in CI.

When the question is not understood it returns a clarification with concrete
options drawn from the cookbook — a question back rather than an arbitrary
pipeline, because an arbitrary pipeline looks like an answer to a question nobody
asked.
"""

from .intent import SIGNALS, Intent, read
from .plan import NEVER_PLANNED, Plan, Step, plan_for

__all__ = [
    "NEVER_PLANNED",
    "SIGNALS",
    "Intent",
    "Plan",
    "Step",
    "plan_for",
    "read",
]
