"""Assembling `Guidance` — an answer that is never a bare value.

Invariant 5 says every answer carries its reasoning path and its gaps. `Guidance`
is where that becomes a type rather than a convention: four parts, all required —
what the answer is, how it was reached, what limits it, and what to ask or do
next. Returning only the first would make the platform a lookup table.

The two generated parts are the interesting ones, and both are **derived, never
authored**:

`next_questions` are ranked by expected information gain over *this* answer's
gaps and blind spots. A question list written by hand would offer the same five
prompts regardless of what was found, which is a menu rather than guidance. These
are the questions whose answers would most improve the answer in front of you, and
each one is a composition that will actually run.

`actions` are what the platform can *do*, each carrying its cost. A suggested
upgrade says whether it breaks anything; a suggested declaration says which gap it
closes. Offering an action without its consequence is how a console becomes a
button somebody regrets pressing.
"""

from __future__ import annotations

import shlex
from typing import Any, Iterable, Mapping, Sequence

from ..domain.finding import Gap, GapKind
from ..domain.reasoning import (
    ActionKind,
    Guidance,
    Question,
    ReasoningPath,
    ReasoningStep,
    SuggestedAction,
    rank_questions,
)
from .pipeline import PipelineResult

#: How many questions to offer. Enough to give a direction, few enough to read.
QUESTIONS = 4

#: How many actions. Fewer, because an action changes something.
ACTIONS = 5


def guidance_for(
    result: PipelineResult,
    *,
    question: str = "",
    root: str = ".",
    facts: Mapping[str, Any] | None = None,
) -> Guidance:
    """A pipeline result → the answer, its reasoning, its limits, and what next.

    `facts` overlays the pipeline context's own. The caller usually has more than
    the layers do — a composition that ran `link` before `reason` knows how many
    cross-file joins contradicted, and the layers never saw that. Overlaying
    rather than replacing means the answer improves when the pipeline was richer
    and is still correct when it was not.
    """
    merged = dict(result.context.facts)
    merged.update(facts or {})
    answer = _answer(merged)

    return Guidance(
        answer=answer,
        reasoning=ReasoningPath(steps=result.steps, question=question),
        gaps=result.gaps,
        next_questions=rank_questions(
            _questions(result, root, merged), limit=QUESTIONS,
        ),
        actions=_actions(result, root)[:ACTIONS],
        summary=_summary(merged, result),
    )


def _answer(facts: Mapping[str, Any]) -> dict[str, Any]:
    """What the layers concluded, as a structure a caller can read.

    Deliberately the *facts* rather than a sentence: a caller who wants prose has
    `summary`, and one who wants to act needs numbers they can compare.
    """
    validation = facts.get("validation", {}) or {}
    constraints = facts.get("constraints", {}) or {}
    impact = facts.get("impact", {}) or {}
    optimization = facts.get("optimization", {}) or {}

    return {
        # `identities` when a resolver ran, `nodes` when only the layers did. The
        # two count the same thing by different routes, and reporting zero
        # packages for a tree that plainly has some would be the kind of wrong
        # that makes an operator stop reading the rest.
        "packages": facts.get("identities") or facts.get("nodes", 0),
        "relationships": facts.get("edges", 0),
        "contradictions": facts.get("contradictions", 0)
        + facts.get("contradicting_joins", 0),
        "phantom_dependencies": validation.get("phantom", 0),
        "unused_declarations": validation.get("unused", 0),
        "satisfiable": constraints.get("satisfiable"),
        "conflicts": constraints.get("conflicts", 0),
        "largest_blast_radius": impact.get("largest", 0),
        "hubs": impact.get("hubs", []),
        "upgrade_options": optimization.get("upgrades", 0),
        "duplicate_versions": optimization.get("duplicates", 0),
    }


def _summary(facts: Mapping[str, Any], result: PipelineResult) -> str:
    """One sentence, leading with whatever is worst.

    Ordered by severity rather than by layer number: an operator reading one line
    should be told the conflict, not the package count.
    """
    constraints = facts.get("constraints", {}) or {}
    validation = facts.get("validation", {}) or {}
    # Both kinds count. `contradicting_joins` is a pin outside its declared
    # range; `contradictions` is one coordinate pinned twice. Leading with
    # "nothing contradictory found" while the answer below reports a
    # contradiction is the worst line this function could print.
    joins = int(facts.get("contradicting_joins", 0) or 0)
    pins = int(facts.get("contradictions", 0) or 0)
    contradictions = joins + pins

    if constraints.get("satisfiable") is False:
        return (
            f"{constraints.get('conflicts', 0)} version conflict(s): the declared "
            f"ranges cannot all hold at once"
        )
    if joins:
        return (
            f"{joins} lockfile pin(s) fall outside the range that was declared "
            f"for them; the build is not what the manifest says"
        )
    if pins:
        return (
            f"{pins} package(s) are pinned to two different versions in one "
            f"tree; somebody's build is not what they think it is"
        )
    if validation.get("phantom"):
        return (
            f"{validation['phantom']} dependenc(ies) are imported but never "
            f"declared; a clean install would not have them"
        )
    if result.abstained:
        return (
            f"{len(result.abstained)} layer(s) abstained, so this answer is "
            f"partial and says which parts are missing"
        )
    return (
        f"{facts.get('identities') or facts.get('nodes', 0)} package(s) resolved, "
        f"nothing contradictory found"
    )


def _questions(
    result: PipelineResult, root: str, facts: Mapping[str, Any],
) -> list[Question]:
    """The questions worth asking next, ranked by what answering them would buy.

    Every one is a runnable composition, so the console is never a dead end: a
    question the platform cannot act on is a prompt, not guidance.
    """
    found: list[Question] = []
    where = shlex.quote(root or ".")

    constraints = facts.get("constraints", {}) or {}
    validation = facts.get("validation", {}) or {}
    impact = facts.get("impact", {}) or {}
    optimization = facts.get("optimization", {}) or {}

    if constraints.get("conflicts"):
        found.append(Question(
            text="which two requirements conflict, and what does each demand?",
            intent="constraints",
            information_gain=0.95,
            rationale=(
                "an unsatisfiable set is only actionable once the pair is named"
            ),
            parameters={"pipeline": f"discover {where} | link | constraints | findings"},
        ))

    if validation.get("phantom"):
        found.append(Question(
            text="which imports are not declared anywhere?",
            intent="validation",
            information_gain=0.85,
            rationale="a phantom dependency breaks on a clean install, not here",
            parameters={"pipeline": f"discover {where} | reason | explain"},
        ))

    if impact.get("largest", 0) > 1:
        hub = (impact.get("hubs") or [""])[0]
        # The name without its version, which is what `radius --package` matches
        # on. Offering the question about `flask` and a pipeline that answers it
        # about everything would be a question the platform then dodges.
        name = hub.rsplit("/", 1)[-1].split("@", 1)[0]
        found.append(Question(
            text=f"what breaks if {hub.rsplit('/', 1)[-1]} changes?",
            intent="impact",
            information_gain=0.8,
            rationale=(
                f"it is the most depended-on thing here, reaching "
                f"{impact['largest']}"
            ),
            parameters={
                "pipeline": (
                    f"discover {where} | reason | radius "
                    f"--package {shlex.quote(name)}"
                    if name else f"discover {where} | reason | radius"
                ),
            },
        ))

    if optimization.get("upgrades"):
        found.append(Question(
            text="which upgrades are free, and which break something?",
            intent="optimization",
            information_gain=0.6,
            rationale="the safe ones cost nothing and are worth taking now",
            parameters={"pipeline": f"discover {where} | reason | options"},
        ))

    if result.gaps:
        # A gap the operator can close themselves outranks one they cannot: being
        # told about a missing analyser is information, being told to attach a
        # repository is a next step.
        actionable = [gap for gap in result.gaps if gap.actionable]
        chosen = actionable[0] if actionable else result.gaps[0]
        found.append(Question(
            text="what is limiting the confidence of this answer?",
            intent="gaps",
            information_gain=0.7 if actionable else 0.45,
            rationale=chosen.detail[:120],
            parameters={"pipeline": f"discover {where} | reason | ask"},
        ))

    if not found:
        found.append(Question(
            text="show me the evidence behind this",
            intent="explain",
            information_gain=0.4,
            rationale="nothing is wrong, so the useful next move is verification",
            parameters={"pipeline": f"discover {where} | reason | explain"},
        ))

    return found


def _actions(result: PipelineResult, root: str) -> list[SuggestedAction]:
    """What can be done, each with its consequence stated."""
    found: list[SuggestedAction] = []
    where = shlex.quote(root or ".")
    named = _names(result)
    enrichments = result.context.enrichments.values()

    for enrichment in enrichments:
        subject = named.get(enrichment.subject, enrichment.subject)
        if enrichment.attribute == "safe_upgrade":
            found.append(SuggestedAction(
                kind=ActionKind.UPGRADE,
                summary=(
                    f"upgrade {subject} to {enrichment.value} — every declared "
                    f"range still admits it"
                ),
                target=subject,
                command=f"slpie 'discover {where} | reason | options'",
                breaking=False,
            ))
        elif enrichment.attribute == "upgrade_option":
            found.append(SuggestedAction(
                kind=ActionKind.UPGRADE,
                summary=(
                    f"upgrade {subject} to {enrichment.value} — "
                    f"{enrichment.rationale[:90]}"
                ),
                target=subject,
                command=f"slpie 'discover {where} | reason | options'",
                breaking=True,
            ))
        elif enrichment.attribute == "phantom_dependency":
            found.append(SuggestedAction(
                kind=ActionKind.DECLARE,
                summary=(
                    f"declare {subject} — it is imported but nothing declares it"
                ),
                target=subject,
                command=f"slpie 'discover {where} | reason | ask'",
            ))
        # Not `resolved_version`: L4 emits that for *every* join it makes,
        # including the ones where the pin sits happily inside the declared
        # range. Offering "reconcile this" for a healthy dependency is the kind
        # of false alarm that teaches an operator to ignore the list.
        elif enrichment.attribute in ("contradiction", "declaration_contradicted"):
            found.append(SuggestedAction(
                kind=ActionKind.PIN,
                summary=(
                    f"reconcile {subject}: the pin and the declared range "
                    f"disagree"
                ),
                target=subject,
                command=f"slpie 'discover {where} | link | findings'",
                breaking=True,
            ))

    for gap in result.gaps:
        if not gap.actionable:
            continue
        found.append(SuggestedAction(
            kind=ActionKind.RESCAN if gap.kind is GapKind.PARSE_FAILURE
            else ActionKind.ATTACH,
            summary=gap.remediation or gap.detail,
            target=gap.subject,
            command=f"slpie 'discover {where} | reason | options'",
            closes_gap=gap.id,
        ))

    #: Deduplicated by id, so the same upgrade offered by two layers is one
    #: button rather than two identical ones.
    seen: dict[str, SuggestedAction] = {}
    for action in found:
        seen.setdefault(action.id, action)

    # Non-breaking first: an operator scanning the list should meet the free
    # options before the ones that cost something.
    return sorted(seen.values(), key=lambda item: (item.breaking, item.summary))


def _names(result: PipelineResult) -> dict[str, str]:
    """Node id → the identity a human recognises.

    An enrichment names its subject by node id, which is a blake2b digest. An
    action reading "reconcile b3e270c4…" is unreadable, and an unreadable
    suggestion is one nobody acts on — so the identity is looked up wherever the
    resolution knows it.
    """
    resolution = result.context.resolution
    return {
        entry.node_id: entry.identity
        for entry in getattr(resolution, "resolved", ())
        if entry.identity
    }


def render(guidance: Guidance, *, width: int = 76) -> str:
    """Guidance for a terminal. What `slpie ask` prints."""
    import textwrap

    lines = ["", f"  {guidance.summary}", ""]

    for key, value in guidance.answer.items():
        if value in (0, None, [], ""):
            continue
        lines.append(f"    {key.replace('_', ' '):<24} {value}")

    if guidance.gaps:
        lines += ["", "  limiting this answer:"]
        for gap in guidance.gaps[:5]:
            wrapped = textwrap.wrap(gap.detail, width - 6) or [gap.detail]
            lines.append(f"    - {wrapped[0]}")
            lines.extend(f"      {line}" for line in wrapped[1:])

    if guidance.next_questions:
        lines += ["", "  worth asking next:"]
        for item in guidance.next_questions:
            lines.append(f"    · {item.text}")
            if item.parameters.get("pipeline"):
                lines.append(f"        slpie '{item.parameters['pipeline']}'")

    if guidance.actions:
        lines += ["", "  what you can do:"]
        for action in guidance.actions:
            mark = " [breaking]" if action.breaking else ""
            lines.append(f"    · {action.summary}{mark}")

    lines += [
        "",
        f"  confidence {guidance.confidence}"
        + ("" if guidance.complete else " — see the limits above"),
        "",
    ]
    return "\n".join(lines)
