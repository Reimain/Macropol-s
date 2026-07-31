"""Executing a tool call, and answering with what limited the answer.

The whole reason an agent over this platform is worth building is that it can say
*why* — and the whole reason agents over other platforms cannot is that the tool
returned a value and threw the provenance away. So a `ToolResult` carries four
things, and three of them are the ones a model needs in order not to overclaim:

* the **answer**, rendered for a model rather than for a terminal;
* the **reasoning**, so "how do you know" has a reply;
* the **gaps**, so the model can say what it could not see;
* the **confidence**, already discounted by those gaps.

Two decisions worth defending:

**A failed tool call is a result, not an exception.** A model handed a stack
trace produces an apology; a model handed "that composition is invalid because
`findings` produces FINDINGS and `attach` consumes ELEMENTS" produces a corrected
call. So every failure comes back as a `ToolResult` with `ok=False` and a message
written to be read by whatever is going to try again.

**Output is bounded before it reaches the model.** A scan of a large monorepo
produces more findings than fit in any context window, and a tool that returned
all of them would blow the window and lose the beginning — including the part
that said what was missing. `MAX_ITEMS` truncates, and the truncation is
*reported* rather than silent, because a model that thinks it has seen everything
will say so.

Each call runs in its own `Context`, so its spill session is isolated from every
other call and swept when the call ends. Concurrent agents therefore share a
memory ceiling and share nothing else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..compose import Composition, CompositionError, Context, ParseError, VerbError
from ..compose import registry as verb_registry
from ..errors import SlpieError
from .tools import Tool, ToolError, ToolSet

#: How many items of an answer a model is shown. Enough to reason over, few
#: enough that the gaps at the end of the payload survive the context window.
MAX_ITEMS = 40

#: How long one item's rendering may be. A single finding with a 4,000-character
#: detail would otherwise consume the budget the other thirty-nine needed.
MAX_ITEM_CHARS = 400


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What one tool call produced, and everything qualifying it."""

    tool: str
    pipeline: str
    ok: bool = True
    kind: str = ""
    size: int = 0
    items: tuple[str, ...] = ()
    truncated: int = 0
    reasoning: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    confidence: float = 0.0
    grounded: bool = False
    error: str = ""
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "pipeline": self.pipeline, "ok": self.ok,
            "kind": self.kind, "size": self.size, "items": list(self.items),
            "truncated": self.truncated, "reasoning": list(self.reasoning),
            "gaps": list(self.gaps), "confidence": self.confidence,
            "grounded": self.grounded, "error": self.error,
            "duration": round(self.duration, 4),
        }

    def render(self) -> str:
        """The result as text for a model. Structured, and honest about limits."""
        if not self.ok:
            return f"FAILED: {self.error}\n\n(the composition was: {self.pipeline})"

        lines = [f"{self.kind or 'result'}: {self.size} item(s)", ""]
        lines.extend(f"  {item}" for item in self.items)
        if self.truncated:
            # Stated, not silent. A model that believes it has seen everything
            # will tell the user so.
            lines.append(
                f"  … {self.truncated} more not shown; narrow the question or "
                f"filter by severity to see them"
            )

        if self.reasoning:
            lines += ["", "how this was reached:"]
            lines.extend(f"  {step}" for step in self.reasoning)

        if self.gaps:
            lines += ["", "what limits this answer:"]
            lines.extend(f"  - {gap}" for gap in self.gaps)

        lines += [
            "",
            f"confidence {self.confidence}"
            + ("" if self.grounded else " (not every claim traces to a file)"),
        ]
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


class ToolRunner:
    """Runs tool calls against the verb registry. One session per call."""

    def __init__(
        self,
        *,
        root: str = ".",
        tools: ToolSet | None = None,
        verbs: Any = None,
        engine: Any = None,
        actor: str = "agent",
    ) -> None:
        self.root = root
        self.tools = tools if tools is not None else ToolSet(root=root)
        self.verbs = verbs if verbs is not None else verb_registry()
        self.engine = engine
        self.actor = actor

    def describe(self) -> list[dict[str, Any]]:
        """The tool schemas, for handing to a model."""
        return self.tools.to_dict()

    def call(
        self, name: str, arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Run one tool call. Never raises — a failure is a result."""
        started = time.monotonic()
        pipeline = ""
        try:
            tool = self.tools.require(name)
            pipeline = tool.pipeline(arguments)
            composition = Composition.read(pipeline, verbs=self.verbs)
        except (ToolError, ParseError, VerbError, CompositionError) as error:
            return ToolResult(
                tool=name, pipeline=pipeline, ok=False, error=str(error),
                duration=time.monotonic() - started,
            )

        validation = composition.validate()
        if not validation.ok:
            # Refused before anything runs, and the message names both kinds so
            # the next call can be right rather than merely different.
            return ToolResult(
                tool=name, pipeline=pipeline, ok=False,
                error=validation.explain(),
                duration=time.monotonic() - started,
            )

        # Mutating verbs are never reachable from an agent. The guard lives at
        # the write side and would refuse anyway; refusing here as well means a
        # model is not even shown the affordance.
        if validation.needs_confirmation:
            return ToolResult(
                tool=name, pipeline=pipeline, ok=False,
                error=(
                    f"{', '.join(validation.mutating)} would change the "
                    f"environment; an agent cannot confirm that on somebody's "
                    f"behalf. Ask the operator to run it."
                ),
                duration=time.monotonic() - started,
            )

        # One context per call: its spill session is isolated from every other
        # call and swept on the way out, so concurrent agents share a ceiling
        # and nothing else.
        with Context(
            root=self.root, actor=self.actor, engine=self.engine,
        ) as context:
            result = composition.run(context)
            return self._result(name, pipeline, result, started)

    def _result(
        self, name: str, pipeline: str, result: Any, started: float,
    ) -> ToolResult:
        flow = result.flow
        if not result.ok:
            return ToolResult(
                tool=name, pipeline=pipeline, ok=False,
                error=f"the composition stopped at `{result.failed}`: {result.error}",
                # The partial flow's gaps still travel: they say what had already
                # been found to be missing when it stopped.
                gaps=tuple(gap.detail for gap in flow.gaps[:6]),
                duration=time.monotonic() - started,
            )

        rendered, truncated = _items(flow)
        return ToolResult(
            tool=name, pipeline=pipeline, ok=True,
            kind=flow.kind.value, size=flow.size,
            items=rendered, truncated=truncated,
            reasoning=tuple(
                step.claim for step in flow.reasoning.steps[-6:]
            ),
            gaps=tuple(gap.detail for gap in flow.gaps[:6]),
            confidence=flow.confidence, grounded=flow.grounded,
            duration=time.monotonic() - started,
        )


def _items(flow: Any) -> tuple[tuple[str, ...], int]:
    """The answer as bounded text.

    Slices rather than iterating: on a spilled flow this reads one block instead
    of walking every one of them for forty lines.
    """
    from ..spill import SpilledSequence

    value = flow.value
    if isinstance(value, (tuple, list, SpilledSequence)):
        head = list(value[:MAX_ITEMS])
        rest = max(0, len(value) - len(head))
    else:
        # A scalar answer — a report, a solution, a guidance object. Prefer the
        # fact a verb prepared for a human, since that is already the readable
        # form of exactly this value.
        for name in ("answer", "enterprise", "risk", "rules", "audit", "guidance"):
            if name in flow.facts:
                text = str(flow.facts[name]).strip().splitlines()
                return tuple(text[:MAX_ITEMS]), max(0, len(text) - MAX_ITEMS)
        head, rest = [value], 0

    return tuple(_short(item) for item in head), rest


def _short(item: Any) -> str:
    text = " ".join(str(item).split())
    return text if len(text) <= MAX_ITEM_CHARS else text[:MAX_ITEM_CHARS] + "…"
