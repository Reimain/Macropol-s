"""Agent tools — the platform's capabilities, reachable by a model.

Every tool is a **named composition over the verb registry**, never a second
implementation. The obvious design is ten hand-written functions; that would make
the tool set an eleventh place a capability is declared, and it would drift from
the other five the way every parallel restatement does.

Three properties follow from the projection rather than being enforced:

* a tool cannot invent a capability — its pipeline type-checks before it runs;
* adding a verb widens the tool set with no change here;
* every answer carries its reasoning and its gaps, so a model can say what it
  could not see instead of quietly answering as though it had seen everything.

===========  ==============================================================
`tools`      the tool set: named compositions with typed, quoted parameters
`runner`     executing one call, and reporting what limited the answer
===========  ==============================================================

Mutating verbs are unreachable: an agent cannot confirm a change to somebody's
environment on their behalf, so the runner refuses before the guard would.
"""

from __future__ import annotations

from .runner import MAX_ITEMS, ToolResult, ToolRunner
from .tools import Tool, ToolError, ToolParam, ToolSet, builtin_tools

__all__ = [
    "MAX_ITEMS",
    "Tool",
    "ToolError",
    "ToolParam",
    "ToolResult",
    "ToolRunner",
    "ToolSet",
    "builtin_tools",
]
