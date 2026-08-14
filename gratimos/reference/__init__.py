"""What this system knows about the world it operates in — with citations.

Two registries, one rule: **no entry without a source.** It is the evidence
discipline the graph already enforces, applied to the reference material itself,
because a hand-written landscape survey ages into confident wrongness faster
than any code does.

* `protocols` — the agent-interoperability protocols (A2A, MCP, the Microsoft
  Agent Framework, the OpenAI and Google SDKs, ACP, AGNTCY), each with its
  owner, its governance, and the URL where the claim can be checked. Ownership
  is recorded precisely because it is routinely misattributed.
* `science` — the vocabulary of *Conjecture Machines: AI agents and the new
  validation bottleneck in science*, mapped to the module that answers each
  idea, or to an explicit admission that nothing does. The `gaps()` list is the
  point of the file.
"""

from __future__ import annotations

from .protocols import (
    RECORDED,
    Governance,
    Layer,
    Protocol,
    ReferenceError,
    Registry,
    protocol_registry,
)
from .science import PAPER, Answered, Reference, Term, Tool, terminology, tools

__all__ = [
    "Protocol", "Registry", "Governance", "Layer", "protocol_registry",
    "RECORDED", "ReferenceError",
    "Reference", "Term", "Tool", "Answered", "terminology", "tools", "PAPER",
]
