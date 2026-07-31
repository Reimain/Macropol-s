"""The artifacts a release actually ships: SBOM, C4, architecture-as-code.

Four verbs, and the interesting decision is what each one *consumes*.

`sbom`, `c4` and `enterprise` all consume `OBSERVATIONS` rather than requiring an
environment, for the same reason `govern` does: the tree that most needs an SBOM
is often one nobody has declared yet, and an emitter that only worked after
`slpie init` would be the wrong half of the capability. They build a real
in-memory graph from the scan (`governance/view.py`) — the same `SqliteGraph` the
engine uses — so the emitters cannot tell a scan from a database.

`risk` consumes `FINDINGS`, because a risk register is an aggregation of findings
and nothing else. That makes `discover . | govern | risk` the natural composition
and means the register can never contain a risk no rule raised.

**Emission is text generation and therefore ring-0 safe.** Writing an SBOM, a
Mermaid diagram or a Python module needs no third-party package and no network.
The one exception is `enterprise --write`, which routes through the Gratimos
codegen bridge — still ring 0, still stdlib, and still the single import
`tests/test_slpie_boundaries.py` permits.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "artifacts"

#: Where `enterprise --write` puts generated views, relative to the root. Named
#: here so the verb, its help text and the manual cannot disagree.
ARCHITECTURE_DIR = "architecture"


def _graph_of(flow: Flow):
    """The observations flowing in, as a real graph. Caller closes it."""
    from ...governance.view import view_of

    return view_of(flow.items)


def _sbom(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """A bill of materials, in whichever standard the consumer speaks."""
    from ...artifacts.sbom import (
        SbomOptions,
        cyclonedx_document,
        spdx_document,
        write_sbom,
    )
    from ...errors import ArtifactError

    wanted = str(arguments.get("format") or "cyclonedx").lower()
    if wanted not in ("cyclonedx", "spdx"):
        raise VerbError(
            f"{wanted!r} is not an SBOM format; use cyclonedx or spdx"
        )

    # Supplied, never read from the clock. An SBOM whose bytes changed on every
    # run cannot be diffed, attested or checked into a release — so the default
    # is *no* timestamp rather than "now".
    stamp = int(arguments.get("timestamp") or 0)
    options = SbomOptions(
        timestamp=stamp,
        subject=str(arguments.get("subject") or ""),
        subject_version=str(arguments.get("subject_version") or ""),
    )

    with _graph_of(flow) as graph:
        try:
            document = (
                cyclonedx_document(graph, options=options) if wanted == "cyclonedx"
                else spdx_document(graph, options=options)
            )
        except ArtifactError as error:
            raise VerbError(str(error)) from error

    body = document.to_json()
    written = ""
    if arguments.get("out"):
        written = str(write_sbom(document, Path(str(arguments["out"])).expanduser()))

    return flow.then(
        Kind.REPORT, (document.to_dict(),), stage="sbom",
        steps=[ReasoningStep(
            claim=(
                f"emitted a {document.format} {document.spec_version} document: "
                f"{document.components} component(s), "
                f"{document.relationships} relationship(s)"
            ),
            layer="artifacts", operation="shape",
        )],
        facts={
            "sbom": body,
            "sbom_format": document.format,
            "sbom_components": document.components,
            "sbom_written": written,
        },
    )


def _c4(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """C4 views, as Mermaid — one level, or every level this graph supports."""
    from ...artifacts.c4 import C4Level, c4_views

    wanted = str(arguments.get("level") or "").lower()

    with _graph_of(flow) as graph:
        # C3 and C4 need a subject; `c4_views` produces them only when one is
        # named, because a component diagram with no container is a list rather
        # than a diagram. Without these parameters two of the four levels would
        # be unreachable from every surface — built, and dead.
        views = c4_views(
            graph,
            container=str(arguments.get("container") or ""),
            component=str(arguments.get("component") or ""),
        )

    if wanted:
        views = tuple(
            view for view in views
            if view.level.value.lower() == wanted or view.level.name.lower() == wanted
        )
        if not views:
            raise VerbError(
                f"{wanted!r} is not a level this graph supports; use one of "
                + ", ".join(level.value for level in C4Level)
            )

    rendered = "\n\n".join(
        f"%% {view.name}: {view.doc}\n{view.to_mermaid()}" for view in views
    )
    written: list[str] = []
    if arguments.get("out"):
        target = Path(str(arguments["out"])).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        for view in views:
            path = target / f"{view.level.value}.mmd"
            path.write_text(view.to_mermaid(), encoding="utf-8")
            written.append(str(path))

    return flow.then(
        Kind.REPORT, tuple(view.to_dict() for view in views), stage="c4",
        steps=[ReasoningStep(
            claim=(
                f"{len(views)} C4 view(s): "
                + ", ".join(f"{v.level.value} ({len(v.elements)})" for v in views)
            ),
            layer="artifacts", operation="shape",
        )],
        facts={
            "c4": rendered,
            "c4_levels": [view.level.value for view in views],
            "c4_written": written,
        },
    )


def _enterprise(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """TOGAF views and the topology — rendered, or generated as importable code."""
    from ...artifacts.codegen import (
        ArchitectureCodegen,
        ArchitectureConflict,
        MergePolicy,
    )
    from ...enterprise import togaf_views, topology_view
    from ...errors import ArtifactError

    wanted = str(arguments.get("view") or "").lower()

    with _graph_of(flow) as graph:
        views = (*togaf_views(graph), topology_view(graph))

    if wanted:
        views = tuple(view for view in views if view.name == wanted)
        if not views:
            raise VerbError(
                f"{wanted!r} is not a view; use application, data, technology, "
                f"standards or topology"
            )

    lines: list[str] = [""]
    for view in views:
        lines.append(f"  {view.name:<12} {len(view.elements):>4} element(s), "
                     f"{len(view.relations):>4} relation(s)")
        lines.append(f"               {view.doc}")
        if view.empty:
            # Reported, not hidden. An empty data architecture usually means
            # nothing has scanned the warehouse — a fact about coverage, and
            # dropping the view would turn it into an absence nobody notices.
            lines.append("               (nothing selected — was this scanned?)")
        lines.append("")

    emitted: list[dict[str, Any]] = []
    if arguments.get("write"):
        root = Path(str(
            arguments.get("out")
            or Path(str(flow.facts.get("scanned_root") or context.root or "."))
            / ARCHITECTURE_DIR
        )).expanduser()
        policy = MergePolicy(str(arguments.get("policy") or MergePolicy.RAISE.value))
        codegen = ArchitectureCodegen(root, policy=policy)
        for view in views:
            if view.empty:
                continue      # `shape_for` refuses an empty view, and rightly
            try:
                emitted.append(codegen.emit(view).to_dict())
            except ArchitectureConflict as error:
                raise VerbError(
                    f"{error}\n\nNothing was written for this view. The other "
                    f"views emitted before it were."
                ) from error
            except ArtifactError as error:
                raise VerbError(str(error)) from error
        lines.append(f"  generated into {root}")
        for item in emitted:
            kept = f", kept {', '.join(item['kept'])}" if item["kept"] else ""
            lines.append(
                f"    {item['name']:<12} r{item['revision']} "
                f"{'written' if item['rewritten'] else 'unchanged'}{kept}"
            )
        lines.append("")

    return flow.then(
        Kind.REPORT, tuple(view.to_dict() for view in views), stage="enterprise",
        steps=[ReasoningStep(
            claim=(
                f"{len(views)} enterprise view(s), "
                f"{sum(len(view.elements) for view in views)} element(s) total"
                + (f"; {len(emitted)} generated as code" if emitted else "")
            ),
            layer="artifacts", operation="shape",
        )],
        facts={
            "enterprise": "\n".join(lines),
            "views": [view.name for view in views],
            "generated": emitted,
        },
    )


def _risk(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Findings aggregated onto subjects, ranked by severity times reach."""
    from ...enterprise.risk import heat_map, register, report

    risks = register(flow.items)
    limit = max(1, int(arguments.get("limit") or 20))

    if arguments.get("markdown"):
        rendered = report(risks, limit=limit)
    else:
        lines = ["", f"  {len(risks)} subject(s) carrying findings", ""]
        for risk in risks[:limit]:
            lines.append(
                f"  {risk.rank:>6}  {risk.severity.value:<9} {risk.label}"
            )
            lines.append(
                f"          {len(risk.findings)} finding(s), "
                f"{risk.blocking} blocking, {risk.dependents} dependent(s)"
            )
        lines += ["", heat_map(risks), ""]
        rendered = "\n".join(lines)

    if arguments.get("out"):
        target = Path(str(arguments["out"])).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report(risks, limit=limit), encoding="utf-8")

    return flow.then(
        Kind.REPORT, tuple(risk.to_dict() for risk in risks), stage="risk",
        steps=[ReasoningStep(
            claim=(
                f"{len(risks)} subject(s) carry findings; the worst is "
                + (f"{risks[0].label} at {risks[0].severity.value}" if risks
                   else "nothing")
            ),
            layer="artifacts", operation="shape",
        )],
        facts={
            "risk": rendered,
            "risk_subjects": len(risks),
            "risk_blocking": sum(risk.blocking for risk in risks),
        },
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="sbom", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="a bill of materials, in CycloneDX or SPDX",
            detail=(
                "Identity passes through rather than being translated: packages "
                "are already purls, which is what CycloneDX, SPDX and OSV all "
                "speak. Nothing reads the clock — the timestamp is an argument, "
                "so identical graphs produce byte-identical documents that can "
                "be diffed and attested."
            ),
            params=(
                Param("format", "str", "which standard", default="cyclonedx",
                      choices=("cyclonedx", "spdx")),
                Param("out", "path", "write the document here"),
                Param("subject", "str", "what this SBOM is about"),
                Param("subject_version", "str", "its version"),
                Param("timestamp", "int", "document time, epoch seconds"),
            ),
            examples=(
                "discover . | sbom",
                "discover . | sbom --format spdx",
            ),
            run=_sbom,
        ),
        Verb(
            name="c4", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="C4 views of the system, as Mermaid",
            detail=(
                "Four levels, each answering a different question: context, "
                "container, component and code. Only the levels this graph can "
                "actually support are built."
            ),
            params=(
                Param("level", "str", "one level, instead of every one",
                      choices=("context", "container", "component", "code")),
                Param("container", "str", "a container node id, to build C3"),
                Param("component", "str", "a component node id, to build C4"),
                Param("out", "path", "write .mmd files into this directory"),
            ),
            examples=(
                "discover . | c4",
                "discover . | c4 --level context",
            ),
            run=_c4,
        ),
        Verb(
            name="enterprise", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.REPORT,
            summary="TOGAF views and the deployment topology",
            detail=(
                "With --write, each view is generated into ./architecture/ as "
                "importable Python through an AST three-way merge. An "
                "architect's annotation marked `# gratimos:keep` survives "
                "regeneration; a genuine conflict raises rather than silently "
                "choosing a winner, because either outcome of choosing loses "
                "somebody's work with no record."
            ),
            params=(
                Param("view", "str", "one view, instead of every one",
                      choices=("application", "data", "technology", "standards",
                               "topology")),
                Param("write", "bool", "generate into ./architecture/",
                      default=False),
                Param("out", "path", "generate here instead"),
                Param("policy", "str", "what to do on a merge conflict",
                      default="raise",
                      choices=("raise", "mark", "local", "generated")),
            ),
            examples=(
                "discover . | enterprise",
                "discover . | enterprise --view application",
            ),
            run=_enterprise,
        ),
        Verb(
            name="risk", group=GROUP, consumes=Kind.FINDINGS,
            produces=Kind.REPORT,
            summary="findings aggregated into a ranked risk register",
            detail=(
                "A findings list is a work queue; a register answers which parts "
                "of the estate are dangerous. Ranked by severity times reach, "
                "because a critical in a leaf nobody imports is a smaller "
                "problem than a high one in the package forty modules touch."
            ),
            params=(
                Param("limit", "int", "how many to show", default=20),
                Param("markdown", "bool", "render as markdown", default=False),
                Param("out", "path", "write the markdown report here"),
            ),
            examples=(
                "discover . | govern | risk",
                "discover . | govern | risk --markdown",
            ),
            run=_risk,
        ),
    )
