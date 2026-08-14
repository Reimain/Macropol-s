"""Suggestion and routines, as verbs — so guidance composes like everything else.

`suggest` is polymorphic and produces `SAME`, which is the decision that matters
here: it can be appended to *any* pipeline without changing what that pipeline
produces. `discover . | link | findings | suggest` still yields findings — with the
next moves attached to the flow's facts. Guidance that changed the answer would be
guidance nobody could safely add to a working command.

`routine` is how a reviewer keeps what they learned. Claiming is deliberate and
validated: a short key is a promise, and a key that sometimes fails costs more than
no key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...domain.reasoning import ReasoningStep
from ..flow import Flow, Kind
from ..verb import Context, Param, Verb, VerbError

GROUP = "guidance"

#: Where the acceptance record and the claimed routines live. Under the working
#: directory rather than in a home directory, because routines are about *this*
#: repository — `rc` should mean something different in a different codebase, and a
#: global list would make every key a compromise.
STATE = ".slpie"


def _history(context: Context) -> Any:
    from ...suggest import History

    return History(Path(context.root or ".") / STATE / "accepted.jsonl")


def _routines(context: Context) -> Any:
    from ...suggest import Routines

    return Routines(Path(context.root or ".") / STATE / "routines.json")


def _touch_for(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Any:
    """What the reviewer is looking at, inferred from the flow when not stated.

    Inferring is the point: a reviewer appending `| suggest` has not told us what
    they are stuck on, and asking would be a worse experience than reading the
    situation. An empty result and a findings list want different next moves, and
    the flow already says which one this is.
    """
    from ...suggest import Touch, TouchKind, on_empty

    subject = str(arguments.get("about") or "")
    stated = str(arguments.get("kind") or "")

    if stated:
        try:
            return Touch(kind=TouchKind(stated), subject=subject or flow.kind.value,
                         context={"pipeline": " | ".join(flow.stages)})
        except ValueError:
            raise VerbError(
                f"{stated!r} is not a touch kind; use one of "
                f"{', '.join(item.value for item in TouchKind)}"
            ) from None

    if flow.empty and flow.stages:
        return on_empty(" | ".join(flow.stages))

    if subject:
        return Touch(
            kind=TouchKind.NODE if subject.startswith(("pkg:", "urn:"))
            else TouchKind.TERM,
            subject=subject,
            context={"pipeline": " | ".join(flow.stages)},
        )

    if flow.kind is Kind.FINDINGS and not flow.empty:
        first = flow.items[0]
        return Touch(
            kind=TouchKind.FINDING,
            subject=str(getattr(first, "subject", "") or flow.kind.value),
            context={
                "pipeline": " | ".join(flow.stages),
                "evidence": tuple(getattr(first, "evidence", ())),
            },
        )

    if flow.gaps:
        return Touch(
            kind=TouchKind.GAP, subject=flow.gaps[0].subject,
            context={"pipeline": " | ".join(flow.stages)},
        )

    return Touch(
        kind=TouchKind.KIND, subject=flow.kind.value,
        context={"pipeline": " | ".join(flow.stages)},
    )


def _suggest(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    from ...suggest import SuggestionEngine

    touch = _touch_for(flow, arguments, context)
    engine = SuggestionEngine(
        root=context.root or ".", history=_history(context),
    )
    suggestions = engine.suggest(touch)

    return flow.then(
        Kind.SAME, flow.value, stage="suggest",
        steps=[ReasoningStep(
            claim=(
                f"{len(suggestions)} next move(s) for a {touch.kind.label}"
                + (f" on {touch.subject}" if touch.subject else "")
            ),
            layer="guidance", operation="shape",
        )],
        facts={
            "suggestions": suggestions.to_dict(),
            "suggestion_keys": list(suggestions.keys()),
            "guidance": suggestions.render(),
        },
    )


def _accept(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Record that a suggested path was taken. This is how the ranking learns."""
    key = str(arguments.get("key") or "")
    if not key:
        raise VerbError("accept needs --key, the key of the suggestion you took")

    touch = _touch_for(flow, arguments, context)
    from ...suggest import SuggestionEngine

    engine = SuggestionEngine(root=context.root or ".", history=_history(context))
    suggestions = engine.suggest(touch)
    chosen = suggestions.by_key(key)
    if chosen is None:
        raise VerbError(
            f"no suggestion is keyed {key!r} here"
            + (f"; offered: {', '.join(suggestions.keys())}"
               if suggestions.keys() else "")
        )

    history = _history(context)
    history.accept(touch, chosen, actor=context.actor)

    return flow.then(
        Kind.SAME, flow.value, stage="accept",
        steps=[ReasoningStep(
            claim=f"accepted `{chosen.pipeline}`; it will rank higher here",
            layer="guidance", operation="shape",
        )],
        facts={"accepted": chosen.pipeline, "run_next": chosen.pipeline},
    )


def _dismiss(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """Dismissal is as informative as acceptance, and is weighted equally."""
    key = str(arguments.get("key") or "")
    if not key:
        raise VerbError("dismiss needs --key")

    touch = _touch_for(flow, arguments, context)
    from ...suggest import RETIRE_AFTER, SuggestionEngine

    engine = SuggestionEngine(root=context.root or ".", history=_history(context))
    chosen = engine.suggest(touch).by_key(key)
    if chosen is None:
        raise VerbError(f"no suggestion is keyed {key!r} here")

    history = _history(context)
    history.dismiss(touch, chosen, actor=context.actor,
                    reason=str(arguments.get("reason") or ""))

    return flow.then(
        Kind.SAME, flow.value, stage="dismiss",
        steps=[ReasoningStep(
            claim=(
                f"dismissed `{chosen.pipeline}`"
                + (" — it will not be offered here again"
                   if history.retired(touch, chosen)
                   else f"; {RETIRE_AFTER} dismissals retire it here")
            ),
            layer="guidance", operation="shape",
        )],
        facts={"dismissed": chosen.pipeline},
    )


def _routine(flow: Flow, arguments: Mapping[str, Any], context: Context) -> Flow:
    """List, claim or forget a routine."""
    from ...suggest import RoutineError

    routines = _routines(context)
    action = str(arguments.get("action") or "list").lower()

    if action == "list":
        return flow.then(
            Kind.REPORT, routines.to_dict(), stage="routine",
            steps=[ReasoningStep(
                claim=f"{len(routines)} claimed routine(s)",
                layer="guidance", operation="query",
            )],
            facts={"routines": routines.render()},
        )

    name = str(arguments.get("name") or "")
    if action == "claim":
        if not name:
            raise VerbError("routine claim needs --name")
        pipeline = str(arguments.get("pipeline") or "")
        try:
            if pipeline:
                claimed = routines.claim(
                    name, pipeline, key=str(arguments.get("key") or ""),
                    actor=context.actor,
                )
            else:
                # Claiming from the session is the intended path: you ran things,
                # some worked, and at the end you keep the best one.
                claimed = routines.claim_session(
                    name, _history(context).accepted_pipelines(),
                    key=str(arguments.get("key") or ""), actor=context.actor,
                )
        except RoutineError as error:
            raise VerbError(str(error)) from None

        return flow.then(
            Kind.REPORT, claimed.to_dict(), stage="routine",
            steps=[ReasoningStep(
                claim=f"claimed `{claimed.pipeline}` as {claimed.key}",
                layer="guidance", operation="shape",
            )],
            facts={
                "claimed": claimed.key,
                "routines": (
                    f"  claimed as [{claimed.key}]\n"
                    f"      {claimed.pipeline}\n\n"
                    f"  type `slpie {claimed.key}` to run exactly this."
                ),
            },
        )

    if action == "forget":
        if not name:
            raise VerbError("routine forget needs --name, the key to forget")
        removed = routines.forget(name)
        return flow.then(
            Kind.REPORT, {"forgotten": removed, "key": name}, stage="routine",
            steps=[ReasoningStep(
                claim=f"{'forgot' if removed else 'no such routine:'} {name}",
                layer="guidance", operation="shape",
            )],
        )

    raise VerbError(
        f"{action!r} is not a routine action; use list, claim or forget"
    )


def verbs() -> tuple[Verb, ...]:
    return (
        Verb(
            name="suggest", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="what to look at next, and why",
            detail=(
                "Produces SAME, so appending it never changes what a pipeline "
                "yields — guidance that altered the answer would be guidance "
                "nobody could safely add to a working command. Candidates come "
                "from the type graph, so a suggestion can never be something this "
                "platform cannot do; only their order is learned."
            ),
            params=(
                Param("about", "str", "the node, finding or term you are stuck on"),
                Param("kind", "str", "the touch kind, when the flow does not say"),
            ),
            examples=(
                "discover . | link | findings | suggest",
                "discover . | link | suggest --about lockfile",
            ),
            run=_suggest,
        ),
        Verb(
            name="accept", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="record that a suggested path was worth taking",
            detail=(
                "This is how the ranking learns. Acceptance is a fact, so `why is "
                "it offering this first` has a real answer."
            ),
            params=(
                Param("key", "str", "the suggestion's key", required=True),
                Param("about", "str", "the subject, if it was stated"),
            ),
            examples=("discover . | link | findings | accept --key dlfe",),
            run=_accept,
        ),
        Verb(
            name="dismiss", group=GROUP, consumes=Kind.ANY, produces=Kind.SAME,
            summary="record that a suggested path was not useful here",
            detail=(
                "Weighted equally with acceptance. A system that only learns from "
                "acceptance keeps proposing what you have refused four times."
            ),
            params=(
                Param("key", "str", "the suggestion's key", required=True),
                Param("reason", "str", "why, if you want it on the record"),
                Param("about", "str", "the subject, if it was stated"),
            ),
            examples=("discover . | link | findings | dismiss --key si",),
            run=_dismiss,
        ),
        Verb(
            name="routine", group=GROUP, produces=Kind.REPORT,
            summary="claim what you did as a short key, or list what you claimed",
            detail=(
                "Claiming is deliberate and validated: a routine that would not "
                "run cannot be claimed, because a short key is a promise."
            ),
            params=(
                Param("action", "str", "list, claim or forget", default="list",
                      choices=("list", "claim", "forget")),
                Param("name", "str", "what to call it"),
                Param("pipeline", "str", "the composition; omit to claim the session"),
                Param("key", "str", "a specific key, instead of a minted one"),
            ),
            examples=(
                "routine",
                "routine --action claim --name release-check "
                "--pipeline 'discover . | link | findings'",
            ),
            run=_routine,
        ),
    )
