"""Phase 10's intelligence, delivered as verbs rather than as a new surface.

L5–L8 already run inside `reason`; they append to the pipeline context and their
conclusions sit there as enrichments and facts. Without a verb, that work exists
and nobody can reach it — which would be exactly the drift §24 exists to prevent,
one capability declared in a layer and in no registry.

So three verbs read what the layers concluded, and each is deliberately narrow:

* **`ask`** assembles `Guidance` — the answer, its reasoning, its gaps, the
  questions worth asking next and the actions available. It is the only verb that
  produces `GUIDANCE`, and it is where invariant 5 becomes a type rather than a
  convention.
* **`radius`** surfaces L7's blast radii. It exists because the environment's
  `impact` verb needs a graph store, and "what depends on this" is a question
  worth answering on a bare checkout with nothing installed.
* **`options`** surfaces L8's upgrade enumeration. It **enumerates and does not
  recommend**: every option carries what it changes and who it breaks, ordered
  safest first, because which one to take is a judgement about the codebase and
  the platform is not in a position to make it.

All three consume `ENRICHMENTS`, so the only way to reach them is through `reason`
— which is correct, and is the type system saying so rather than a runtime check
somebody has to remember to write.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "intelligence"

#: How many upgrade options to render. Beyond this the list stops being read, and
#: an unread enumeration is the same as no enumeration.
OPTIONS = 20


def _result(flow: Flow, verb: str) -> Any:
    """The pipeline result flowing in, or a refusal naming what was expected.

    `reason` is the only producer of `ENRICHMENTS`, so this cannot fail through
    the CLI — but a plugin verb could declare the same kind, and receiving
    something else should say so rather than raising an attribute error three
    lines further down.
    """
    result = flow.value
    if not hasattr(result, "context") or not hasattr(result, "gaps"):
        raise VerbError(
            f"{verb} needs what `reason` produces — a pipeline result carrying "
            f"the layers' context. It was handed {type(result).__name__}"
        )
    return result


def _ask(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """The answer, and everything that qualifies it."""
    from ...reasoning.guidance import guidance_for, render

    result = _result(flow, "ask")
    question = str(arguments.get("question") or "")

    guidance = guidance_for(
        result,
        question=question,
        root=str(context.root or "."),
        # The flow knows things the layers do not: a composition that ran `link`
        # before `reason` counted cross-file joins, and the layers never saw
        # that count. Overlaying keeps the answer as good as the pipeline was.
        facts=flow.facts,
    )

    return flow.then(
        Kind.GUIDANCE, guidance, stage="ask",
        # `shape`, so appending `ask` never makes a grounded answer look
        # unfounded: assembling an answer derives no new claim about the world,
        # and every claim it reports was already cited by the layer that made it.
        steps=[ReasoningStep(
            claim=guidance.summary,
            layer="guidance", operation="shape",
            confidence=guidance.confidence,
        )],
        facts={
            "answer": render(guidance),
            "questions": [item.text for item in guidance.next_questions],
            "suggested_actions": len(guidance.actions),
            "guidance_confidence": guidance.confidence,
        },
    )


def _radius(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """L7's blast radii, largest first — offline, over the resolution's links."""
    result = _result(flow, "radius")
    reaches = tuple(result.context.facts.get("reaches", ()))

    minimum = max(1, int(arguments.get("min_size") or 1))
    subject = str(arguments.get("package") or "").lower()

    chosen = [item for item in reaches if item.size >= minimum]
    gaps: list[Any] = []
    if subject:
        chosen = [
            item for item in chosen
            if subject in (item.identity or item.node).lower()
        ]
        if not chosen:
            # An empty radius reads as "nothing depends on it", which is a very
            # different statement from "no node here is called that". The
            # planner fills this parameter from a noun it extracted out of a
            # question, so getting the noun wrong must not answer confidently.
            from ...domain.finding import Gap, GapKind

            gaps.append(Gap(
                kind=GapKind.UNRESOLVED_DEPENDENCY,
                subject=subject,
                detail=(
                    f"no node in this tree matches {subject!r}, so this is not "
                    f"the answer that nothing depends on it — it is the answer "
                    f"that it was not found"
                ),
                remediation=(
                    "check the spelling, or drop --package to see every radius"
                ),
                confidence_impact=0.4,
            ))
    chosen.sort(key=lambda item: (-item.size, item.node))

    steps = [ReasoningStep(
        claim=(
            f"{len(chosen)} node(s) have at least {minimum} dependent(s)"
            + (f" matching {subject!r}" if subject else "")
        ),
        layer="impact", operation="shape",
    )]
    # The claims about the world are the reaches themselves, and each cites the
    # evidence of the node it is about — so a pipeline ending here is grounded
    # for the same reason `reason` was.
    for reach in chosen[:5]:
        steps.append(ReasoningStep(
            claim=(
                f"{reach.identity or reach.node} is depended on by {reach.size}; "
                f"the least certain path is believed at {reach.weakest:.2f}"
            ),
            layer="impact", operation="traverse",
            confidence=reach.weakest,
            evidence=tuple(
                result.context.evidence[item]
                for item in _evidence_of(result, reach.node)
                if item in result.context.evidence
            )[:2],
        ))

    return flow.then(
        Kind.IMPACT, tuple(chosen), stage="radius",
        steps=steps, gaps=tuple(gaps),
        facts={
            "radius": len(chosen),
            "largest": chosen[0].size if chosen else 0,
        },
    )


def _evidence_of(result: Any, node: str) -> tuple[str, ...]:
    """The evidence ids behind one resolved node, or none if it is not resolved."""
    resolution = result.context.resolution
    for entry in getattr(resolution, "resolved", ()):
        if entry.node_id == node:
            return tuple(item.id for item in entry.evidence)
    return ()


def _options(flow: Flow, arguments: Mapping[str, Any], _context: Context) -> Flow:
    """What could change, and what each change costs. Recommends nothing.

    Safe options first, then the ones that break something — because an operator
    scanning the list should meet the free moves before the expensive ones, and
    because a list that mixed them would need to be read in full to be safe.
    """
    result = _result(flow, "options")
    enrichments = result.context.enrichments
    # An enrichment names its subject by node id, which is a blake2b digest. An
    # option reading "upgrade 92797588…" is one nobody can act on.
    named = {
        entry.node_id: entry.identity
        for entry in getattr(result.context.resolution, "resolved", ())
        if entry.identity
    }

    wanted = str(arguments.get("kind") or "").lower()
    safe_only = bool(arguments.get("safe"))

    found: list[dict[str, Any]] = []
    for enrichment in enrichments.values():
        if enrichment.attribute not in (
            "safe_upgrade", "upgrade_option", "duplicate_versions",
            "unconstrained_range",
        ):
            continue
        if wanted and enrichment.attribute != wanted:
            continue
        breaking = enrichment.attribute == "upgrade_option"
        if safe_only and breaking:
            continue
        found.append({
            "kind": enrichment.attribute,
            "subject": named.get(enrichment.subject, enrichment.subject),
            "value": enrichment.value,
            "breaking": breaking,
            "confidence": enrichment.confidence,
            "why": enrichment.rationale,
            "enrichment": enrichment.id,
        })

    found.sort(key=lambda item: (item["breaking"], item["kind"], item["subject"]))
    found = found[:OPTIONS]

    return flow.then(
        Kind.REPORT, tuple(found), stage="options",
        steps=[ReasoningStep(
            claim=(
                f"{len(found)} option(s): "
                f"{sum(1 for item in found if not item['breaking'])} that break "
                f"nothing, {sum(1 for item in found if item['breaking'])} that do. "
                f"Which to take is a judgement about this codebase"
            ),
            layer="optimization", operation="shape",
        )],
        facts={
            "options": len(found),
            "safe_options": sum(1 for item in found if not item["breaking"]),
        },
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="ask", group=GROUP, consumes=Kind.ENRICHMENTS,
            produces=Kind.GUIDANCE,
            summary="the answer, its reasoning, its gaps, and what to ask next",
            detail=(
                "Never a bare value. Invariant 5 says every answer carries its "
                "reasoning path and its limits, and this is where that becomes a "
                "type rather than a convention: the questions are ranked by what "
                "answering them would buy, and every one is a composition that "
                "will actually run."
            ),
            params=(
                Param("question", "str", "what you were trying to find out"),
            ),
            examples=(
                "discover . | reason | ask",
                # Deliberately unquoted: `slpie help` wraps an example in single
                # quotes for copy-paste, and an example carrying its own would
                # produce a line that does not run when pasted.
                "discover . | reason | ask | explain",
            ),
            run=_ask,
        ),
        Verb(
            name="radius", group=GROUP, consumes=Kind.ENRICHMENTS,
            produces=Kind.IMPACT,
            summary="what depends on what, without needing a database",
            detail=(
                "The environment's `impact` verb answers the same question over "
                "a graph store. This one answers it over the resolution the "
                "layers just built, so it works on a bare checkout — and "
                "confidence propagates as a minimum, so a radius reached only "
                "through a dynamic load is reported as the weak thing it is."
            ),
            params=(
                Param("package", "str", "keep only radii whose subject matches"),
                Param("min_size", "int", "ignore anything smaller", default=1),
            ),
            examples=(
                "discover . | reason | radius",
                "discover . | reason | radius --min_size 3 | head --count 5",
            ),
            run=_radius,
        ),
        Verb(
            name="options", group=GROUP, consumes=Kind.ENRICHMENTS,
            produces=Kind.REPORT,
            summary="every upgrade available, with the cost of each",
            detail=(
                "Enumerates; it does not recommend. Which upgrade to take "
                "depends on how well tested this codebase is and whether it uses "
                "the thing a major bump changed — judgements the platform cannot "
                "make for somebody. It can say exactly what each option costs, "
                "and it orders the free ones first."
            ),
            params=(
                Param("kind", "str", "one family of option", choices=(
                    "safe_upgrade", "upgrade_option", "duplicate_versions",
                    "unconstrained_range",
                )),
                Param("safe", "bool", "only what breaks nothing", default=False),
            ),
            examples=(
                "discover . | reason | options",
                "discover . | reason | options --safe",
            ),
            run=_options,
        ),
    )
