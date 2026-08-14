"""Capability → tool, as data. The driver table.

A caller asks for a *capability* — "search content", "read history" — and the
registry decides which binary serves it, or reports that none is installed. That
indirection is what makes `rg`-then-`grep`-then-stdlib a configuration rather than
a chain of `if` statements scattered through the discovery layer.

The dispatcher itself is a protocol with a stdlib default, and Gratimos registers
its guarded, risk-analysed executor here as a *driver*. That inversion is how §27
survives invariant 8: the kernel declares the interface and loads a driver; it
never imports Gratimos to get one.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from ..domain.evidence import Evidence, EvidenceKind, SourceLocation
from ..domain.finding import Gap, GapKind
from .local import DispatchError, LocalDispatcher, probe
from .tool import KNOWN, Determinism, Outcome, Tool, known


@runtime_checkable
class Dispatcher(Protocol):
    """What a driver must implement. `LocalDispatcher` is the default."""

    name: str

    def dispatch(
        self,
        tool: Tool,
        arguments: Sequence[str] = (),
        *,
        cwd: str = "",
        stdin: str = "",
        timeout: float | None = None,
    ) -> Outcome:
        ...


class ToolRegistry:
    """Which tool serves which capability, and who runs it."""

    def __init__(
        self,
        tools: Sequence[Tool] = (),
        *,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {
            tool.name: tool for tool in (tools or KNOWN)
        }
        self.dispatcher: Dispatcher = dispatcher or LocalDispatcher()
        #: Every dispatch this registry performed, so a flow can report what it
        #: leaned on and how reproducible that makes it.
        self.outcomes: list[Outcome] = []

    # -- registration ----------------------------------------------------

    def add(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def use(self, dispatcher: Dispatcher) -> Dispatcher:
        """Install a driver. This is the seam Gratimos's guarded executor takes."""
        self.dispatcher = dispatcher
        return dispatcher

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        found = self._tools.get(name)
        if found is None:
            raise DispatchError(
                f"no tool is registered for {name!r}; registered: "
                f"{', '.join(sorted(self._tools))}"
            )
        return found

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    # -- use -------------------------------------------------------------

    def available(self) -> dict[str, bool]:
        """What is installed here. The capability report for this machine."""
        return {name: probe(tool)[0] for name, tool in sorted(self._tools.items())}

    def run(
        self,
        name: str,
        arguments: Sequence[str] = (),
        **options: Any,
    ) -> Outcome:
        """Dispatch one capability, recording the outcome for provenance."""
        outcome = self.dispatcher.dispatch(self.require(name), arguments, **options)
        self.outcomes.append(outcome)
        return outcome

    # -- provenance ------------------------------------------------------

    def evidence_for(self, outcome: Outcome) -> Evidence:
        """A dispatched answer, cited.

        `RUNTIME_TRACE` because that is exactly what it is: the observed behaviour
        of a real tool at a real moment, not a declaration. The excerpt names the
        binary and its version, so a result that differs between two machines is
        attributable rather than mysterious.
        """
        return Evidence(
            kind=EvidenceKind.RUNTIME_TRACE,
            location=SourceLocation(uri=f"tool://{outcome.tool}"),
            extractor=f"slpie.dispatch.{outcome.tool}",
            extractor_version=outcome.version or "unknown",
            excerpt=(
                f"{' '.join(outcome.argv[:6])} → exit {outcome.code}"
                + (f" [{outcome.version}]" if outcome.version else "")
            ),
        )

    def gaps(self) -> tuple[Gap, ...]:
        """One gap per tool that was wanted and missing.

        The fallback is announced rather than substituted quietly. An
        approximation that looks identical to the real answer is worse than a
        slower correct one, because nobody knows to distrust it.
        """
        found: list[Gap] = []
        for outcome in self.outcomes:
            if outcome.available:
                continue
            tool = self._tools.get(outcome.tool)
            found.append(Gap(
                kind=GapKind.CAPABILITY_REFUSED,
                subject=f"tool://{outcome.tool}",
                detail=outcome.error or f"{outcome.tool} is not installed",
                remediation=(
                    f"install {tool.binary} for the faster path"
                    if tool else f"install {outcome.tool}"
                ),
                confidence_impact=0.05,
            ))
        return tuple(found)

    def determinism(self) -> Determinism:
        """How reproducible anything built on these dispatches can claim to be."""
        from .local import determinism_of

        return determinism_of(self.outcomes, self._tools)

    def reproducible(self) -> bool:
        return self.determinism().reproducible

    def report(self) -> dict[str, Any]:
        return {
            "dispatcher": self.dispatcher.name,
            "tools": {name: tool.to_dict() for name, tool in sorted(self._tools.items())},
            "available": self.available(),
            "dispatches": [outcome.to_dict() for outcome in self.outcomes],
            "determinism": self.determinism().value,
            "reproducible": self.reproducible(),
            "note": self.determinism().note,
        }

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools[name] for name in self.names)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ToolRegistry {len(self._tools)} tools via {self.dispatcher.name}>"


_REGISTRY: ToolRegistry | None = None


def registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry()
    return _REGISTRY


def reset() -> None:
    global _REGISTRY
    _REGISTRY = None
