"""What else is on the market, what it cannot do, and what to build next.

A competitive claim without a citation is marketing, and marketing that reaches a
data room is a liability. So this package borrows the discipline
`gratimos/reference/` already applies to protocols: **every rival carries a URL,
every capability claim carries the date it was checked, and an entry that cannot
be checked refuses to exist.**

Three things live here, and the third is the one that pays for the first two.

===========  ================================================================
`rival`      one product, its capabilities, and where each claim was checked
`registry`   the field, as recorded — with `RECORDED` saying when we looked
`gap`        where nobody serves the buyer, ranked by what it would take us
===========  ================================================================

**The scoring is deliberately narrow.** A rival is scored only on capabilities
that can be verified from public documentation, and `Coverage.UNKNOWN` is a real
answer that counts against *our* confidence rather than against the rival. A
comparison table with a tick in every one of our rows and a cross in every one of
theirs is a table nobody outside the building believes.

What this is *for* is not the table. It is `gap.py`: the capabilities buyers keep
asking for that nobody currently serves, ranked by leverage — which is the input
to what we build next, and the only part of competitive analysis that changes a
roadmap.
"""

from .gap import Gap, Leverage, Opportunity, opportunities, positioning
from .registry import CAPABILITIES, RECORDED, field, rival_registry
from .rival import Capability, Coverage, Evidence, Rival, Segment

__all__ = [
    "CAPABILITIES",
    "RECORDED",
    "Capability",
    "Coverage",
    "Evidence",
    "Gap",
    "Leverage",
    "Opportunity",
    "Rival",
    "Segment",
    "field",
    "opportunities",
    "positioning",
    "rival_registry",
]
