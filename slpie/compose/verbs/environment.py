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


def _scenarios() -> tuple[str, ...]:
    """The scenario names, read from the registry rather than typed here.

    A hand-written list would be the twelfth place a capability is declared, and
    the one that goes stale the first time somebody adds a scenario — which is
    the whole argument for the verb registry, applied one level down.
    """
    from ...simulator.scenarios import available

    return available()


def _declare(flow: Flow, _arguments: Mapping[str, Any], context: Context) -> Flow:
    engine = context.require_engine("declare")
    count = engine.declare()
    # `declarations`, not `elements`. The wrong name here made every `declare`
    # composition fail with an AttributeError, and nothing caught it because
    # this module was the one at 27% coverage — a verb no test ran.
    return flow.then(
        Kind.ELEMENTS,
        tuple(engine.manifest.declarations) if engine.manifest else (),
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
    # `report["observations"]` is a *count* — `ScanReport.to_dict` says so, and
    # excludes `captured` on purpose so a status call does not ship ten thousand
    # rows. Reading it here put an integer on a flow declaring OBSERVATIONS, so
    # every verb that consumes them and actually reads them — `govern`, `link`,
    # `warehouse` — died on `'int' object has no attribute 'evidence'`. A typed
    # pipe whose type is a lie is worse than an untyped one: the composition
    # checks out and then crashes at the second stage.
    return flow.then(
        Kind.OBSERVATIONS, tuple(engine.observed or ()), stage="scan",
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


def _boundary_rule(engine: Any) -> Any:
    """The manifest's own boundary-membership rule, or nothing.

    `SecurityPosture.boundary_for` already decides what sits inside a declared boundary,
    and it is the rule the operator wrote once in `security.boundaries`. Asking
    it per node keeps the cost proportional to what the walk reached; resolving
    the whole membership set up front would scan the node table on every
    selection.
    """
    security = getattr(getattr(engine, "manifest", None), "security", None)
    if security is None or not getattr(security, "boundaries", ()):
        return None
    return lambda node: security.boundary_for(node.display) is not None


def _interest(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Degree of interest around a selection — the render set, bounded by the question.

    This is the verb behind the graph screen's first frame. It exists because
    the alternative was drawing the estate and hoping the renderer kept up, and
    no useful question about an estate returns twenty thousand nodes.

    Severity is deliberately *not* read here. The graph holds no findings
    projection, and running governance inside a read verb would hide an
    expensive stage behind a cheap-looking one. The surveyor takes severities as
    an injection instead, so the caller that already has findings on screen is
    the one that supplies them.
    """
    from ...graph.interest import BUDGET, HORIZON, Surveyor

    engine = context.require_engine("interest")
    horizon = max(1, int(arguments.get("horizon") or HORIZON))
    budget = max(1, int(arguments.get("budget") or BUDGET))
    threshold = arguments.get("threshold")

    explicit = str(arguments.get("id") or "")
    focus = [explicit] if explicit else [getattr(node, "id", "") for node in flow.items]
    focus = [item for item in focus if item][:20]
    if not focus:
        raise VerbError(
            "interest needs a selection; pipe `search` or `graph` into it, or pass --id"
        )

    boundary = _boundary_rule(engine)
    surveyor = Surveyor(engine.graph, boundary=boundary)
    field = surveyor.field(
        focus, horizon=horizon, budget=budget,
        threshold=float(threshold) if threshold not in (None, "") else None,
    )

    return flow.then(
        Kind.REPORT, field.to_dict(), stage="interest",
        steps=[_step(
            f"degree of interest from {len(focus)} selected node(s): {field.summary()}",
            "rank",
        )],
        facts={
            "focus": len(focus), "rendered": len(field.rendered),
            "hidden": field.hidden, "threshold": round(field.threshold, 4),
            "boundaries": boundary is not None,
        },
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


def _simulate(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Materialise the declared world as real files, then bind to it.

    Real artifacts, not mocks: a genuine `package-lock.json`, a genuine git
    repository, genuine Kubernetes YAML. The same discoverers and the same
    plugins run against it unchanged, which is what makes a green simulator case
    evidence about the real code path rather than about the fixtures.
    """
    engine = context.require_engine("simulate")
    # Named `at`, not `root`. `Context.root` already means "the tree being
    # examined", and a parameter of the same name would materialise a default
    # into `arguments` that wins the `or` chain against it — the shadowing bug
    # `changed` already had.
    world = engine.simulate(root=arguments.get("at") or None)

    elements = tuple(engine.manifest.declarations) if engine.manifest else ()
    artifacts = tuple(world.artifacts)
    return flow.then(
        Kind.ELEMENTS, elements, stage="simulate",
        steps=[_step(
            f"materialised {len(elements)} declared element(s) as "
            f"{len(artifacts)} real artifact(s) under {world.root}",
            "materialize",
        )],
        gaps=engine.gaps(),
        facts={"root": str(world.root),
               "elements": len(elements), "artifacts": len(artifacts)},
    )


def _fire(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Fire a scripted condition at the simulated world.

    The outcome carries what the platform *ought* to conclude —
    `expect_findings` and `expect_gaps` — as data rather than as a docstring, so
    a caller can assert against it instead of reading the result and agreeing
    with itself.
    """
    from ...simulator.scenarios import available

    engine = context.require_engine("fire")
    name = str(arguments.get("scenario") or "")
    if not name:
        raise VerbError(
            f"fire needs a scenario name; this build has {', '.join(available())}"
        )

    parameters = {
        key: value
        for key, value in (("package", arguments.get("package")),
                           ("version", arguments.get("version")))
        if value
    }
    outcome = engine.fire(name, **parameters)

    return flow.then(
        Kind.REPORT, outcome.to_dict(), stage="fire",
        steps=[_step(
            f"fired {outcome.scenario}: {outcome.detail}", "simulate",
        )],
        gaps=engine.gaps(),
        facts={
            "scenario": outcome.scenario,
            "changed": len(outcome.changed),
            "expect_findings": list(outcome.expect_findings),
            "expect_gaps": list(outcome.expect_gaps),
        },
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
            name="simulate", group=GROUP, produces=Kind.ELEMENTS, mutates=True,
            summary="materialise the declared world as real files on disk",
            detail=(
                "Writes genuine artifacts — a real `package-lock.json`, a real "
                "git repository, real Kubernetes YAML — rather than mocks, so "
                "the same discoverers and the same plugins run against it "
                "unchanged. A green simulator case is therefore evidence about "
                "the real code path.\n\n"
                "`mutates` because it writes to a filesystem, so a composition "
                "containing it is confirmed as a whole at plan time through the "
                "same guard that refuses an unconfirmed live binding."
            ),
            params=(
                Param("at", "str", "where to materialise it; a temp directory "
                      "is used when this is omitted"),
            ),
            # Not `simulate | attach | scan`: those three are all source verbs,
            # each reading the engine rather than the flow, and `attach`
            # accepting an upstream would also make `findings | attach`
            # type-check — the refusal the composition tests use as their
            # canonical example. They are separate invocations against one
            # engine, which is what `acceptance.py` does.
            examples=("simulate", "simulate --at ./world"),
            run=_simulate,
        ),
        Verb(
            name="fire", group=GROUP, produces=Kind.REPORT, mutates=True,
            summary="fire a scripted condition at the simulated world",
            detail=(
                "A CVE lands, a major version bumps, a service dies, a boundary "
                "is breached. The outcome carries what the platform *ought* to "
                "conclude — `expect_findings` and `expect_gaps` — as data, so a "
                "caller can assert against it rather than reading the result and "
                "agreeing with itself.\n\n"
                "Requires a world: run `simulate` first."
            ),
            params=(
                Param("scenario", "str", "which condition to fire", required=True,
                      choices=_scenarios()),
                Param("package", "str", "the package the scenario acts on, "
                      "where it takes one"),
                Param("version", "str", "the version the scenario acts on, "
                      "where it takes one"),
            ),
            examples=("fire cve --package lodash", "fire boundary-breach"),
            run=_fire,
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
            name="interest", group=GROUP, consumes=Kind.NODES, produces=Kind.REPORT,
            summary="what a selection makes worth drawing, and what it hides",
            params=(
                Param("id", "str", "a node id, instead of piping nodes in"),
                Param("horizon", "int", "how many hops count as the neighbourhood", default=6),
                Param("budget", "int", "how many nodes render as themselves", default=200),
                Param("threshold", "float", "cut here instead of at the budget"),
            ),
            examples=(
                "search lodash | interest",
                "search redis | interest --horizon 3 --budget 40",
            ),
            run=_interest,
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
