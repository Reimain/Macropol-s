"""The verbs that do the actual work, and need no environment to do it.

`discover` walks a directory and runs the 29 discoverers over it. `reason` runs
L1–L4. `link` resolves identities. `constraints` solves. None of them touches the
ledger, the graph or a manifest, which has three consequences worth having:

* the CLI is useful the moment it is installed — `slpie 'discover . | reason |
  explain'` answers something real with no `slpie init` first;
* they are testable without a database, so the suite stays fast;
* they run identically under the CLI, an HTTP request and a Celery worker, which
  is what invariant 7 asks of everything above `binding/`.

The engine-backed verbs live next door in `environment.py`. The split is not
cosmetic: it marks exactly which capabilities need state and which do not, and a
verb that quietly acquired a database dependency would move file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ...domain.finding import Gap, GapKind
from ...domain.reasoning import ReasoningStep
from ...discovery.registry import Registry, register_builtins
from ...linking.linkers import Linked, LinkerSet
from ...linking.resolver import Resolver
from ...reasoning.constraints import BacktrackingSolver, StaticIndex, requirements_from
from ...reasoning.pipeline import Pipeline
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "analysis"

#: Directories never worth walking. Mirrors `slpie/discovery/registry.py:SKIP`
#: rather than importing it, because that constant is about connector listings
#: and this one is about a local filesystem walk; they agree today and are free
#: to diverge without breaking each other.
SKIP = (
    "/.git/", "/node_modules/", "/.venv/", "/venv/", "/__pycache__/",
    "/dist/", "/build/", "/.tox/", "/target/", "/vendor/", "/.mypy_cache/",
    "/.pytest_cache/", "/site-packages/",
)

#: A file larger than this is not a manifest. Reading it would cost more than the
#: information it could hold.
MAX_BYTES = 4 * 1024 * 1024


def _discover(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Walk a path and run every discoverer that claims a file in it."""
    root = Path(str(arguments.get("path") or context.root or ".")).expanduser()
    if not root.exists():
        raise VerbError(f"{root} does not exist")

    limit = max(1, int(arguments.get("limit") or 5000))
    registry = register_builtins(Registry())

    observations: list[Any] = []
    errors: list[str] = []
    seen = read = 0

    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in candidates:
        if seen >= limit:
            break
        if not path.is_file():
            continue
        uri = path.resolve().as_uri()
        if any(part in uri for part in SKIP):
            continue
        seen += 1
        if not registry.for_uri(uri):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                errors.append(f"{path.name}: too large to analyse")
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            errors.append(f"{path.name}: {error}")
            continue

        read += 1
        outcome = registry.discover(uri, content=content, element=str(root))
        observations.extend(outcome.observations)
        errors.extend(outcome.errors)

    gaps = tuple(
        Gap(
            kind=GapKind.PARSE_FAILURE, subject=str(root), detail=message,
            remediation="the file was skipped; the rest of the tree was read",
            confidence_impact=0.05,
        )
        for message in errors[:20]
    )

    return flow.then(
        Kind.OBSERVATIONS, tuple(observations), stage="discover",
        steps=[ReasoningStep(
            claim=(
                f"read {read} of {seen} files under {root} and recorded "
                f"{len(observations)} observations"
            ),
            layer="discovery", operation="discover",
            evidence=tuple(
                item.evidence for item in observations[:6]
                if item.evidence is not None
            ),
        )],
        gaps=gaps,
        facts={
            "files_seen": seen, "files_read": read, "discover_errors": len(errors),
            # Recorded so a later stage governs the tree that was *read* rather
            # than the working directory. `discover /a/b | govern` was scanning
            # the cwd for secrets and reporting findings about a different
            # codebase entirely — which looked like a real result.
            "scanned_root": str(root.resolve()),
        },
    )


def _reason(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Run L1–L4 and carry the pipeline's own gaps into the flow."""
    element = str(arguments.get("element") or "")
    pipeline = Pipeline()
    result = pipeline.run(flow.items, element=element)

    return flow.then(
        Kind.ENRICHMENTS, result, stage="reason",
        steps=result.steps,
        gaps=result.gaps,
        facts={
            "layers": len(result.results),
            "enrichments": len(result.context.enrichments),
            "abstained": [item.layer for item in result.abstained],
            **{k: v for k, v in result.context.facts.items()
               if isinstance(v, (int, str, bool))},
        },
    )


def _link(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Merge observations onto identities, then run the cross-file linkers."""
    resolution = Resolver().resolve(flow.items)
    linkers = LinkerSet()
    joined = linkers.run(resolution) if not arguments.get("resolve_only") else ()

    gaps = tuple(
        Gap(
            kind=GapKind.UNRESOLVED_DEPENDENCY, subject=observation.subject,
            detail=reason,
            remediation="a discoverer must emit a purl or an SLPIE urn",
            confidence_impact=0.1,
        )
        for observation, reason in resolution.unresolved[:20]
    ) + tuple(
        Gap(
            kind=GapKind.NOT_IMPLEMENTED, subject="linking", detail=message,
            remediation="the remaining linkers ran; this join was not made",
            confidence_impact=0.1,
        )
        for message in linkers.errors
    )

    steps = [ReasoningStep(
        claim=(
            f"{len(resolution.resolved)} identities from "
            f"{flow.size} observations, {len(resolution.links)} edges, "
            f"{len(joined)} cross-file link(s)"
        ),
        layer="linking", operation="resolve",
        evidence=tuple(
            item for entry in resolution.resolved[:4] for item in entry.evidence[:2]
        ),
    )]
    for entry in resolution.contradictions()[:8]:
        steps.append(ReasoningStep(
            claim=(
                f"{entry.identity} is pinned to "
                f"{' and '.join(entry.versions)} by different sources"
            ),
            layer="linking", operation="compare", evidence=entry.evidence[:2],
        ))

    # A contradiction step asserts something about the world, so it cites the
    # evidence of the node it is about. A join carries node ids rather than
    # evidence, so it is looked up — leaving the step uncited would make every
    # pipeline that found a contradiction report itself as ungrounded.
    by_node = {entry.node_id: entry for entry in resolution.resolved}
    for entry in linkers.contradictions(joined)[:8]:
        about = by_node.get(entry.target) or by_node.get(entry.source)
        steps.append(ReasoningStep(
            claim=entry.contradiction, layer="linking", operation="compare",
            inputs=(entry.enrichment.id,),
            evidence=about.evidence[:2] if about is not None else (),
        ))

    linked = Linked(
        resolution=resolution, joins=tuple(joined),
        abstentions=tuple(linkers.errors),
    )
    return flow.then(
        Kind.RESOLUTION, linked, stage="link",
        steps=steps, gaps=gaps,
        facts={
            "identities": len(resolution.resolved),
            "edges": len(resolution.links),
            "merged": resolution.merged,
            "contradictions": len(resolution.contradictions()),
            "cross_file_links": len(joined),
            "contradicting_joins": len(linked.contradicting_joins()),
        },
    )


def _constraints(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Solve what the resolution declared, and name any conflict precisely."""
    resolution = flow.value
    ecosystem = str(arguments.get("ecosystem") or "generic")
    requirements = requirements_from(resolution, ecosystem=ecosystem)

    # The index is what was *observed*, never a network lookup. A coordinate we
    # have never seen yields no candidates, which becomes a named conflict rather
    # than an assumption that some satisfying version exists somewhere.
    index = StaticIndex(ecosystem=ecosystem)
    for entry in getattr(resolution, "resolved", ()):
        for version in entry.versions:
            if version:
                index.add(entry.coordinate, version)
        if entry.pinned:
            index.add(entry.coordinate, entry.pinned)

    solver = BacktrackingSolver(
        max_steps=max(1, int(arguments.get("max_steps") or 10_000)),
    )
    solution = solver.solve(requirements, index)

    gaps = tuple(
        Gap(
            kind=GapKind.PARSE_FAILURE, subject=requirement.coordinate,
            detail=f"{requirement.range!r} is not a readable version range",
            remediation="record a range the ecosystem's own resolver would accept",
            confidence_impact=0.1,
        )
        for requirement in solution.unreadable[:20]
    )
    if solution.exhausted:
        gaps += (Gap(
            kind=GapKind.NOT_IMPLEMENTED, subject="constraints",
            detail=(
                f"the search stopped after {solution.attempts} steps, so any "
                f"conflict list is partial"
            ),
            remediation="raise --max-steps, or pin the packages already known to conflict",
            confidence_impact=0.2,
        ),)

    return flow.then(
        Kind.SOLUTION, solution, stage="constraints",
        steps=solver.steps(solution), gaps=gaps,
        facts={
            "satisfiable": solution.satisfiable,
            "requirements": len(requirements),
            "conflicts": len(solution.conflicts),
            "attempts": solution.attempts,
        },
    )


def _findings(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Turn whatever is flowing into findings, ranked.

    Polymorphic on purpose: contradictions from `link`, conflicts from
    `constraints` and gaps from anywhere upstream are all things an operator wants
    ranked in one list. Requiring a different verb per source would make the
    obvious composition impossible.
    """
    from ...domain.finding import Finding, FindingKind, Remediation, Severity

    wanted = str(arguments.get("severity") or "").lower()
    found: list[Finding] = []

    resolution = flow.value
    for entry in getattr(resolution, "contradictions", lambda: ())():
        found.append(Finding(
            kind=FindingKind.CONTRADICTED, severity=Severity.HIGH,
            subject=entry.identity,
            title=f"{entry.identity} is pinned to two different versions",
            detail=(
                f"pinned to {' and '.join(entry.versions)} by different sources; "
                f"somebody's build is not what they think it is"
            ),
            evidence=entry.evidence[:4],
            remediation=Remediation(
                summary="reconcile the lockfiles, or declare which is authoritative",
                action="pin", target=entry.pinned,
            ),
        ))

    # `Finding` refuses to exist without evidence, which is invariant 1 reaching
    # all the way out to the findings list. A join names node ids, so the evidence
    # is looked up from the resolution rather than invented.
    by_node = {
        entry.node_id: entry for entry in getattr(resolution, "resolved", ())
    }
    for join in getattr(resolution, "contradicting_joins", lambda: ())():
        subject = join.target or join.source
        entry = by_node.get(subject) or by_node.get(join.source)
        evidence = entry.evidence[:4] if entry is not None else ()
        if not evidence:
            # No evidence means no finding. Raising one anyway would put an
            # unfounded item at the top of somebody's triage list.
            continue
        found.append(Finding(
            kind=FindingKind.CONTRADICTED, severity=Severity.HIGH,
            subject=entry.identity if entry is not None else subject,
            title="a lockfile pin falls outside its declared range",
            detail=join.contradiction,
            evidence=evidence,
            remediation=Remediation(
                summary="regenerate the lockfile, or widen the declared range",
                action="pin",
            ),
        ))

    # A conflict names evidence *ids* (the requirements carry them); the Evidence
    # objects themselves are in the reasoning path this flow accumulated. Matching
    # by id is what lets the finding cite the manifest line that caused the
    # conflict, rather than being refused for having nothing to cite.
    upstream = {item.id: item for item in flow.reasoning.evidence}
    for conflict in getattr(flow.value, "conflicts", ()):
        cited = tuple(
            upstream[reference]
            for side in (conflict.left, conflict.right)
            if side is not None
            for reference in side.derived_from
            if reference in upstream
        )[:4] or tuple(flow.reasoning.evidence)[:2]
        if not cited:
            continue
        found.append(Finding(
            kind=FindingKind.CONSTRAINT_CONFLICT, severity=Severity.HIGH,
            subject=conflict.coordinate,
            title=f"{conflict.name} cannot satisfy every requirement on it",
            detail=conflict.explain(),
            evidence=cited,
            related=tuple(name for name in conflict.pair if name),
            remediation=Remediation(
                summary="pin one side, upgrade the other, or drop a dependency",
                action="pin", breaking=True,
            ),
        ))

    # Gaps become findings because "we could not see this" is actionable in the
    # same list as "this is wrong" — an operator triaging a release wants one
    # ranked list, not two views they have to reconcile themselves.
    for gap in flow.gaps:
        found.append(Finding(
            kind=FindingKind.POLICY_VIOLATION if gap.kind.resolvable_by_user
            else FindingKind.UNDOCUMENTED_INTERFACE,
            severity=Severity.MEDIUM,
            subject=gap.subject,
            title=f"incomplete: {gap.kind.value}",
            detail=gap.detail,
            remediation=Remediation(summary=gap.remediation) if gap.remediation else None,
        ))

    if wanted:
        found = [item for item in found if item.severity.name.lower() == wanted]
    found.sort(key=lambda item: (-item.severity.rank, item.subject))

    return flow.then(
        Kind.FINDINGS, tuple(found), stage="findings",
        steps=[ReasoningStep(
            claim=f"raised {len(found)} finding(s)"
                  + (f" at severity {wanted}" if wanted else ""),
            layer="governance", operation="evaluate",
            # A finding asserts something about the world, so its step cites the
            # evidence the finding was built from. Leaving it uncited would make
            # every pipeline ending in `findings` report itself as ungrounded,
            # which is both alarming and wrong.
            evidence=tuple(
                item for finding in found[:6] for item in finding.evidence[:2]
            ),
        )],
        facts={"findings": len(found)},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="discover", group=GROUP, produces=Kind.OBSERVATIONS,
            summary="read a tree and record what every discoverer finds",
            detail=(
                "Needs no environment and no database, so it works the moment "
                "the package is installed."
            ),
            params=(
                Param("path", "path", "the directory or file to read", default="."),
                Param("limit", "int", "maximum files to consider", default=5000),
            ),
            examples=(
                "discover .",
                "discover ./services/payments | reason | explain",
            ),
            run=_discover,
        ),
        Verb(
            name="reason", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.ENRICHMENTS,
            summary="run the reasoning layers over what was observed",
            params=(Param("element", "str", "the element these belong to"),),
            examples=("discover . | reason", "discover . | reason | explain"),
            run=_reason,
        ),
        Verb(
            name="link", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.RESOLUTION,
            summary="merge observations onto identities and join across files",
            detail="Merges on identity, never on similarity.",
            params=(Param("resolve_only", "bool", "skip the cross-file linkers",
                          default=False),),
            examples=("discover . | link", "discover . | link | findings"),
            run=_link,
        ),
        Verb(
            name="constraints", group=GROUP, consumes=Kind.RESOLUTION,
            produces=Kind.SOLUTION,
            summary="solve the version constraints, naming any conflict",
            params=(
                Param("ecosystem", "str", "the version dialect", default="generic"),
                Param("max_steps", "int", "search budget", default=10000),
            ),
            examples=("discover . | link | constraints",),
            run=_constraints,
        ),
        Verb(
            name="findings", group=GROUP, consumes=Kind.ANY, produces=Kind.FINDINGS,
            summary="rank everything wrong that is known so far",
            params=(Param("severity", "str", "keep only this severity",
                          choices=("critical", "high", "medium", "low", "info")),),
            examples=(
                "discover . | link | findings",
                "discover . | link | constraints | findings --severity high",
            ),
            run=_findings,
        ),
    )
