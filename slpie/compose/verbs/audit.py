"""The judge, as verbs — so a verdict composes with everything else.

`audit` produces JUDGEMENTS, which means an architecture finding pipes into
`findings`, `filter` and `explain` like any other conclusion. That is the point of
putting the judge on the graph rather than in a separate report: `audit | findings
--severity high` is one triage list, not two views somebody has to reconcile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...audit import Auditor, Check, Verdict, project, slpie_checks
from ...domain.finding import Gap, GapKind
from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError


def _checks_for(root: Path, named: str) -> tuple[Check, ...]:
    """Which checks apply to this tree.

    A tree we have not been told about gets the generic checks plus whatever its
    own `pyproject.toml` declares. Asserting somebody else's layer boundaries would
    be inventing their architecture and then failing them against it.
    """
    from ...audit import generic_checks

    if named == "self" or (root / "slpie").is_dir():
        checks = list(slpie_checks())
    else:
        checks = list(generic_checks())

    declared, optional, ring = _declared(root)
    if declared:
        checks.append(Check("declared-dependencies", {
            "ring": ring, "declared": declared, "exempt": optional,
        }))
    return tuple(checks)


def _declared(root: Path) -> tuple[list[str], list[str], str]:
    """A tree's own declared dependencies, from its packaging metadata."""
    import re
    import tomllib

    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return [], [], ""
    try:
        body = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return [], [], ""

    project_body = body.get("project", {})

    def names(specs: Any) -> list[str]:
        return [
            re.split(r"[<>=!\[; ]", str(item).strip())[0]
            for item in (specs or []) if str(item).strip()
        ]

    optional = [
        name
        for group in (project_body.get("optional-dependencies", {}) or {}).values()
        for name in names(group)
    ]
    ring = str(project_body.get("name", "") or "").replace("-", "_")
    return names(project_body.get("dependencies")), optional, ring


def _audit(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    root = Path(str(arguments.get("path") or context.root or ".")).expanduser()
    if not root.exists():
        raise VerbError(f"{root} does not exist")

    named = str(arguments.get("checks") or "auto")
    wanted = str(arguments.get("rule") or "")

    checks = _checks_for(root, named)
    if wanted:
        checks = tuple(
            item for item in checks
            if item.rule == wanted or item.options.get("rule") == wanted
        )
        if not checks:
            raise VerbError(f"no check named {wanted!r} applies to this tree")

    result = Auditor(checks).run(root)

    gaps = tuple(
        Gap(
            kind=GapKind.NOT_IMPLEMENTED, subject=item.subject,
            detail=item.detail,
            remediation=item.remediation
            or "the judge declined to rule here rather than guessing",
            confidence_impact=0.1,
        )
        for item in result.indeterminate[:20]
    )

    return flow.then(
        Kind.JUDGEMENTS, result.judgements, stage="audit",
        steps=[ReasoningStep(
            claim=(
                f"{len(result)} judgement(s) over {result.scanned} module(s): "
                f"{len(result.violations)} violated, "
                f"{len(result.indeterminate)} undecided, "
                f"coverage {result.coverage:.0%}"
            ),
            layer="audit", operation="judge",
            evidence=tuple(
                item for judgement in result.judgements[:6]
                for item in judgement.evidence[:1]
            ),
        )],
        gaps=gaps,
        facts={
            "digest": result.digest,
            "clean": result.clean,
            "coverage": result.coverage,
            "violations": len(result.violations),
            "indeterminate": len(result.indeterminate),
            "audit": result.render(),
        },
    )


def _verdicts(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """Filter judgements to one verdict. `audit | verdicts --only violated`."""
    wanted = str(arguments.get("only") or "violated").lower()
    try:
        verdict = Verdict(wanted)
    except ValueError:
        raise VerbError(
            f"{wanted!r} is not a verdict; use "
            f"{', '.join(item.value for item in Verdict)}"
        ) from None

    kept = tuple(item for item in flow.items if item.verdict is verdict)
    return flow.then(
        Kind.SAME, kept, stage="verdicts",
        steps=[ReasoningStep(
            claim=f"{len(kept)} judgement(s) with verdict {verdict.value}",
            layer="audit", operation="shape",
        )],
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="audit", group="audit", produces=Kind.JUDGEMENTS,
            summary="judge a tree against its stated architecture",
            detail=(
                "Deterministic: an AST projected into a graph, then queried. Same "
                "tree, same digest, on any machine — which is what lets CI pin "
                "'the architecture has not drifted' to one comparable value. A "
                "rule that cannot decide says INDETERMINATE rather than passing."
            ),
            params=(
                Param("path", "path", "the tree to judge", default="."),
                Param("checks", "str", "auto or self", default="auto",
                      choices=("auto", "self")),
                Param("rule", "str", "run only this rule"),
            ),
            examples=("audit", "audit . | verdicts --only violated"),
            run=_audit,
        ),
        Verb(
            name="verdicts", group="audit", consumes=Kind.JUDGEMENTS,
            produces=Kind.SAME,
            summary="keep only judgements with one verdict",
            params=(Param("only", "str", "which verdict", default="violated",
                          choices=("upheld", "violated", "indeterminate",
                                   "inapplicable")),),
            examples=("audit | verdicts --only indeterminate",),
            run=_verdicts,
        ),
    )
