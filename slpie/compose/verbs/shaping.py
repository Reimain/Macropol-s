"""The shell filters: `head`, `sort`, `count`, `filter`, `json`, `explain`.

These are why the composition analogy holds rather than merely being invoked.
`ls | grep | wc` is powerful because `grep` and `wc` do not care what they are
given — and the same polymorphism here is what turns a fixed verb list into a
combinatorial space.

They consume `Kind.ANY` and produce `Kind.SAME`, so they pass the kind through
untouched. That matters more than it looks: if `head` produced its own kind, then
`scan | head | link` would fail the type check even though it is exactly what
somebody wants, and every filter would have to be special-cased in every
downstream verb.

`explain` is the one that is not a shell filter. It renders the accumulated
reasoning of everything upstream — the payoff for carrying provenance in the pipe
instead of bytes, and the verb that makes a long composition auditable.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb

GROUP = "shaping"


def _step(claim: str, operation: str = "shape") -> ReasoningStep:
    """A shaping step cites nothing because it read nothing.

    Deliberate: `ReasoningPath.grounded` should stay true only when every step
    cites evidence, and a filter inventing a citation to look grounded would
    defeat the check. So shaping steps carry no evidence and the path's
    groundedness reflects the *substantive* stages.
    """
    return ReasoningStep(claim=claim, layer="compose", operation=operation)


def _field(item: Any, name: str) -> Any:
    """One named field of anything — attribute, mapping key, or `to_dict` entry.

    Domain objects are frozen dataclasses, plugin output is dicts, and both flow
    through the same filters. Trying all three is what stops `filter --field kind`
    from working on half the pipeline.
    """
    if isinstance(item, Mapping):
        return item.get(name)
    if hasattr(item, name):
        return getattr(item, name)
    if hasattr(item, "to_dict"):
        try:
            return item.to_dict().get(name)
        except Exception:  # noqa: BLE001 - a plugin's to_dict is not ours
            return None
    return None


def _head(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    limit = max(0, int(arguments.get("count") or 10))
    items = flow.items[:limit]
    return flow.then(
        Kind.SAME, items, stage="head",
        steps=[_step(f"kept the first {len(items)} of {flow.size}")],
    )


def _count(flow: Flow, _arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Replaces the value with its size, keeping the kind.

    Keeping the kind rather than producing a number is what lets `count` sit
    mid-pipeline without breaking what follows — and the count is on the flow's
    facts where a renderer can read it.
    """
    return flow.then(
        Kind.SAME, flow.items, stage="count",
        steps=[_step(f"counted {flow.size}")],
        facts={"count": flow.size},
    )


def _sort(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    field = arguments.get("field") or ""
    descending = bool(arguments.get("desc"))

    def key(item: Any) -> tuple[int, str]:
        value = _field(item, field) if field else item
        # Sorting mixed types raises in Python 3, and a pipeline is not the place
        # to discover that. Everything sorts as text, with `None` last so absent
        # values do not masquerade as the smallest.
        if value is None:
            return (1, "")
        return (0, str(value))

    items = tuple(sorted(flow.items, key=key, reverse=descending))
    return flow.then(
        Kind.SAME, items, stage="sort",
        steps=[_step(
            f"sorted {len(items)} by {field or 'value'}"
            f"{' descending' if descending else ''}"
        )],
    )


def _filter(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    field = arguments.get("field") or ""
    contains = str(arguments.get("contains") or "")
    equals = arguments.get("equals")

    def keep(item: Any) -> bool:
        value = _field(item, field) if field else item
        text = "" if value is None else str(value)
        if equals is not None and text != str(equals):
            return False
        if contains and contains.lower() not in text.lower():
            return False
        return True

    items = tuple(item for item in flow.items if keep(item))
    criterion = (
        f"{field or 'value'}"
        + (f" == {equals!r}" if equals is not None else "")
        + (f" contains {contains!r}" if contains else "")
    )
    return flow.then(
        Kind.SAME, items, stage="filter",
        steps=[_step(f"kept {len(items)} of {flow.size} where {criterion}")],
    )


def _unique(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    field = arguments.get("field") or ""
    seen: set[str] = set()
    items: list[Any] = []
    for item in flow.items:
        value = _field(item, field) if field else item
        marker = str(value)
        if marker in seen:
            continue
        seen.add(marker)
        items.append(item)
    return flow.then(
        Kind.SAME, tuple(items), stage="unique",
        steps=[_step(f"reduced {flow.size} to {len(items)} distinct")],
    )


def _explain(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Render everything upstream. The payoff for a provenance-carrying pipe."""
    lines: list[str] = [f"{flow.kind.label} via {' | '.join(flow.stages) or 'start'}"]

    if len(flow.reasoning):
        lines.append("")
        lines.append("reasoning:")
        lines.append(flow.reasoning.render())

    sources = flow.reasoning.sources
    if sources:
        lines.append("")
        lines.append(f"read: {', '.join(sources[:12])}")

    if flow.gaps:
        lines.append("")
        lines.append("gaps limiting this answer:")
        for gap in flow.gaps:
            lines.append(f"  - [{gap.kind.value}] {gap.detail}")
            if gap.remediation and bool(arguments.get("remediation", True)):
                lines.append(f"      → {gap.remediation}")

    lines.append("")
    lines.append(f"confidence {flow.confidence}" + (
        " (discounted by the gaps above)" if flow.gaps else ""
    ))
    if not flow.grounded and len(flow.reasoning):
        lines.append(
            "NOT GROUNDED: a step in this path cites no evidence, so part of "
            "this answer cannot be traced to a file"
        )

    return flow.then(
        Kind.SAME, flow.value, stage="explain",
        steps=[_step("rendered the accumulated reasoning", "explain")],
        facts={"explanation": "\n".join(lines)},
    )


def _json(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    import json as _jsonlib

    limit = max(1, int(arguments.get("limit") or 200))
    body = _jsonlib.dumps(flow.to_dict(limit=limit), indent=2, default=str)
    return flow.then(
        Kind.SAME, flow.value, stage="json",
        steps=[_step("serialised the flow")],
        facts={"json": body},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="head", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="keep the first N",
            params=(Param("count", "int", "how many to keep", default=10),),
            examples=("scan | head --count 5",),
            run=_head,
        ),
        Verb(
            name="count", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="count what is flowing, without changing it",
            examples=("scan | link | count",),
            run=_count,
        ),
        Verb(
            name="sort", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="order by a field",
            params=(
                Param("field", "str", "the field to order by"),
                Param("desc", "bool", "highest first", default=False),
            ),
            examples=("findings | sort --field severity --desc",),
            run=_sort,
        ),
        Verb(
            name="filter", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="keep only what matches",
            params=(
                Param("field", "str", "the field to test"),
                Param("contains", "str", "substring the field must contain"),
                Param("equals", "str", "value the field must equal exactly"),
            ),
            examples=("scan | filter --field kind --equals depends_on",),
            run=_filter,
        ),
        Verb(
            name="unique", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="drop duplicates",
            params=(Param("field", "str", "the field that identifies a duplicate"),),
            examples=("scan | unique --field subject",),
            run=_unique,
        ),
        Verb(
            name="explain", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="render the reasoning, the sources and the gaps behind this",
            detail=(
                "Every stage upstream contributed to the reasoning path this "
                "renders. It is the verb that makes a long composition auditable "
                "rather than merely convenient."
            ),
            params=(Param("remediation", "bool", "include how to close each gap",
                          default=True),),
            examples=("scan | link | explain", "reconcile | findings | explain"),
            run=_explain,
        ),
        Verb(
            name="json", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="serialise the flow, provenance included",
            params=(Param("limit", "int", "maximum items to render", default=200),),
            examples=("scan | link | json",),
            run=_json,
        ),
    )
