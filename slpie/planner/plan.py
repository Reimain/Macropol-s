"""A plan is a composition, shown before it runs.

This is what "conducting rather than answering" means concretely. You ask a
question; you get back the pipeline that would answer it, with the reason each
stage is there and the gaps that will limit the result — *before* paying for it.

The composition is not generated freely. It is selected from
`VerbRegistry.paths_to(kind)`, which enumerates the pipelines the type graph
already contains. Three consequences, and they are why this is trustworthy:

* the planner **cannot invent a verb**, because it only picks routes that exist;
* it **cannot emit an invalid pipeline**, because every route type-checks by
  construction;
* it works **offline with no model**, because selection is graph search.

When the question is not understood the planner returns a `Clarification` rather
than a plan — options drawn from the registry, and a question back. Picking an
arbitrary composition and running it would be worse than useless: it would look
like an answer to the question that was asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..compose.flow import Kind
from ..compose.parse import render
from ..compose.pipeline import Composition
from ..compose.registry import VerbRegistry, registry as default_registry
from .intent import Intent, read

#: Verbs the planner will not put in a plan it wrote itself. A plan is offered for
#: the operator to run with one keystroke, and a mutating verb must be a decision
#: they typed rather than one the planner slipped in.
NEVER_PLANNED = frozenset({"target"})

#: Which source verb to prefer when several could start the route. `discover`
#: needs nothing, so a plan built on it runs for somebody who has not yet written
#: a manifest — which is most people, the first time.
SOURCE_PREFERENCE = ("discover", "scan", "graph", "search", "reconcile", "declare")


@dataclass(frozen=True, slots=True)
class Step:
    """One stage of a plan, and why the planner put it there."""

    verb: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    because: str = ""

    def render(self) -> str:
        parts = [self.verb]
        for name, value in self.arguments.items():
            parts.append(f"--{name} {value}" if value is not True else f"--{name}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb, "arguments": dict(self.arguments),
            "because": self.because, "rendered": self.render(),
        }


@dataclass(frozen=True, slots=True)
class Plan:
    """A composition, its reasons, and whether it can be run as it stands."""

    question: str
    intent: Intent
    steps: tuple[Step, ...] = ()
    alternatives: tuple[str, ...] = ()
    clarification: str = ""
    options: tuple[str, ...] = ()
    needs_environment: bool = False
    backend: str = "deterministic"

    @property
    def pipeline(self) -> str:
        return " | ".join(step.render() for step in self.steps)

    @property
    def understood(self) -> bool:
        return bool(self.steps)

    def composition(self, *, verbs: VerbRegistry | None = None) -> Composition | None:
        if not self.steps:
            return None
        return Composition.read(self.pipeline, verbs=verbs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent.to_dict(),
            "pipeline": self.pipeline,
            "steps": [step.to_dict() for step in self.steps],
            "alternatives": list(self.alternatives),
            "clarification": self.clarification,
            "options": list(self.options),
            "needs_environment": self.needs_environment,
            "backend": self.backend,
            "understood": self.understood,
        }

    def render(self) -> str:
        """What `slpie plan "<question>"` prints."""
        lines = ["", f'  {self.question}', ""]

        if not self.understood:
            lines.append(f"  {self.clarification}")
            if self.options:
                lines.append("")
                lines.append("  did you mean one of these?")
                for option in self.options:
                    lines.append(f"    slpie '{option}'")
            lines.append("")
            return "\n".join(lines)

        lines.append(f"  {self.pipeline}")
        lines.append("")
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"    {index}. {step.verb:<14} {step.because}")
        lines.append("")

        if self.needs_environment:
            lines.append(
                "  needs an environment: run it where slpie.environment.yaml lives"
            )
        if self.alternatives:
            lines.append("  other routes to the same answer:")
            for alternative in self.alternatives:
                lines.append(f"    {alternative}")
        lines.append("")
        lines.append(f"  planned by the {self.backend} backend. Run it with --run.")
        lines.append("")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.pipeline or f"(no plan: {self.clarification})"


def plan_for(
    question: str,
    *,
    verbs: VerbRegistry | None = None,
    max_length: int = 4,
) -> Plan:
    """A question → the composition that answers it, or a question back."""
    verbs = verbs if verbs is not None else default_registry()
    intent = read(question)

    if not intent.understood:
        return Plan(
            question=question, intent=intent,
            clarification=(
                "I could not tell what kind of answer you want, and guessing "
                "would give you something that looks like an answer to a "
                "different question."
            ),
            options=_suggestions(verbs),
            backend="deterministic",
        )

    routes = [
        route for route in verbs.paths_to(intent.target, max_length=max_length)
        if not any(name in NEVER_PLANNED for name in route)
    ]
    if not routes:
        return Plan(
            question=question, intent=intent,
            clarification=(
                f"nothing this platform can currently do produces a "
                f"{intent.target.label} answer; that is a gap in the verb set, "
                f"not in the question"
            ),
            options=_suggestions(verbs),
        )

    chosen = _prefer(routes, intent, verbs)
    steps = tuple(
        Step(
            verb=name,
            arguments=_arguments(name, intent, verbs),
            because=_because(name, intent, verbs, first=(index == 0)),
        )
        for index, name in enumerate(chosen)
    )

    # Alternatives exclude routes that only differ by a shaping verb wedged in
    # the middle. `graph | explain | impact` is legal and is not a different
    # route; offering it as one is noise that makes the useful alternatives
    # harder to see.
    alternatives = tuple(
        " | ".join(route) for route in routes
        if route != chosen and not _has_interior_filter(route, verbs)
    )[:3]

    return Plan(
        question=question, intent=intent, steps=steps,
        alternatives=alternatives,
        needs_environment=any(
            verbs.require(name).group == "environment" for name in chosen
        ),
    )


def _prefer(
    routes: Sequence[tuple[str, ...]],
    intent: Intent,
    verbs: VerbRegistry,
) -> tuple[str, ...]:
    """The most *useful* route, which is not simply the shortest one.

    Shortest-first was the obvious rule and it gave bad plans. "what is wrong
    here?" resolved to the one-stage `reconcile`, which needs a manifest, a ledger
    and a graph — so the shortest plan was the one most readers could not run,
    while the two-stage `discover | findings` would have worked immediately. And
    "what breaks if lodash 5 lands?" chose `graph | impact`, silently discarding
    the word `lodash` that the question was entirely about.

    So the ordering is: runs-without-setup first, then routes that can actually use
    the subject, then no interior filters, then length.
    """
    def rank(route: tuple[str, ...]) -> tuple[int, int, int, int, str]:
        needs_environment = any(
            verbs.require(name).group == "environment" for name in route
        )
        # A subject is only usable by a verb that takes it. Preferring a route
        # that can consume `lodash` is preferring the plan that answers the
        # question actually asked.
        uses_subject = bool(intent.subject) and any(
            _arguments(name, intent, verbs) for name in route
        )
        return (
            int(needs_environment),
            0 if uses_subject or not intent.subject else 1,
            int(_has_interior_filter(route, verbs)),
            len(route),
            _source_rank(route),
        )

    return min(routes, key=rank)


def _source_rank(route: tuple[str, ...]) -> str:
    """A stable tie-break, biased towards the source that needs least setup."""
    head = route[0]
    position = (
        SOURCE_PREFERENCE.index(head) if head in SOURCE_PREFERENCE
        else len(SOURCE_PREFERENCE)
    )
    return f"{position:02d}:{' | '.join(route)}"


def _has_interior_filter(route: tuple[str, ...], verbs: VerbRegistry) -> bool:
    """Whether a shaping verb sits between two substantive ones.

    `discover | count | link` reaches the same place as `discover | link` and adds
    nothing, so it should never be chosen over it or offered as an alternative.
    """
    for name in route[1:-1]:
        verb = verbs.get(name)
        if verb is not None and verb.group == "shaping":
            return True
    return False


def _arguments(
    verb: str, intent: Intent, verbs: VerbRegistry,
) -> dict[str, Any]:
    """Fill in what the question already told us. Never invents a value."""
    declared = verbs.require(verb)
    found: dict[str, Any] = {}

    if verb == "discover" and intent.path:
        found["path"] = intent.path
    if verb == "search" and intent.subject:
        found["query"] = intent.subject
    if verb == "findings" and intent.severity:
        found["severity"] = intent.severity

    # `filter` is deliberately never filled in. Injecting `--contains <subject>`
    # from a loosely-extracted noun produced plans like
    # `... | constraints | filter --contains actually` — the planner narrowing an
    # answer on a word that was never a package name. Filtering is a refinement
    # the operator asks for, not one the planner guesses at.
    return found


def _because(
    verb: str, intent: Intent, verbs: VerbRegistry, *, first: bool,
) -> str:
    """Why this stage is in the plan. The summary, plus what the question added."""
    declared = verbs.require(verb)
    base = declared.summary

    if first and verb == "discover":
        return f"{base}" + (f" (under {intent.path})" if intent.path else "")
    if verb == "search" and intent.subject:
        return f"find {intent.subject} in the graph"
    if verb == "findings" and intent.severity:
        return f"{base}, kept to {intent.severity}"
    if verb == "explain":
        return "show the evidence behind every hop, because you asked why"
    return base


def _suggestions(verbs: VerbRegistry) -> tuple[str, ...]:
    """Concrete options for a clarification, drawn from the cookbook.

    Drawn from the recipes rather than composed on the spot, because a
    clarification's options have to be things that definitely work — the reader is
    being asked to pick one, and offering a broken option wastes their trust.
    """
    from ..manual.cookbook import offline

    return tuple(recipe.pipeline for recipe in offline()[:4])
