"""Capture as verbs — identification, the firewall, and what was held.

`capture` walks a tree, identifies each file by content, runs the firewall chain
and reports what was accepted, quarantined and refused. It produces OBSERVATIONS
so it composes with `link` and everything downstream, which is the point: capture
is not a separate pipeline, it is the front of the same one.

`quarantine` answers the question that makes the design honest — *what is being
held, and why*. A platform that quarantines without a way to list what it holds
has simply lost the data with extra steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...capture import (
    Candidate,
    Depth,
    Verdict,
    default_chain,
    digest_of,
    identify,
    parse,
)
from ...domain.evidence import Evidence, EvidenceKind
from ...domain.finding import Gap
from ...domain.reasoning import ReasoningStep
from ...plugins.protocol import Observation
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "capture"

SKIP = (
    "/.git/", "/node_modules/", "/.venv/", "/__pycache__/", "/dist/",
    "/build/", "/.tox/", "/target/", "/vendor/", "/site-packages/",
)


def _capture(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    root = Path(str(arguments.get("path") or context.root or ".")).expanduser()
    if not root.exists():
        raise VerbError(f"{root} does not exist")

    try:
        depth = Depth[str(arguments.get("depth") or "structure").upper()]
    except KeyError:
        raise VerbError(
            f"{arguments.get('depth')!r} is not a depth; use probe, structure, "
            f"model or semantic"
        ) from None

    limit = max(1, int(arguments.get("limit") or 5000))
    chain = default_chain()

    observations: list[Observation] = []
    decisions: list[Any] = []
    counts: dict[str, int] = {item.value: 0 for item in Verdict}
    held: list[dict[str, Any]] = []

    candidates = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in candidates:
        if len(decisions) >= limit:
            break
        if not path.is_file():
            continue
        uri = path.resolve().as_uri()
        if any(part in uri for part in SKIP):
            continue

        try:
            # PROBE reads 4KB; deeper reads the file. Paying only for the depth
            # requested is the whole optimisation.
            payload = (
                path.read_bytes() if depth >= Depth.STRUCTURE
                else path.open("rb").read(4096)
            )
        except OSError as error:
            continue

        found = identify(payload, uri, depth=depth)
        decision = chain.evaluate(Candidate(
            uri=uri, size=path.stat().st_size, format=found,
            digest=digest_of(payload),
        ))
        decisions.append(decision)
        counts[decision.verdict.value] += 1

        if decision.verdict is Verdict.QUARANTINE:
            held.append({
                "uri": uri, "digest": decision.candidate.digest,
                "format": found.name, "reason": decision.reason,
                "rule": decision.rule,
            })

        if not decision.projected:
            continue

        observations.append(Observation(
            kind="declares",
            subject=f"urn:slpie:file:{path.name}",
            evidence=Evidence(
                kind=EvidenceKind.MANIFEST_DECLARED,
                location=found.evidence[0].location if found.evidence
                else Evidence(
                    kind=EvidenceKind.NAME_HEURISTIC,
                    location=decision.candidate.format.evidence[0].location,
                    extractor="slpie.capture",
                ).location,
                extractor="slpie.capture", extractor_version="1",
                excerpt=f"{found.name}: {found.reason}"[:300],
            ),
            properties={
                "format": found.name, "dialect": found.dialect,
                "confidence": found.confidence, "digest": decision.candidate.digest,
                "strategies": list(found.strategies),
            },
        ))

    gaps = tuple(
        gap for gap in (item.gap() for item in decisions) if gap is not None
    )[:40]

    return flow.then(
        Kind.OBSERVATIONS, tuple(observations), stage="capture",
        steps=[ReasoningStep(
            claim=(
                f"identified {len(decisions)} file(s) by content at depth "
                f"{depth.label}: {counts['accept']} accepted, "
                f"{counts['quarantine']} held, "
                f"{counts['reject'] + counts['drop']} refused"
            ),
            layer="capture", operation="capture",
            evidence=tuple(
                item.evidence for item in observations[:6]
                if item.evidence is not None
            ),
        )],
        gaps=gaps,
        facts={
            "captured": len(decisions), **counts,
            "quarantined": held[:50],
            "depth": depth.label,
            "chain": [
                item.caller_view() for item in decisions
                if item.verdict is not Verdict.ACCEPT
            ][:50],
        },
    )


def _quarantine(flow: Flow, _arguments: Mapping[str, Any], _context: Context) -> Flow:
    """What is being held, and why. Without this, quarantine is data loss."""
    held = list(flow.facts.get("quarantined", ()))
    lines = ["  nothing is held."] if not held else [
        f"  {len(held)} file(s) held, digested and retrievable:", "",
        *(
            f"    {item['uri'].rsplit('/', 1)[-1]:<28} {item['rule']}\n"
            f"      {item['reason'][:96]}"
            for item in held[:30]
        ),
    ]
    return flow.then(
        Kind.REPORT, {"quarantined": held}, stage="quarantine",
        steps=[ReasoningStep(
            claim=f"{len(held)} capture(s) held rather than admitted",
            layer="capture", operation="query",
        )],
        facts={"quarantine": "\n".join(lines)},
    )


def _chain(flow: Flow, _arguments: Mapping[str, Any], _context: Context) -> Flow:
    """The rule chain itself, so an operator can read what will happen."""
    chain = default_chain()
    return flow.then(
        Kind.REPORT, chain.to_dict(), stage="chain",
        steps=[ReasoningStep(
            claim=f"{len(chain)} rules; anything matching none is held",
            layer="capture", operation="query",
        )],
        facts={"chain": chain.render()},
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="capture", group=GROUP, produces=Kind.OBSERVATIONS,
            summary="identify files by content and run them past the firewall",
            detail=(
                "An extension is a name heuristic at 0.25; magic bytes and a "
                "structural parse are far stronger. When they disagree the file "
                "is held rather than routed on a guess. Depth is the "
                "optimisation: probe reads 4KB, model reads everything."
            ),
            params=(
                Param("path", "path", "what to capture", default="."),
                Param("depth", "str", "how far to look", default="structure",
                      choices=("probe", "structure", "model", "semantic")),
                Param("limit", "int", "maximum files", default=5000),
            ),
            examples=(
                "capture .",
                "capture . --depth semantic | link",
            ),
            run=_capture,
        ),
        Verb(
            name="quarantine", group=GROUP, consumes=Kind.ANY,
            produces=Kind.REPORT,
            summary="what was held rather than admitted, and why",
            detail=(
                "A platform that quarantines without a way to list what it holds "
                "has lost the data with extra steps."
            ),
            examples=("capture . | quarantine",),
            run=_quarantine,
        ),
        Verb(
            name="chain", group=GROUP, produces=Kind.REPORT,
            summary="the firewall's rules, in the order they are evaluated",
            examples=("chain",),
            run=_chain,
        ),
    )
