"""Governance as a verb, so rules compose like everything else.

`govern` consumes `OBSERVATIONS` and produces `FINDINGS`. Both halves of that
signature are decisions:

**It consumes observations, not a graph.** Rules read a `GraphView`, and the
obvious wiring would have been to require an environment — which would mean the
licence check only runs once somebody has a ledger and a SQLite file. The tree
that most needs a licence check is the one nobody has described yet. So the verb
builds a real in-memory graph from the scan (`governance/view.py`) and the rules
cannot tell the difference, because it is the same `SqliteGraph` the engine uses.

**It produces findings**, which already exist as a kind, so `govern` slots into
every composition that already ends in one: `discover . | govern --severity high
| explain` renders the file and line behind each. A `GOVERNANCE` kind of its own
would have been a new vocabulary nothing else spoke.

The parameters are all *data the rules need in order not to decline*. A rule that
cannot see a distribution context or a popularity list refuses to guess, and the
verb reports how many declined — so a clean result never quietly means "nothing
was checked".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "governance"

#: How many source files to hand the secret detectors. Every file read is a file
#: held in memory, and a secret scan over a whole monorepo is a different verb.
MAX_SOURCES = 400

#: Files worth reading for credentials. Binary and vendored trees are skipped —
#: a match inside a minified bundle is not actionable by the person reading it.
SOURCE_SUFFIXES = (
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".java", ".cs", ".php",
    ".sh", ".bash", ".zsh", ".env", ".yaml", ".yml", ".json", ".toml", ".ini",
    ".cfg", ".conf", ".properties", ".tf", ".tfvars", ".pem", ".txt", ".md",
)

SKIP = (
    "/.git/", "/node_modules/", "/.venv/", "/venv/", "/__pycache__/", "/dist/",
    "/build/", "/target/", "/vendor/", "/.mypy_cache/", "/.pytest_cache/",
    "/site-packages/", ".min.js", ".lock",
)


def _sources(root: Path, *, limit: int = MAX_SOURCES) -> dict[str, str]:
    """The files the secret detectors read, keyed by uri.

    Bounded twice — by count and by suffix — because an unbounded read is how a
    governance run becomes something people stop running.
    """
    found: dict[str, str] = {}
    if not root.exists():
        return found
    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in candidates:
        if len(found) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        uri = path.resolve().as_uri()
        if any(part in uri for part in SKIP):
            continue
        try:
            if path.stat().st_size > 512 * 1024:
                continue
            found[uri] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return found


def _advisories(argument: Any) -> Any:
    """An OSV feed from a file or a directory, or an empty database.

    Nothing here fetches. An advisory database is data the caller supplies, which
    is what makes a governance run reproducible and identical in the simulator —
    and it is why a missing feed leaves the rule declining rather than silently
    reporting no vulnerabilities.
    """
    from ...governance.security.advisories import AdvisoryDatabase

    database = AdvisoryDatabase()
    if not argument:
        return database

    location = Path(str(argument)).expanduser()
    if not location.exists():
        raise VerbError(
            f"{location} does not exist; --advisories takes an OSV document or a "
            f"directory of them, and this verb never fetches one for you"
        )

    documents = (
        [location] if location.is_file()
        else sorted(location.rglob("*.json"))
    )
    for path in documents:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VerbError(f"{path.name}: {error}") from error
        for entry in (body if isinstance(body, list) else [body]):
            database.add(entry)
    return database


def _facts(arguments: Mapping[str, Any], context: Context) -> dict[str, Any]:
    """The data the rules need in order not to decline."""
    facts: dict[str, Any] = {}

    if arguments.get("distribution"):
        facts["distribution"] = str(arguments["distribution"])
    if arguments.get("linkage"):
        facts["linkage"] = str(arguments["linkage"])
    if arguments.get("project_license"):
        facts["project_license"] = str(arguments["project_license"])

    if arguments.get("popular"):
        path = Path(str(arguments["popular"])).expanduser()
        if not path.exists():
            raise VerbError(f"{path} does not exist")
        try:
            facts["popular_packages"] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VerbError(f"{path.name}: {error}") from error

    return facts


def _record(findings: tuple[Any, ...], context: Context) -> int:
    """Put what was raised on the ledger, when there is a ledger to put it on.

    Without this the Findings screen reads a projection nothing fills. The
    command, the event kind and the projection have all existed since phase 2
    and `RaiseFinding` was dispatched by exactly one unit test — so the machinery
    was proven and unused, and the surface it feeds showed an empty list on an
    estate with two hundred open findings. That reads as "nothing is wrong",
    which is the most expensive thing an empty state can say.

    Silent when no engine is attached: `discover . | govern` from a directory
    with no environment is a legitimate way to use this verb, and it has nowhere
    to record. The count travels in `facts` either way, so a caller can tell the
    difference between "recorded nothing" and "had nowhere to record".
    """
    commands = getattr(context.engine, "commands", None)
    if commands is None or not findings:
        return 0

    from ...core.commands import RaiseFinding

    for finding in findings:
        commands.dispatch(RaiseFinding(finding=finding, actor=context.actor))
    return len(findings)


def _govern(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Run every built-in rule over what was scanned."""
    from ...domain.finding import Gap, GapKind
    from ...governance.builtins import builtins
    from ...governance.rules import RuleContext
    from ...governance.view import view_of

    families = tuple(
        item for item in str(arguments.get("family") or "").split(",") if item
    )
    ruleset = builtins(advisories=_advisories(arguments.get("advisories")), only=families)

    # The tree that produced these observations, not the working directory. An
    # upstream `discover /a/b` records where it read, and preferring that is what
    # stops `discover /a/b | govern` from scanning the cwd for secrets and
    # reporting them as though they belonged to /a/b.
    root = Path(str(
        arguments.get("path")
        or flow.facts.get("scanned_root")
        or context.root
        or "."
    )).expanduser()
    sources = {} if arguments.get("no_sources") else _sources(root)

    # The manifest is what declares security boundaries, and it only exists once
    # somebody has an environment. Without one the boundary rules decline — which
    # is reported as a gap, so "no boundary violations" is never confused with
    # "no boundaries were declared to violate".
    manifest = getattr(context.engine, "manifest", None)

    with view_of(flow.items) as graph:
        evaluation = ruleset.evaluate(RuleContext(
            graph=graph,
            manifest=manifest,
            sources=sources,
            facts=_facts(arguments, context),
            now=int(time.time()),
            source_uri=str(root),
        ))

    wanted = str(arguments.get("severity") or "").lower()
    findings = tuple(
        item for item in evaluation.findings
        if not wanted or item.severity.value == wanted
    )

    # A rule that abstained is a hole in the report, and a rule that declined is
    # a check nobody ran. Both travel as gaps rather than as a footnote, because
    # a finding list is read as "everything that is wrong" and neither of those
    # states supports that reading.
    gaps = tuple(
        Gap(
            kind=GapKind.NOT_IMPLEMENTED,
            subject=error.rule_id,
            detail=f"{error.rule_id} abstained: {error.error_type}: {error.message}",
            remediation="the other rules ran; this check produced nothing",
            confidence_impact=0.15,
        )
        for error in evaluation.errors
    )
    if evaluation.declined:
        gaps += (Gap(
            kind=GapKind.CAPABILITY_REFUSED,
            subject="governance",
            detail=(
                f"{evaluation.declined} rule(s) declined for want of data they "
                f"refuse to guess at — an advisory feed, a popularity list, or a "
                f"distribution context"
            ),
            remediation=(
                "supply --advisories, --popular or --distribution; a clean result "
                "from a check that did not run is the wrong kind of clean"
            ),
            confidence_impact=0.2,
        ),)

    recorded = _record(findings, context)

    return flow.then(
        Kind.FINDINGS, findings, stage="govern",
        steps=[ReasoningStep(
            claim=(
                f"{evaluation.evaluated} rule(s) ran over {flow.size} observation(s): "
                f"{len(findings)} finding(s)"
                + (f" at severity {wanted}" if wanted else "")
                + (f", {evaluation.declined} declined" if evaluation.declined else "")
                + (f", {evaluation.error_count} abstained"
                   if evaluation.error_count else "")
            ),
            layer="governance", operation="evaluate",
            # A finding asserts something about the world, so the step cites what
            # the findings were built from. Leaving it uncited would report every
            # governed pipeline as ungrounded.
            evidence=tuple(
                item for finding in findings[:6] for item in finding.evidence[:2]
            ),
        )],
        gaps=gaps,
        facts={
            "findings": len(findings),
            "rules_evaluated": evaluation.evaluated,
            "rules_declined": evaluation.declined,
            "rules_abstained": evaluation.error_count,
            "ruleset_digest": ruleset.digest,
            "by_severity": evaluation.by_severity(),
            "sources_read": len(sources),
            "recorded": recorded,
        },
    )


def _rules(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """What this build will check, and the fingerprint of each rule."""
    from ...governance.builtins import builtins

    ruleset = builtins()
    wanted = str(arguments.get("tag") or "").lower()
    chosen = [
        rule for rule in ruleset
        if not wanted or wanted in (tag.lower() for tag in rule.tags)
    ]

    lines = [
        "", f"  {len(chosen)} rule(s), set digest {ruleset.digest[:16]}", "",
    ]
    for rule in sorted(chosen, key=lambda item: (-item.severity.rank, item.id)):
        lines.append(f"  {rule.severity.value:<9} {rule.id}")
        lines.append(f"            {rule.title}")
        lines.append(f"            fix: {rule.remediation}")
        lines.append(f"            {rule.source_digest[:16]}  {' '.join(rule.tags)}")
        lines.append("")

    return flow.then(
        Kind.REPORT, tuple(rule.to_dict() for rule in chosen), stage="rules",
        steps=[ReasoningStep(
            claim=f"{len(chosen)} rule(s) are registered in this build",
            layer="governance", operation="shape",
        )],
        facts={"rules": "\n".join(lines), "rule_count": len(chosen)},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="govern", group=GROUP, consumes=Kind.OBSERVATIONS,
            produces=Kind.FINDINGS,
            summary="run every governance rule over what was scanned",
            detail=(
                "Many findings, never one verdict. A rule that raises abstains "
                "and the run continues; a rule that lacks the data it needs "
                "declines rather than guessing. Both are reported as gaps, "
                "because a clean list from checks that never ran is the wrong "
                "kind of clean."
            ),
            params=(
                Param("path", "path", "where to read source for secret scanning"),
                Param("severity", "str", "keep only this severity",
                      choices=("critical", "high", "medium", "low", "info")),
                Param("family", "str", "comma-separated families, instead of all"),
                Param("advisories", "path", "an OSV document or directory of them"),
                Param("popular", "path", "a JSON popularity list, for typosquats"),
                Param("distribution", "str", "how this software reaches users",
                      choices=("internal_only", "network_service", "distributed_binary",
                               "distributed_source", "embedded")),
                Param("linkage", "str", "how dependencies are incorporated",
                      choices=("dynamic", "static", "separate_process", "unmodified")),
                Param("project_license", "str", "this project's own licence"),
                Param("no_sources", "bool", "skip secret scanning", default=False),
            ),
            examples=(
                "discover . | govern",
                "discover . | govern --severity critical | explain",
            ),
            run=_govern,
        ),
        Verb(
            name="rules", group=GROUP, produces=Kind.REPORT,
            summary="what this build checks, and each rule's fingerprint",
            detail=(
                "A rule's `source_digest` covers its logic, not just its name, so "
                "a finding raised last month can be checked against the rule as it "
                "stands today: if the digest moved, the question moved."
            ),
            params=(Param("tag", "str", "only rules carrying this tag"),),
            examples=("rules", "rules --tag license"),
            run=_rules,
        ),
    )
