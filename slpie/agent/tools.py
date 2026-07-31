"""Agent tools — a projection of the verb registry, never a second implementation.

The obvious way to build this is ten hand-written functions: `dependency_lookup`,
`impact_analysis`, `blast_radius`, and so on. That is what the plan named, and it
would have been a mistake, because it makes the tool set an *eleventh* place a
capability is declared. Add a verb and the agent cannot use it. Rename a
parameter and the tool silently passes the old one. The CLI, the API, the manual,
the planner and the clients are all projections of `compose/registry.py`
precisely so they cannot drift — and an agent that bypassed it would be the one
surface that could.

So a `Tool` is **a named composition over the registry**, and three properties
fall out of that rather than being enforced separately:

* **A tool cannot invent a capability.** Its pipeline type-checks against the
  registry before it runs, so a model asking for something the platform cannot do
  gets a refusal at plan time rather than a plausible-looking wrong answer.
* **Adding a verb widens the tool set.** `discover . | reason | radius` became
  available to an agent the moment `radius` was registered, with no change here.
* **Every answer carries its provenance.** A tool returns the `Flow`, so the
  reasoning path and the gaps travel to the model exactly as they travel to a
  terminal — which is what lets an agent say "I could not see the vendored tree"
  instead of quietly answering as though it had.

The parameters are typed and substituted, never interpolated. A model supplying
`lodash; rm -rf /` as a package name produces a *quoted argument*, because the
pipeline is assembled through `shlex.quote` and parsed by the same splitter the
CLI uses. There is no shell anywhere in this path, and the quoting is belt to
that brace.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..errors import SlpieError


class ToolError(SlpieError):
    """A tool was called wrongly, or its composition would not run."""


@dataclass(frozen=True, slots=True)
class ToolParam:
    """One argument a tool takes, with the help a model reads to fill it in.

    A parameter is a **value**, never a fragment of command syntax. The first
    version asked a model to supply `"--severity critical"`, which quoted to a
    single argument and produced `govern '--severity critical'` — a flag the
    verb had never heard of. Making the tool own the flag and the model own the
    value removes the whole class: a model cannot mis-spell syntax it never
    writes.

    `flag` is what makes an optional value work. Supplied, it renders
    `--severity critical`; omitted, it renders nothing at all, rather than
    leaving a dangling `--severity` with no argument.
    """

    name: str
    description: str
    required: bool = False
    default: str = ""
    #: The flag this value belongs to, without dashes. Empty means the value is
    #: substituted bare — a positional, or a whole composition.
    flag: str = ""
    #: Substituted without quoting. Only for a composition, which is parsed by
    #: the same splitter the CLI uses and type-checked against the registry —
    #: never by a shell. Quoting one would collapse a pipeline into one token.
    raw: bool = False
    #: A flag that takes no value: `--safe`, not `--safe true`. Rendered bare
    #: when asked for and omitted entirely otherwise, because a verb declaring a
    #: boolean parameter refuses `--safe true` — the `true` would be read as the
    #: next stage's argument.
    boolean: bool = False
    choices: tuple[str, ...] = ()
    #: A value that is valid for this parameter. Shown to a model in the schema,
    #: and used by the suite to prove every tool composes — a required parameter
    #: with no valid example is a tool nobody can check.
    example: str = ""

    def render(self, value: str) -> str:
        """The value as it appears in the composition. Quoted unless `raw`."""
        if not value:
            return ""
        if self.boolean:
            return f"--{self.flag or self.name}" if value.lower() in (
                "true", "yes", "1", "on",
            ) else ""
        if self.raw:
            return value
        quoted = shlex.quote(value)
        return f"--{self.flag} {quoted}" if self.flag else quoted

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "string",
            "description": self.description
            + (f" (default: {self.default})" if self.default else "")
            + (f". For example: {self.example}" if self.example else ""),
        }
        if self.choices:
            body["enum"] = list(self.choices)
        return body

    @property
    def sample(self) -> str:
        """A value that works, for a schema and for a test."""
        return self.example or (self.choices[0] if self.choices else "example")


@dataclass(frozen=True, slots=True)
class Tool:
    """One capability an agent can call: a template, and what fills it in.

    `template` is a composition with `{name}` placeholders. Substitution is by
    `shlex.quote`, so a value containing a pipe, a space or a quote becomes one
    argument rather than three stages.
    """

    name: str
    summary: str
    template: str
    params: tuple[ToolParam, ...] = ()
    detail: str = ""

    def pipeline(self, arguments: Mapping[str, Any] | None = None) -> str:
        """The composition this call becomes. Quoted, never interpolated raw."""
        supplied = dict(arguments or {})
        values: dict[str, str] = {}

        for param in self.params:
            raw = supplied.pop(param.name, None)
            if raw in (None, ""):
                if param.required:
                    raise ToolError(
                        f"{self.name} needs {param.name!r}: {param.description}"
                    )
                values[param.name] = param.render(param.default)
                continue
            value = str(raw)
            if param.choices and value not in param.choices:
                raise ToolError(
                    f"{self.name}: {param.name}={value!r} is not one of "
                    f"{', '.join(param.choices)}"
                )
            values[param.name] = param.render(value)

        if supplied:
            raise ToolError(
                f"{self.name} does not take {', '.join(sorted(supplied))}; it "
                f"takes {', '.join(p.name for p in self.params) or 'nothing'}"
            )
        return self.template.format(**values).strip()

    def to_dict(self) -> dict[str, Any]:
        """The tool as a JSON schema — what a model is shown."""
        return {
            "name": self.name,
            "description": (
                f"{self.summary}. {self.detail}" if self.detail else self.summary
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    param.name: param.to_dict() for param in self.params
                },
                "required": [p.name for p in self.params if p.required],
            },
        }

    def __str__(self) -> str:
        return f"{self.name}: {self.summary}"


#: The tools, each a composition. Ordered so the ones an agent reaches for first
#: appear first — a model reading a list of twenty tools weights the early ones.
def builtin_tools(root: str = ".") -> tuple[Tool, ...]:
    """Every built-in tool, bound to a tree.

    `root` is baked in rather than taken as a parameter on every tool, because a
    model that could choose the directory could read one it was never pointed at.
    The caller decides what the agent can see; the agent decides what to ask.
    """
    where = shlex.quote(root)

    return (
        Tool(
            name="dependency_lookup",
            summary="what this project depends on, resolved onto identities",
            template=f"discover {where} | link",
            detail=(
                "Merges what every manifest and lockfile said onto one node per "
                "package, so a contradiction between two files is visible rather "
                "than one of them silently winning."
            ),
        ),
        Tool(
            name="findings",
            summary="everything currently wrong, ranked worst first",
            template=f"discover {where} | link | findings {{severity}}",
            params=(
                ToolParam(
                    "severity", "keep only this severity", flag="severity",
                    choices=("critical", "high", "medium", "low", "info"),
                ),
            ),
            detail="Each finding cites the file and line it was derived from.",
        ),
        Tool(
            name="governance_scan",
            summary="run every governance rule: vulnerabilities, secrets, licences",
            template=f"discover {where} | govern {{severity}}",
            params=(
                ToolParam(
                    "severity", "keep only this severity", flag="severity",
                    choices=("critical", "high", "medium", "low", "info"),
                ),
            ),
            detail=(
                "A rule lacking the data it needs declines rather than guessing, "
                "and the count of declined rules travels out as a gap -- so a "
                "short list never quietly means nothing was checked."
            ),
        ),
        Tool(
            name="impact_analysis",
            summary="what depends on a package, and how confidently",
            template=f"discover {where} | reason | radius {{package}}",
            params=(
                ToolParam(
                    "package", "the package name to measure the blast radius of",
                    flag="package",
                ),
            ),
            detail=(
                "Confidence propagates as a minimum, so a path reached only "
                "through a dynamic load is reported as the weak thing it is. A "
                "name that matches nothing raises a gap rather than answering "
                "that nothing depends on it."
            ),
        ),
        Tool(
            name="constraint_check",
            summary="whether the declared version ranges can all hold at once",
            template=f"discover {where} | link | constraints",
            detail=(
                "Resolves against versions that were observed, never a network "
                "lookup. An unsatisfiable result names the conflicting pair and "
                "the window each one demands."
            ),
        ),
        Tool(
            name="safe_upgrade",
            summary="which upgrades are available, and what each one costs",
            template=f"discover {where} | reason | options {{safe}}",
            params=(
                ToolParam(
                    "safe", "'true' to keep only upgrades that break nothing",
                    boolean=True, choices=("true", "false"),
                ),
            ),
            detail=(
                "Enumerates; it does not recommend. Which upgrade to take "
                "depends on how well tested this codebase is, which is a "
                "judgement the platform cannot make."
            ),
        ),
        Tool(
            name="architecture_summary",
            summary="the TOGAF views and the deployment topology",
            template=f"discover {where} | enterprise {{view}}",
            params=(
                ToolParam(
                    "view", "which architecture view", flag="view",
                    choices=("application", "data", "technology", "standards",
                             "topology"),
                ),
            ),
        ),
        Tool(
            name="risk_register",
            summary="findings aggregated onto subjects, ranked by reach",
            template=f"discover {where} | govern | risk",
            detail=(
                "A critical in a leaf nobody imports ranks below a high in the "
                "package forty modules touch."
            ),
        ),
        Tool(
            name="sbom",
            summary="a bill of materials in CycloneDX or SPDX",
            template=f"discover {where} | sbom {{format}}",
            params=(
                ToolParam(
                    "format", "which standard", flag="format",
                    choices=("cyclonedx", "spdx"),
                ),
            ),
        ),
        Tool(
            name="graph_explanation",
            summary="the answer, its reasoning, its limits and what to ask next",
            template=f"discover {where} | reason | ask {{question}}",
            params=(
                ToolParam(
                    "question", "what you were trying to find out",
                    flag="question",
                ),
            ),
            detail=(
                "Never a bare value: the answer arrives with the evidence behind "
                "it, the gaps that limit it, and questions that are themselves "
                "runnable."
            ),
        ),
        Tool(
            name="architecture_audit",
            summary="whether this codebase honours its own stated invariants",
            template="audit",
            detail=(
                "Deterministic verdicts over an AST projection, with a "
                "reproducible digest. Declines to rule where it cannot see "
                "rather than passing what it did not examine."
            ),
        ),
        Tool(
            name="run_composition",
            summary="run any composition of verbs directly",
            template="{pipeline}",
            params=(
                ToolParam(
                    "pipeline",
                    "a composition such as 'discover . | link | findings'",
                    required=True,
                    example="discover . | link | findings --severity high",
                    # Unquoted: this *is* a composition, parsed by the same
                    # splitter the CLI uses and type-checked against the
                    # registry. Quoting it would collapse a pipeline into one
                    # token and every call would fail as an unknown verb.
                    raw=True,
                ),
            ),
            detail=(
                "The escape hatch, for a question the named tools do not cover. "
                "It is still type-checked against the registry, so it cannot "
                "reach a capability that does not exist."
            ),
        ),
    )


class ToolSet:
    """The tools an agent may call, and the one way to call them."""

    def __init__(
        self, tools: Iterable[Tool] | None = None, *, root: str = ".",
    ) -> None:
        self.root = root
        self._tools: dict[str, Tool] = {}
        for tool in (tools if tools is not None else builtin_tools(root)):
            self.add(tool)

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ToolError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                f"there is no tool called {name!r}; this build offers "
                f"{', '.join(sorted(self._tools))}"
            )
        return tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def to_dict(self) -> list[dict[str, Any]]:
        """The whole set as JSON schemas — what is handed to a model."""
        return [tool.to_dict() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ToolSet {len(self._tools)} tools on {self.root}>"
