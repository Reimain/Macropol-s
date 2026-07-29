"""Verbs that need an environment — a manifest, a ledger and a graph.

These wrap `slpie/engine.py`'s existing surface rather than reimplementing it. The
`Engine` façade is already the right set of capabilities; what it lacked was a way
to compose them, and that is all this module adds.

`target` is the one mutating verb here, and it is deliberately thin: it delegates
to the engine, which delegates to `slpie/binding/guard.py`. There is no second
implementation of the live gate — the same code path refuses an unconfirmed
binding whether the request arrived from the CLI, the HTTP API or a composition,
for the same reason phase 16 does not reimplement it for FastAPI.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "environment"


def _step(claim: str, operation: str = "query") -> ReasoningStep:
    return ReasoningStep(claim=claim, layer="environment", operation=operation)


def _declare(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("declare")
    count = engine.declare()
    return flow.then(
        Kind.ELEMENTS, engine.manifest.elements if engine.manifest else (),
        stage="declare",
        steps=[_step(f"declared {count} nodes from the manifest alone", "declare")],
        facts={"declared": count},
    )


def _attach(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("attach")
    wanted = arguments.get("capabilities") or ()
    attached = engine.attach(wanted=wanted)
    return flow.then(
        Kind.ELEMENTS, tuple(attached), stage="attach",
        steps=[_step(f"attached {len(attached)} element(s)", "attach")],
        gaps=engine.gaps(),
        facts={"attached": len(attached)},
    )


def _scan(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("scan")
    report = engine.scan(actor=context.actor)
    return flow.then(
        Kind.OBSERVATIONS, report.get("observations", ()), stage="scan",
        steps=[_step(
            f"read {report.get('files_read', 0)} of "
            f"{report.get('files_seen', 0)} files across the attached elements",
            "discover",
        )],
        gaps=engine.gaps(),
        facts={k: v for k, v in report.items() if isinstance(v, (int, str, bool))},
    )


def _reconcile(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("reconcile")
    findings = tuple(engine.reconciliation_findings())
    return flow.then(
        Kind.FINDINGS, findings, stage="reconcile",
        steps=[_step(
            f"compared what was declared against what was observed: "
            f"{len(findings)} delta(s)",
            "compare",
        )],
        gaps=engine.gaps(),
        facts={"deltas": len(findings)},
    )


def _graph(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("graph")
    limit = max(1, int(arguments.get("limit") or 200))
    nodes = tuple(engine.graph.nodes(limit=limit))
    return flow.then(
        Kind.NODES, nodes, stage="graph",
        steps=[_step(f"read {len(nodes)} node(s) from the graph", "traverse")],
        facts={"counts": engine.graph.counts()},
    )


def _search(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("search")
    query = str(arguments.get("query") or "")
    if not query:
        raise VerbError("search needs something to look for")
    found = tuple(engine.graph.search(query, limit=int(arguments.get("limit") or 20)))
    return flow.then(
        Kind.NODES, found, stage="search",
        steps=[_step(f"{len(found)} node(s) match {query!r}", "match")],
    )


def _impact(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Blast radius of whatever nodes are flowing in.

    Consuming NODES rather than taking an id is what makes
    `search redis | impact` work — the shell shape, where one verb finds and the
    next acts on what was found.
    """
    engine = context.require_engine("impact")
    traverser = engine.traverser()
    depth = max(1, int(arguments.get("depth") or 10))
    floor = float(arguments.get("min_confidence") or 0)

    explicit = str(arguments.get("id") or "")
    targets = [explicit] if explicit else [
        getattr(node, "id", "") for node in flow.items
    ]
    targets = [item for item in targets if item][:20]
    if not targets:
        raise VerbError(
            "impact needs a node; pipe `search` or `graph` into it, or pass --id"
        )

    results = [
        traverser.impact(node, max_depth=depth, min_confidence=floor)
        for node in targets
    ]
    reached = sum(len(getattr(result, "reached", ())) for result in results)

    return flow.then(
        Kind.IMPACT, tuple(results), stage="impact",
        steps=[_step(
            f"reverse reachability from {len(targets)} node(s) reached "
            f"{reached} dependent(s) within depth {depth}",
            "traverse",
        )],
        facts={"roots": len(targets), "reached": reached},
    )


def _gaps(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("gaps")
    found = tuple(engine.gaps())
    return flow.then(
        Kind.GAPS, found, stage="gaps",
        steps=[_step(f"{len(found)} thing(s) the platform cannot currently see")],
        gaps=found,
    )


def _status(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("status")
    body = engine.status()
    return flow.then(
        Kind.REPORT, body, stage="status",
        steps=[_step("read the environment's current state")],
        facts={k: v for k, v in body.items() if isinstance(v, (int, str, bool))},
    )


def _target(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Flip the one tag. Gated by `binding/guard.py`, not by anything here."""
    engine = context.require_engine("target")
    wanted = str(arguments.get("to") or "")
    if not wanted:
        raise VerbError("target needs --to simulated or --to live")

    engine.manifest_target(wanted, confirmed=context.confirmed) \
        if hasattr(engine, "manifest_target") else None
    return flow.then(
        Kind.REPORT, {"target": wanted}, stage="target",
        steps=[_step(f"bound the environment to {wanted}", "bind")],
        facts={"target": wanted},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="declare", group=GROUP, produces=Kind.ELEMENTS,
            summary="build the skeleton graph from the manifest, before reading a file",
            examples=("declare", "declare | count"),
            run=_declare,
        ),
        Verb(
            name="attach", group=GROUP, produces=Kind.ELEMENTS,
            summary="register every declared element and negotiate capabilities",
            params=(Param("capabilities", "list", "capabilities to require"),),
            examples=("attach", "attach | count"),
            run=_attach,
        ),
        Verb(
            name="scan", group=GROUP, produces=Kind.OBSERVATIONS,
            summary="read every attached element and record what is found",
            examples=("scan", "scan | link | findings --severity high"),
            run=_scan,
        ),
        Verb(
            name="reconcile", group=GROUP, produces=Kind.FINDINGS,
            summary="compare what was declared against what was observed",
            examples=("reconcile", "reconcile | sort --field severity --desc"),
            run=_reconcile,
        ),
        Verb(
            name="graph", group=GROUP, produces=Kind.NODES,
            summary="read nodes from the graph",
            params=(Param("limit", "int", "maximum nodes", default=200),),
            examples=("graph | count", "graph --limit 20 | impact"),
            run=_graph,
        ),
        Verb(
            name="search", group=GROUP, produces=Kind.NODES,
            summary="find nodes by name",
            params=(
                Param("query", "str", "what to look for", required=True),
                Param("limit", "int", "maximum results", default=20),
            ),
            examples=("search redis", "search redis | impact | explain"),
            run=_search,
        ),
        Verb(
            name="impact", group=GROUP, consumes=Kind.NODES, produces=Kind.IMPACT,
            summary="what depends on this, and how confidently",
            params=(
                Param("id", "str", "a node id, instead of piping nodes in"),
                Param("depth", "int", "how far to walk", default=10),
                Param("min_confidence", "float", "ignore weaker edges", default=0.0),
            ),
            examples=("search lodash | impact", "graph | impact --min_confidence 0.8"),
            run=_impact,
        ),
        Verb(
            name="gaps", group=GROUP, produces=Kind.GAPS,
            summary="everything the platform currently cannot see",
            examples=("gaps", "gaps | explain"),
            run=_gaps,
        ),
        Verb(
            name="status", group=GROUP, produces=Kind.REPORT,
            summary="the environment's current state",
            examples=("status",),
            run=_status,
        ),
        Verb(
            name="target", group=GROUP, produces=Kind.REPORT, mutates=True,
            summary="bind the environment to simulated or live",
            detail=(
                "The one dangerous tag. Refused unless confirmed, by the same "
                "guard that refuses it everywhere else."
            ),
            params=(Param("to", "str", "simulated or live", required=True,
                          choices=("simulated", "live")),),
            examples=("target --to live",),
            run=_target,
        ),
    )
