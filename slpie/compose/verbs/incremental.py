"""Incremental rescanning and the agent tool set, as verbs.

Both exist as packages already; without verbs they would be capabilities the
platform has and no surface can reach — which is the drift §24 exists to prevent,
and the same mistake phase 12 nearly made with the enterprise views.

`changed` is the interesting one. It produces `REPORT` rather than `OBSERVATIONS`
because it deliberately *does not scan*: it costs a fingerprint pass and tells you
what a rescan would do, so you can decide whether to pay for one. A verb that
quietly ran the scan would remove the only reason to ask.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...domain.finding import Gap, GapKind
from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "incremental"


def _changed(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """What has moved since the baseline, and what it would cost to catch up."""
    from ...incremental import Watcher

    root = Path(str(
        arguments.get("path") or flow.facts.get("scanned_root")
        or context.root or "."
    )).expanduser()
    if not root.exists():
        raise VerbError(f"{root} does not exist")

    # Three states, not two: `--strict`/`--lenient` name the mode explicitly, and
    # neither given defers to `SLPIE_STRICT`. A plain `bool` default here would
    # have made the flag's absence mean "lenient" and silently overridden the
    # environment, which is the opposite of what a production deployment sets it
    # for.
    if arguments.get("lenient"):
        strict: bool | None = False
    elif arguments.get("strict"):
        strict = True
    else:
        strict = None

    # `Param("int")` has already refused anything that is not a whole number, so
    # these are numbers or absent — no second parse, and no second error message
    # saying the same thing in different words.
    watcher = Watcher(
        root,
        baseline=str(arguments["baseline"]) if arguments.get("baseline") else None,
        strict=strict,
        limit=arguments.get("limit"),
        max_bytes=arguments.get("max-bytes"),
    )
    graph = getattr(getattr(context, "engine", None), "graph", None)
    plan = watcher.plan(graph)

    if arguments.get("commit"):
        # Only ever after the caller has acted on the plan. Committing before a
        # rescan would leave a baseline claiming work that never happened, and
        # the next run would skip exactly the files the failed one did not read.
        watcher.commit()

    gaps: tuple[Gap, ...] = ()
    if not plan.trustworthy:
        # Invariant 5 through composition: a caller who piped `changed` into a
        # rescan must see, at the far end, that the plan was drawn over a tree
        # this pass could not read in full.
        gaps = (Gap(
            kind=GapKind.ACCESS_DENIED,
            subject=str(root),
            detail=plan.caveat,
            remediation=(
                "read the reasons under `files not read`, then either fix the "
                "cause or raise --limit / --max-bytes; the nodes those files "
                "justify are neither refreshed nor retired until one is read"
            ),
        ),)

    return flow.then(
        Kind.REPORT, tuple(
            {"uri": uri, "state": state}
            for state, uris in (
                ("added", plan.delta.added),
                ("changed", plan.delta.changed),
                ("removed", plan.delta.removed),
                # Reported, not omitted. A caller acting on this list must be
                # able to see the files the pass could not speak for, or it will
                # read the three states above as the whole truth about the tree.
                ("unknown", plan.delta.unknown),
            )
            for uri in uris
        ),
        stage="changed",
        steps=[ReasoningStep(
            claim=plan.reason, layer="incremental", operation="shape",
        )],
        gaps=gaps,
        facts={
            "changed": plan.render(),
            "incremental": plan.to_dict(),
            "worth_rescanning": plan.worth_it,
            "trustworthy": plan.trustworthy,
        },
    )


def _tools(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """The tool set an agent is handed, as JSON schemas."""
    from ...agent import ToolSet

    tools = ToolSet(root=str(context.root or "."))
    wanted = str(arguments.get("name") or "")
    if wanted:
        chosen = (tools.require(wanted),)
    else:
        chosen = tuple(tools)

    lines = ["", f"  {len(chosen)} tool(s) an agent may call", ""]
    for tool in chosen:
        lines.append(f"  {tool.name}")
        lines.append(f"      {tool.summary}")
        lines.append(f"      {tool.pipeline({p.name: p.sample for p in tool.params if p.required})}")
        for param in tool.params:
            mark = " (required)" if param.required else ""
            lines.append(f"        --{param.name}{mark}  {param.description}")
        lines.append("")

    return flow.then(
        Kind.REPORT, tuple(tool.to_dict() for tool in chosen), stage="agent-tools",
        steps=[ReasoningStep(
            claim=(
                f"{len(chosen)} tool(s), each a composition that type-checks "
                f"against this build's verb registry"
            ),
            layer="agent", operation="shape",
        )],
        facts={"agent_tools": "\n".join(lines), "tool_count": len(chosen)},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="changed", group=GROUP, produces=Kind.REPORT,
            summary="what has moved since the last scan, and what it would cost",
            detail=(
                "Costs a fingerprint pass and nothing else, so you can ask "
                "whether a rescan is worth it before paying for one. Compares "
                "content, never modification time: a `git checkout` rewrites "
                "mtimes on identical files, and a restored build cache writes "
                "older ones than were recorded.\n\n"
                "Never reports a file it did not read as removed. Strict — the "
                "default — refuses to plan at all over a tree it could not read "
                "in full; `--lenient` plans around those files, lists why each "
                "was skipped, and carries a gap saying the plan is partial."
            ),
            params=(
                # No `default="."`. A default is *bound* by `Verb.bind`, so it
                # lands in `arguments` and wins the `or` chain below — which made
                # this verb scan the working directory even when the caller had
                # bound a root on the `Context`.
                Param("path", "path", "the tree to compare; defaults to the "
                      "root this session is bound to"),
                Param("baseline", "path", "where the baseline is kept"),
                Param("commit", "bool", "record the current state as the new "
                      "baseline; do this only after a rescan succeeded",
                      default=False),
                Param("strict", "bool", "refuse to plan over a tree this pass "
                      "could not read in full (the default; SLPIE_STRICT=0 "
                      "turns it off)", default=False),
                Param("lenient", "bool", "report the unread files and plan "
                      "around them instead of refusing — for development",
                      default=False),
                Param("limit", "int", "how many files the walk may read before "
                      "it stops"),
                Param("max-bytes", "int", "the largest file the walk will read"),
            ),
            examples=("changed", "changed --path .", "changed --lenient"),
            run=_changed,
        ),
        Verb(
            name="agent-tools", group=GROUP, produces=Kind.REPORT,
            summary="the capabilities an agent can call, as JSON schemas",
            detail=(
                "Each tool is a named composition over this registry, so a tool "
                "cannot reach a capability that does not exist and adding a verb "
                "widens the set with no change to the agent."
            ),
            params=(Param("name", "str", "one tool, instead of every one"),),
            examples=("agent-tools", "agent-tools --name impact_analysis"),
            run=_tools,
        ),
    )
