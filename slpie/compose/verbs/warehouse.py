"""The warehouse as verbs — facts out, and a template to read them through.

Four, and the split between them is the point this whole layer exists to make:

    warehouse           build the star schema from the graph
    warehouse-export    write it as CSV, JSON or SQL
    warehouse-load      materialise it into the store the platform already has
    dashboard           choose a template for a demand and fill it

The first three end in *data*. The fourth ends in a screen, and it is one
consumer of the first three rather than the only way to reach them — which is
the correction this package was built for. Before it, every number the platform
derived left through a Mermaid diagram or a Markdown report, and a question the
report did not anticipate had no answer at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...domain.finding import Gap, GapKind
from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "warehouse"

#: The components that draw rows rather than a single number. A `stat` handed a
#: thousand rows would carry them across the wire to render one of them.
ROW_COMPONENTS = frozenset({"grid", "table", "auto"})


def _measure(name: str):
    from ...warehouse.measures import measure

    return measure(name)


def _step(claim: str, operation: str = "aggregate") -> ReasoningStep:
    return ReasoningStep(claim=claim, operation=operation, layer="warehouse")


def _gap(subject: str, detail: str) -> Gap:
    return Gap(kind=GapKind.NOT_IMPLEMENTED, subject=subject, detail=detail,
               confidence_impact=0.0)


def _built(flow: Flow, arguments: Mapping[str, Any], context: Context):
    """Build the warehouse from whatever the flow is carrying.

    Observations rather than a live engine, for the reason `govern` takes them:
    the tree that most needs a warehouse is often one nobody has declared yet,
    and a BI layer that only worked after `slpie declare` would be the wrong
    half of the capability.

    The graph is closed before returning. `view_of` is a context manager and
    the `Warehouse` it produces holds only *rows* — plain mappings, already
    read — so nothing downstream needs the connection to stay open. A build
    that returned while holding it would leak one per verb invocation.

    ── Why `--govern` exists rather than a `govern | warehouse` pipe ────

    `govern` produces FINDINGS and `warehouse` consumes OBSERVATIONS, so the
    two cannot be piped: by the time findings exist the observations they were
    derived from are no longer on the flow. The obvious fix — having the
    warehouse consume ANY — would type-check and produce a *worse* answer, an
    elements star with no findings or a findings star with no elements, because
    a flow only ever carries one of them.

    So the rules run here, over the graph that is already open. It costs a
    rules pass and no second scan, and it is opt-in because a warehouse of
    what exists is a legitimate thing to want without paying for governance.
    """
    from ...governance.view import view_of
    from ...warehouse.build import build

    manifest = getattr(context.engine, "manifest", None) if context.engine else None
    with view_of(flow.items) as graph:
        findings = _governed(graph, manifest, arguments, context) \
            if arguments.get("govern") else ()
        return build(graph, findings=findings, manifest=manifest,
                     now=int(arguments.get("now") or 0))


def _governed(graph: Any, manifest: Any, arguments: Mapping[str, Any],
              context: Context) -> tuple[Any, ...]:
    """Every open finding over this graph, using the same rule set `govern` uses.

    The identical `RuleSet` and `RuleContext`, so a number here and a number
    from `govern` cannot disagree — which is the entire argument for a
    warehouse and would be undone by a second evaluation path.
    """
    import time

    from ...governance.builtins import builtins
    from ...governance.rules import RuleContext

    ruleset = builtins()
    evaluation = ruleset.evaluate(RuleContext(
        graph=graph, manifest=manifest, sources={}, facts={},
        now=int(arguments.get("now") or time.time()),
        source_uri=str(context.root),
    ))
    return tuple(evaluation.findings)


def _warehouse(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Project the graph into facts and dimensions."""
    from ...warehouse.measures import summarise

    built = _built(flow, arguments, context)
    wanted = str(arguments.get("star") or "")
    stars = [item for item in built.stars if not wanted or item.name == wanted]
    if wanted and not stars:
        from ...warehouse.schema import names

        raise VerbError(
            f"no star named {wanted!r}; this build publishes {', '.join(names())}"
        )

    return flow.then(
        Kind.REPORT,
        {
            **built.to_dict(),
            "measures": {
                item.name: summarise(item.fact.name, item.fact.rows)
                for item in stars
            },
        },
        stage="warehouse",
        steps=[_step(
            f"built {len(built.tables)} table(s) across {len(built.stars)} star(s)"
        )],
        gaps=[_gap("warehouse", detail) for detail in built.gaps],
        facts={"tables": len(built.tables),
               "rows": sum(len(table) for table in built.tables)},
    )


def _export(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Write the warehouse out in a form a BI tool reads."""
    from ...warehouse import export as writer

    built = _built(flow, arguments, context)
    fmt = str(arguments.get("format") or "csv").lower()
    if fmt not in ("csv", "json", "sql"):
        raise VerbError(
            f"no exporter for {fmt!r}; this build writes csv, json or sql. "
            f"Parquet needs `slpie[enterprise]`."
        )

    tables = built.tables
    destination = str(arguments.get("out") or "")
    written: tuple[str, ...] = ()
    if destination:
        written = writer.write(
            tables, Path(context.root) / destination, fmt=fmt,
            dialect=str(arguments.get("dialect") or "sqlite"),
        )

    render = {"csv": writer.to_csv, "json": writer.to_json, "sql": writer.to_sql}[fmt]
    return flow.then(
        Kind.REPORT,
        {
            "format": fmt,
            "written": list(written),
            "tables": {table.name: render(table) for table in tables} if not written else {},
            "dictionary": writer.dictionary(tables),
        },
        stage="warehouse export",
        steps=[_step(f"wrote {len(tables)} table(s) as {fmt}", "generate")],
        gaps=[_gap("warehouse export", detail) for detail in built.gaps],
        facts={"format": fmt, "written": len(written)},
    )


def _load(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Materialise the warehouse into the store the platform already has.

    Into the *existing* database, so any BI tool that speaks SQL connects to
    something that is already deployed, backed up and credentialed. A separate
    analytics database would be a second thing to keep in step with the graph,
    and the graph is where the truth lives.
    """
    import sqlite3

    from ...warehouse.export import load

    built = _built(flow, arguments, context)
    target = str(arguments.get("database") or "")
    if not target:
        raise VerbError(
            "warehouse-load needs --database, the SQLite file to materialise "
            "into. For Postgres, set SLPIE_DATABASE_URL and use "
            "`slpie[enterprise]`."
        )

    path = Path(context.root) / target
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        written = load(connection, built.tables, dialect="sqlite")
    finally:
        connection.close()

    return flow.then(
        Kind.REPORT,
        {"database": str(path), "rows": written, "gaps": list(built.gaps)},
        stage="warehouse load",
        steps=[_step(f"materialised {len(written)} table(s) into {path.name}", "generate")],
        gaps=[_gap("warehouse load", detail) for detail in built.gaps],
        facts={"tables": len(written), "rows": sum(written.values())},
    )


def _fill(panel: Any, star: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The rows a panel draws, and the columns it draws them in.

    A panel that carries only its measures is a headline with no body: the stat
    tiles render and the bar chart underneath them is empty, which is exactly
    the "table dump with extra steps" this layer exists to replace. So the fill
    happens here, once, and the browser receives a panel it can draw with no
    second request — which is also what makes the dashboard renderable offline
    from a flow somebody saved.

    A panel whose star is missing returns empty rather than raising. The verb
    turns that into a stated gap; a raise would lose the four panels that *did*
    fill because the fifth reads a queue this ring cannot see.
    """
    from ...warehouse import query

    if star is None:
        return [], []

    options = dict(panel.options or {})
    limit = int(options.get("limit") or 0)

    if panel.by:
        name = panel.measures[0] if panel.measures else ""
        found = _measure(name)
        if found is None:
            return [], []
        drawn = query.breakdown(star, panel.by, found, limit=limit)
        if options.get("order") == "rank":
            # The dimension's declared order, not the measure's. `by severity`
            # wants critical first even when `info` is the bigger number, and
            # sorting by value would bury the thing the reader opened this for.
            drawn.sort(key=lambda row: row.get("rank", -1),
                       reverse=bool(options.get("descending", True)))
        return drawn, [
            {"key": "label", "label": panel.by.replace("_", " "),
             "align": "", "density": "", "format": "", "link": ""},
            {"key": "value", "label": "value", "align": "right",
             "density": "", "format": "", "link": ""},
        ]

    if panel.component in ROW_COMPONENTS:
        return (
            query.rows(star, sort=str(options.get("sort") or ""),
                       descending=bool(options.get("descending", True)),
                       limit=limit),
            query.columns(star),
        )
    return [], []


def _dashboard(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Pick a template for the demand and fill it from the warehouse."""
    from ...present.template import Demand, select
    from ...present.templates import TEMPLATES, template
    from ...warehouse.measures import measure

    built = _built(flow, arguments, context)

    named = str(arguments.get("template") or "")
    if named:
        chosen = template(named)
        if chosen is None:
            from ...present.templates import keys

            raise VerbError(
                f"no template named {named!r}; this build has {', '.join(keys())}"
            )
        selection = None
    else:
        selection = select(Demand(
            utility=str(arguments.get("utility") or ""),
            context=str(arguments.get("for") or ""),
            domain=str(arguments.get("domain") or ""),
            about=str(arguments.get("about") or ""),
        ), TEMPLATES)
        chosen = selection.template

    panels = []
    unresolved: list[str] = []
    unsourced: list[str] = []
    for panel in chosen.panels:
        star = built.star(panel.star) if panel.star else None
        values = {}
        for name in panel.measures:
            found = measure(name)
            if found is None or star is None:
                unresolved.append(f"{panel.title or panel.component}: {name}")
                continue
            values[name] = {
                "value": found.of(star.fact.rows),
                "unit": found.unit,
                # Carried to the panel because a template must not put a
                # non-additive measure in a total row, and the renderer cannot
                # know which is which without being told.
                "additive": found.additive,
                "doc": found.doc,
            }
        drawn, columns = _fill(panel, star)
        if star is None and panel.options.get("source"):
            unsourced.append(
                f"{panel.title or panel.component} "
                f"({panel.options['source']})"
            )
        panels.append({**panel.to_dict(), "values": values, "data": drawn,
                       "columns": columns,
                       # Always present, exactly as `contract.Block.to_dict`
                       # emits them: a renderer reading `panel.options.limit`
                       # should not have to know which panels declared any.
                       "options": dict(panel.options or {}),
                       "rows": len(star.fact.rows) if star else 0})

    gaps = [_gap("dashboard", detail) for detail in built.gaps]
    if selection is not None and not selection.confident:
        gaps.append(_gap(
            "dashboard",
            f"no template answers this well: {selection.reason}. What follows "
            f"is the closest one rather than the right one.",
        ))
    if unresolved:
        gaps.append(_gap(
            "dashboard",
            f"panel(s) asked for a measure this star does not carry: "
            f"{', '.join(unresolved)}",
        ))
    if unsourced:
        gaps.append(_gap(
            "dashboard",
            f"panel(s) read a source the kernel cannot reach and rendered "
            f"empty rather than absent: {', '.join(unsourced)}. The queue board "
            f"lives in `slpie_enterprise`, so an air-gapped console draws the "
            f"panel and says it has nothing rather than hiding it.",
        ))

    return flow.then(
        Kind.REPORT,
        {
            "template": chosen.to_dict(),
            "selection": selection.to_dict() if selection else
                         {"template": chosen.key, "reason": "named explicitly"},
            "panels": panels,
        },
        stage="dashboard",
        steps=[_step(
            f"filled {len(panels)} panel(s) of {chosen.key}"
            + (f" — {selection.reason}" if selection else " — named explicitly"),
            "select",
        )],
        gaps=gaps,
        facts={"template": chosen.key, "panels": len(panels)},
    )


def verbs() -> tuple[Verb, ...]:
    from ...present.template import CONTEXTS, DOMAINS, UTILITIES
    from ...present.templates import keys
    from ...warehouse.schema import names

    return (
        Verb(
            name="warehouse", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="the graph as facts and dimensions, with its measures",
            detail=(
                "Three stars — elements, relationships, findings — because "
                "every question about an estate is a group-by on one of those "
                "grains. Facts carry the confidence and the evidence kind they "
                "came from: a count of relationships means something different "
                "depending on whether they were read from a lockfile or "
                "inferred from a name, and this is the one place in the "
                "platform a number could have arrived without that."
            ),
            params=(
                Param("star", "str", "one star instead of all three",
                      choices=names()),
                Param("govern", "bool",
                      "run the rules too, so the findings star is not empty"),
                Param("now", "int", "the build timestamp, for a reproducible run"),
            ),
            examples=("discover . | warehouse",
                      "discover . | warehouse --govern",
                      "discover . | warehouse --star relationships"),
            run=_warehouse,
        ),
        Verb(
            name="warehouse-export", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="the warehouse as CSV, JSON or SQL, with its data dictionary",
            detail=(
                "The schema travels with the data: a JSON extract carries its "
                "documented columns, and the dictionary is generated from the "
                "same declaration the tables are. A hand-written column list is "
                "wrong within two releases and leaves every analyst asking the "
                "same person the same question."
            ),
            params=(
                Param("format", "str", "which form", default="csv",
                      choices=("csv", "json", "sql")),
                Param("out", "str", "a directory to write into"),
                Param("dialect", "str", "which SQL", default="sqlite",
                      choices=("sqlite", "postgres")),
                Param("govern", "bool", "run the rules too, for the findings star"),
                Param("now", "int", "the build timestamp"),
            ),
            examples=("discover . | warehouse-export",
                      "discover . | warehouse-export --format sql --out ./warehouse"),
            run=_export,
        ),
        Verb(
            name="warehouse-load", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT, mutates=True,
            summary="materialise the warehouse into a database a BI tool can reach",
            detail=(
                "Into the store the platform already has, so connecting a BI "
                "tool needs no new service, no new credential and no new "
                "dependency. Tables are dropped and rebuilt rather than "
                "migrated: the graph is the source of truth and these are a "
                "projection of it, which is the relationship projections have "
                "to the ledger everywhere else in this platform."
            ),
            params=(
                Param("database", "str", "the SQLite file to write", required=True),
                Param("govern", "bool", "run the rules too, for the findings star"),
                Param("now", "int", "the build timestamp"),
            ),
            examples=("discover . | warehouse-load --database ./warehouse.db",),
            run=_load,
        ),
        Verb(
            name="dashboard", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="a screen chosen for the demand, filled from the warehouse",
            detail=(
                "A template declares what it is for on three axes — utility, "
                "context, domain — and one is selected rather than chosen from "
                "a menu the reader would have to understand first. The "
                "selection explains itself: which axes matched, which did not, "
                "and whether the best match was good enough to present as the "
                "answer. Below a floor it says so instead of dressing a generic "
                "grid up as the right screen."
            ),
            params=(
                Param("template", "str", "name one instead of selecting",
                      choices=keys()),
                Param("about", "str", "the question, in words"),
                Param("utility", "str", "what you are doing", choices=UTILITIES),
                Param("for", "str", "where you are reading it", choices=CONTEXTS),
                Param("domain", "str", "what it is about", choices=DOMAINS),
                Param("govern", "bool", "run the rules too, for the findings star"),
                Param("now", "int", "the build timestamp"),
            ),
            examples=(
                "discover . | dashboard",
                "discover . | dashboard --about 'what CVEs affect payments'",
                "discover . | dashboard --template dependency-report",
            ),
            run=_dashboard,
        ),
    )
