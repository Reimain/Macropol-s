"""Verbs that reach an external tool instead of reimplementing it.

`tools` reports what this machine has. `tool` pipes the flow through a registered
binary. `history` asks git, because git's answer about a repository is the only
correct one.

The design decision worth stating is what `tool` is *not*. The obvious shape would
be `... | sh 'grep -v test'` — a shell string. That is rejected: nothing in the
dispatch path runs a shell, and adding one here for convenience would put an
injection surface at the exact point where user text meets execution. So `tool`
names a **registered** capability and passes argv, and the set of dispatchable
things is the registry rather than "anything you can type".

The fidelity trade is explicit rather than quiet. `tool` produces `TEXT`, because a
binary reads bytes and returns bytes — the typed objects do not survive. That is
why `filter`, `sort` and `head` stay in-process: they compare a `Severity`, and
`grep` can only compare a rendering of one. Piping to `tool` is the user choosing
to leave the typed world, and the kind changing to `TEXT` is that choice made
visible in the composition's signature.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ...dispatch import Determinism, registry as tool_registry
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "dispatch"


def _tools(flow: Flow, _arguments: Mapping[str, Any], _context: Context) -> Flow:
    """What can be dispatched to here, and how reproducible each one is."""
    tools = tool_registry()
    report = tools.report()

    return flow.then(
        Kind.REPORT, report, stage="tools",
        steps=[ReasoningStep(
            claim=(
                f"{sum(1 for ok in report['available'].values() if ok)} of "
                f"{len(report['available'])} known tools are installed here"
            ),
            layer="dispatch", operation="probe",
        )],
        gaps=tools.gaps(),
        facts={
            "available": report["available"],
            "dispatcher": report["dispatcher"],
        },
    )


def _tool(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Pipe the rendered flow through a registered binary.

    The kind becomes TEXT, which is the fidelity loss made visible: a binary reads
    bytes and returns bytes, so the typed objects do not come back. Downstream
    verbs that need objects will now correctly refuse to follow, rather than
    receiving strings and producing a plausible wrong answer.
    """
    name = str(arguments.get("name") or "")
    if not name:
        raise VerbError("tool needs --name, e.g. `tool --name jq --args '.kind'`")

    tools = tool_registry()
    if name not in tools:
        raise VerbError(
            f"{name!r} is not a dispatchable tool; this platform knows "
            f"{', '.join(tools.names)}. Nothing here runs a shell, so only "
            f"registered capabilities can be reached"
        )

    raw = arguments.get("args") or ()
    argv = tuple(raw) if isinstance(raw, (list, tuple)) else tuple(
        part for part in str(raw).split() if part
    )

    payload = _render(flow)
    outcome = tools.run(name, argv, stdin=payload)

    if not outcome.available:
        # A missing binary is an answer. The flow passes through unchanged and the
        # gap says what was not applied, rather than pretending the filter ran.
        return flow.then(
            Kind.SAME, flow.value, stage="tool",
            steps=[ReasoningStep(
                claim=f"{name} is not installed, so nothing was applied",
                layer="dispatch", operation="probe", confidence=0.0,
            )],
            gaps=tools.gaps(),
            facts={"tool": name, "applied": False},
        )

    if not outcome.ok:
        raise VerbError(
            f"{name} exited {outcome.code}: "
            f"{outcome.stderr.strip().splitlines()[:1] or ['no message']}"
        )

    tool = tools.require(name)
    steps = [ReasoningStep(
        claim=(
            f"{name} processed {flow.size} item(s) and returned "
            f"{len(outcome.lines)} line(s)"
        ),
        layer="dispatch", operation="dispatch",
        evidence=(tools.evidence_for(outcome),),
    )]

    # An environment-bound tool means anything built on this flow is no longer
    # reproducible across machines, and saying so is the whole point of tracking
    # determinism rather than assuming it.
    if tool.determinism is not Determinism.DETERMINISTIC:
        steps.append(ReasoningStep(
            claim=f"this answer {tool.determinism.note}",
            layer="dispatch", operation="dispatch", confidence=0.0,
            evidence=(tools.evidence_for(outcome),),
        ))

    return flow.then(
        Kind.TEXT, tuple(outcome.lines), stage="tool",
        steps=steps, gaps=tools.gaps(),
        facts={
            "tool": name, "applied": True, "version": outcome.version,
            "determinism": tool.determinism.value,
            "reproducible": tools.reproducible(),
            "truncated": outcome.truncated,
        },
    )


def _history(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Recent commits, from git rather than from a reimplementation of git."""
    tools = tool_registry()
    count = max(1, int(arguments.get("count") or 20))
    path = str(arguments.get("path") or context.root or ".")

    outcome = tools.run(
        "git",
        ["log", f"-{count}", "--pretty=format:%h\x1f%an\x1f%ad\x1f%s", "--date=iso"],
        cwd=path,
    )

    if not outcome.available or not outcome.ok:
        return flow.then(
            Kind.REPORT, {"commits": [], "available": outcome.available},
            stage="history",
            steps=[ReasoningStep(
                claim=(
                    outcome.error or f"git exited {outcome.code}; no history read"
                ),
                layer="dispatch", operation="probe", confidence=0.0,
            )],
            gaps=tools.gaps(),
            facts={"commits": 0},
        )

    commits = []
    for line in outcome.lines:
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({
                "sha": parts[0], "author": parts[1],
                "date": parts[2], "subject": parts[3],
            })

    return flow.then(
        Kind.REPORT, {"commits": commits}, stage="history",
        steps=[ReasoningStep(
            claim=f"git reported {len(commits)} commit(s) under {path}",
            layer="dispatch", operation="dispatch",
            evidence=(tools.evidence_for(outcome),),
        )],
        facts={"commits": len(commits), "git": outcome.version},
    )


def _render(flow: Flow) -> str:
    """The flow as text for a binary's stdin. One item per line."""
    if "json" in flow.facts:
        return str(flow.facts["json"])
    if flow.kind is Kind.TEXT:
        return "\n".join(str(item) for item in flow.items) + "\n"
    return "\n".join(str(item) for item in flow.items) + "\n"


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="tools", group=GROUP, produces=Kind.REPORT,
            summary="which external tools are installed, and how reproducible each is",
            detail=(
                "A missing tool is a capability gap rather than a failure: the "
                "fallback runs and is announced, because an approximation that "
                "looks identical to the real answer is worse than a slower "
                "correct one."
            ),
            examples=("tools", "tools | json"),
            run=_tools,
        ),
        Verb(
            name="tool", group=GROUP, consumes=Kind.ANY, produces=Kind.TEXT,
            summary="pipe the flow through a registered external tool",
            detail=(
                "Produces TEXT, because a binary reads bytes and returns bytes — "
                "the typed objects do not survive. That is the fidelity loss made "
                "visible in the signature, so downstream verbs needing objects "
                "correctly refuse to follow. Nothing here runs a shell: only "
                "registered capabilities can be named, and arguments are passed "
                "as argv."
            ),
            params=(
                Param("name", "str", "the registered tool", required=True),
                Param("args", "str", "arguments, passed as argv"),
            ),
            examples=(
                "discover . | json | tool --name jq --args .kind",
            ),
            run=_tool,
        ),
        Verb(
            name="history", group=GROUP, produces=Kind.REPORT,
            summary="recent commits, from git rather than from a reimplementation",
            params=(
                Param("count", "int", "how many commits", default=20),
                Param("path", "path", "the repository", default="."),
            ),
            examples=("history --count 5", "history | json"),
            run=_history,
        ),
    )
