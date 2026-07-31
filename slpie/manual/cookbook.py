"""The recipes — where composition is taught rather than asserted.

A verb list tells you what exists. It does not tell you that the interesting
answers come from putting verbs together, and that is the thing worth teaching.
So the cookbook is a curated set of real pipelines, each with the reason it is
shaped the way it is.

**Every recipe is executed by the test suite.** `tests/test_slpie_manual.py` runs
each one and asserts it produces the kind it claims. A documented composition that
does not run is worse than an undocumented one: it teaches the wrong thing and
costs the reader their trust in the rest of the document.

Recipes carry `needs_environment` because roughly half the platform works with no
manifest and no database at all, and a reader deserves to know which half they are
looking at before they try it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Sequence

from ..compose.flow import Kind


@dataclass(frozen=True, slots=True)
class Recipe:
    """One composition worth knowing, and why it is shaped that way."""

    pipeline: str
    intent: str                       # what a person wanted to know
    produces: Kind
    why: str = ""                     # why *this* shape rather than another
    group: str = "general"
    needs_environment: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline, "intent": self.intent,
            "produces": self.produces.value, "why": self.why,
            "group": self.group, "needs_environment": self.needs_environment,
        }

    def __str__(self) -> str:
        return f"{self.pipeline}    # {self.intent}"


#: The primer. Four sentences, because the mechanism is genuinely simple and
#: explaining it at length would suggest otherwise.
PRIMER = """\
Verbs pipe into each other like shell commands: `slpie 'discover . | link | findings'`.

Three things differ from a shell pipe, and each one is why this is worth doing:

  1. The pipe carries provenance, not bytes. Every stage's reasoning and gaps
     travel with the value, so `| explain` at the end of any pipeline shows what
     was read, what was concluded, and what limited the answer.

  2. Pipes are typed. Each verb declares what it consumes and produces, so an
     impossible composition is refused before anything runs -- and `slpie help
     <verb>` can tell you exactly what may follow it.

  3. The filters are polymorphic. `head`, `sort`, `filter`, `unique`, `count` and
     `explain` accept anything and pass its kind through, which is what turns a
     list of verbs into a combinatorial space.

Start with a source verb (`discover`, `scan`, `graph`, `search`, ...), then refine.
`slpie help compose` shows the recipes; `slpie plan "<question>"` writes one for you.
"""


def recipes() -> tuple[Recipe, ...]:
    """Every curated composition. Ordered so the simplest teach first."""
    return (
        # -- works with nothing installed but the package ------------------
        Recipe(
            pipeline="discover .",
            intent="what is in this tree at all?",
            produces=Kind.OBSERVATIONS,
            why=(
                "the one-verb starting point: no manifest, no database, no "
                "network. Everything below refines this."
            ),
            group="getting started",
        ),
        Recipe(
            pipeline="discover . | link",
            intent="which of those observations are the same thing?",
            produces=Kind.RESOLUTION,
            why=(
                "twenty-nine discoverers describe overlapping things. `link` "
                "merges them on identity -- never on similarity -- so the graph "
                "holds one node per package with all the evidence attached."
            ),
            group="getting started",
        ),
        Recipe(
            pipeline="discover . | link | findings",
            intent="what is actually wrong here?",
            produces=Kind.FINDINGS,
            why=(
                "contradictions only exist once observations have been merged, "
                "which is why `findings` follows `link` rather than `discover`."
            ),
            group="getting started",
        ),
        Recipe(
            pipeline="discover . | link | findings --severity high | explain",
            intent="what must I fix before release, and how do you know?",
            produces=Kind.FINDINGS,
            why=(
                "the payoff for a provenance-carrying pipe: `explain` renders "
                "every file and line behind the findings, four stages back."
            ),
            group="getting started",
        ),

        # -- dependency intelligence ---------------------------------------
        Recipe(
            pipeline="discover . | link | constraints",
            intent="do these dependency ranges actually resolve?",
            produces=Kind.SOLUTION,
            why=(
                "resolves against versions that were *observed*, never a network "
                "lookup. An unsatisfiable result names the conflicting pair and "
                "the window each one demands."
            ),
            group="dependencies",
        ),
        Recipe(
            pipeline="discover . | link | constraints | findings --severity high",
            intent="which version conflicts block a release?",
            produces=Kind.FINDINGS,
            why=(
                "`findings` is polymorphic, so the same verb ranks conflicts from "
                "the solver and contradictions from the linker in one list. An "
                "operator triaging a release wants one list, not two views."
            ),
            group="dependencies",
        ),
        Recipe(
            pipeline="discover . | link | findings | sort --field severity --desc | head --count 5",
            intent="the five worst things, worst first",
            produces=Kind.FINDINGS,
            why="`sort` and `head` are the shell filters, unchanged in meaning.",
            group="dependencies",
        ),
        Recipe(
            pipeline="discover . | filter --field kind --equals depends_on | count",
            intent="how many dependency relationships are declared?",
            produces=Kind.OBSERVATIONS,
            why=(
                "`filter` works on raw observations because it is polymorphic; "
                "no dedicated 'count dependencies' verb has to exist."
            ),
            group="dependencies",
        ),

        # -- reasoning ------------------------------------------------------
        Recipe(
            pipeline="discover . | reason",
            intent="what can be concluded beyond what was literally read?",
            produces=Kind.ENRICHMENTS,
            why=(
                "runs the layered pipeline. Each layer appends and never mutates "
                "a prior one, so every conclusion walks back to a file and a line."
            ),
            group="reasoning",
        ),
        Recipe(
            pipeline="discover . | reason | explain",
            intent="show me the reasoning, not just the answer",
            produces=Kind.ENRICHMENTS,
            why=(
                "the layer abstentions travel out as gaps, so this states what "
                "the platform could *not* conclude alongside what it could."
            ),
            group="reasoning",
        ),

        # -- intelligence ----------------------------------------------------
        Recipe(
            pipeline="discover . | reason | ask",
            intent="just tell me what you found, and what you could not see",
            produces=Kind.GUIDANCE,
            why=(
                "never a bare value. The answer arrives with the reasoning that "
                "produced it, the gaps that limit it, and the questions worth "
                "asking next -- each one a composition that will actually run."
            ),
            group="intelligence",
        ),
        Recipe(
            pipeline="discover . | reason | radius --min_size 2",
            intent="what is depended on by more than one thing here?",
            produces=Kind.IMPACT,
            why=(
                "blast radius on a bare checkout, with no database. Confidence "
                "propagates as a minimum, so a radius reached only through a "
                "dynamic load is reported as the weak thing it is."
            ),
            group="intelligence",
        ),
        Recipe(
            pipeline="discover . | reason | options --safe",
            intent="what can I upgrade right now without breaking anything?",
            produces=Kind.REPORT,
            why=(
                "enumerates, never recommends. `--safe` keeps only the moves "
                "every declared range still admits; drop it and the breaking "
                "options appear too, each saying who it breaks."
            ),
            group="intelligence",
        ),
        Recipe(
            pipeline="discover . | reason | ask | suggest",
            intent="I do not know what to do with this answer",
            produces=Kind.GUIDANCE,
            why=(
                "the deterministic answer, then the non-deterministic part: "
                "candidates come from the type graph so a suggestion is never "
                "something the platform cannot do, and only their order is "
                "learned from what you have accepted before."
            ),
            group="intelligence",
        ),

        # -- governance ------------------------------------------------------
        Recipe(
            pipeline="discover . | govern",
            intent="what would stop this from shipping?",
            produces=Kind.FINDINGS,
            why=(
                "fourteen rules across five families. A rule missing the data it "
                "needs declines rather than guessing, and the count of declined "
                "rules travels out as a gap -- so a short list never quietly "
                "means nothing was checked."
            ),
            group="governance",
        ),
        Recipe(
            pipeline="discover . | govern --severity critical | explain",
            intent="show me only what blocks a release, and prove each one",
            produces=Kind.FINDINGS,
            why=(
                "exits 3 when anything is found at that severity, so it works as "
                "a CI gate; `explain` renders the file and line behind every "
                "finding, which is what makes the gate arguable rather than "
                "merely obstructive."
            ),
            group="governance",
        ),
        Recipe(
            pipeline="rules --tag license",
            intent="what does this build actually check, and has it changed?",
            produces=Kind.REPORT,
            why=(
                "each rule carries a digest of its own logic, so a finding raised "
                "last month can be checked against the rule as it stands today: "
                "if the digest moved, the question moved."
            ),
            group="governance",
        ),

        # -- needs an environment -------------------------------------------
        Recipe(
            pipeline="declare",
            intent="what does the manifest say exists, before reading any file?",
            produces=Kind.ELEMENTS,
            why=(
                "declare-first: the skeleton graph exists in milliseconds because "
                "a manifest is a declaration of names, not a search."
            ),
            group="environment",
            needs_environment=True,
        ),
        Recipe(
            pipeline="reconcile",
            intent="where does what I declared disagree with what is really there?",
            produces=Kind.FINDINGS,
            why=(
                "the delta is first-class intelligence: declared-but-absent and "
                "observed-but-undeclared are both findings, with evidence."
            ),
            group="environment",
            needs_environment=True,
        ),
        Recipe(
            pipeline="search redis | impact | explain",
            intent="what breaks if redis changes, and why do you believe each hop?",
            produces=Kind.IMPACT,
            why=(
                "the shell shape: one verb finds, the next acts on what was found. "
                "`impact` consumes NODES, so anything producing nodes can feed it."
            ),
            group="environment",
            needs_environment=True,
        ),
        Recipe(
            pipeline="scan | link | findings --severity high",
            intent="the release gate, over every attached element",
            produces=Kind.FINDINGS,
            why=(
                "`scan` reads through the station's bindings, so refused "
                "capabilities become gaps that travel to the findings."
            ),
            group="environment",
            needs_environment=True,
        ),
        Recipe(
            pipeline="gaps | explain",
            intent="what can this platform not see right now?",
            produces=Kind.GAPS,
            why=(
                "the honest question. A low-confidence answer and a misleading one "
                "differ only in whether this was asked."
            ),
            group="environment",
            needs_environment=True,
        ),
    )


def offline() -> tuple[Recipe, ...]:
    """The recipes that work with no manifest and no database."""
    return tuple(item for item in recipes() if not item.needs_environment)


def groups() -> dict[str, tuple[Recipe, ...]]:
    found: dict[str, list[Recipe]] = {}
    for recipe in recipes():
        found.setdefault(recipe.group, []).append(recipe)
    # Insertion order, not alphabetical: the groups are ordered to teach, and
    # sorting them would put "dependencies" before "getting started".
    return {group: tuple(items) for group, items in found.items()}
