"""Templates, and choosing one — so a reader gets a screen, not a table dump.

The same numbers serve very different readers. A release manager wants to know
whether anything blocks; an architect wants the shape of the estate; a security
lead wants what is critical and where it reaches. Handing all three the same
grid is how a platform gets called "powerful" and goes unused.

So a template is declared data — which blocks, in which order, reading which
measures — and one is *selected* from the demand rather than chosen by the
reader from a menu they have to understand first.

── The three axes, and why three ────────────────────────────────────────

A template declares what it is for on three independent axes, because they vary
independently and a single "type" would collapse them:

    utility   what the reader is doing — monitor · investigate · report ·
              compare · explore
    context   where they are — console · dashboard · document · mobile · api
    domain    what it is about — security · dependencies · architecture ·
              operations · cost · quality

`monitor` in a `console` about `security` and `report` in a `document` about
`security` share a subject and share almost nothing else: one is a live board
that must fit a glance, the other is a paginated artefact somebody prints. A
single axis would have forced those into one template or two unrelated ones.

── Selection explains itself, and declines to guess ─────────────────────

`select()` scores every template against the demand and returns the winner
*with its reasoning* — which axes matched, which did not. Two consequences that
are not optional:

* **A tie is not resolved silently.** Equal scores return the first by name and
  say the score was tied, because an arbitrary winner presented as a decision
  is the kind of thing nobody notices until it is wrong.
* **A weak best match is reported as weak.** Below a floor, `Selection.confident`
  is false and the caller is expected to say so rather than dress a generic grid
  up as the right answer for the question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: What the reader is trying to do. Ordered loosely from "glance" to "study",
#: which is also roughly how much room each needs.
UTILITIES = ("monitor", "investigate", "compare", "report", "explore")

#: Where they are reading it. `api` is here because a machine consumer is a
#: reader too, and the honest template for one is the data with no chrome.
CONTEXTS = ("console", "dashboard", "document", "mobile", "api")

#: What it is about.
DOMAINS = ("security", "dependencies", "architecture", "operations", "cost", "quality")

#: How much of the score each axis is worth. Domain is heaviest because a
#: security board shown to somebody asking about cost is wrong in a way that a
#: slightly mis-sized layout is not — the subject has to match before anything
#: else matters.
WEIGHTS = {"domain": 0.5, "utility": 0.3, "context": 0.2}

#: Below this, the best match is not good enough to present as *the* answer.
#: Set at the value where a template matched on domain alone would fail: 0.5 is
#: exactly the domain weight, so "right subject, wrong everything else" is
#: reported as unconfident rather than served silently.
FLOOR = 0.55

#: The components a panel may name. This is the browser's addressable set —
#: `COMPONENTS` in `slpie/ui/contract.py`, which is itself pinned to the keys of
#: `app/components/dictionary.js` in both directions. It is restated here rather
#: than imported because `contract` reaches the verb registry, which reaches
#: these templates, and a cycle would be the price of one shared constant.
#: `test_the_panel_vocabulary_is_the_browsers` asserts the two agree, so the
#: restatement cannot drift.
#:
#: Deliberately not every component the browser has: what is addressable is what
#: *data alone* can drive. A diagram needs a node-and-edge payload that a star
#: schema's rows do not carry, so an architecture template lays out counts and
#: links to the graph screen rather than pretending it can draw one.
COMPONENTS = frozenset({
    "auto", "grid", "table", "metrics", "runner", "prose", "stat", "bars",
})


@dataclass(frozen=True, slots=True)
class Panel:
    """One block on a template: a component, and what it reads.

    `component` is a key in the browser's component dictionary, so a template
    composes the same vocabulary an authored screen does — a template is a
    *layout of existing components*, never a new rendering path.
    """

    component: str
    title: str = ""
    #: The star this panel reads from.
    star: str = ""
    #: Measures by name, resolved through `warehouse.measures`.
    measures: tuple[str, ...] = ()
    #: The dimension to group by, when the component shows a breakdown.
    by: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Refused at construction, exactly as `contract.Block` refuses one, and
        # for the same reason: a template is imported at start-up, so a name the
        # browser cannot resolve fails the process rather than rendering an
        # apologetic sentence where a panel should be.
        if self.component not in COMPONENTS:
            raise ValueError(
                f"{self.component!r} is not an addressable component; "
                f"known components are {', '.join(sorted(COMPONENTS))}"
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"component": self.component}
        for key in ("title", "star", "by"):
            if value := getattr(self, key):
                out[key] = value
        if self.measures:
            out["measures"] = list(self.measures)
        if self.options:
            out["options"] = dict(self.options)
        return out


@dataclass(frozen=True, slots=True)
class Template:
    """A named layout, and the demand it answers."""

    key: str
    title: str
    doc: str
    utility: str
    context: str
    domain: str
    panels: tuple[Panel, ...] = ()
    #: Extra demands this template also serves, scored at a discount. A
    #: security board is a reasonable second choice for a quality question and
    #: a poor one for a cost question, and saying so beats a hard partition.
    also: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "title": self.title, "doc": self.doc,
            "utility": self.utility, "context": self.context, "domain": self.domain,
            "also": list(self.also),
            "panels": [panel.to_dict() for panel in self.panels],
        }


@dataclass(frozen=True, slots=True)
class Demand:
    """What the reader wants, as far as anything can tell.

    Every field is optional, and an unstated axis scores *neutrally* rather than
    as a mismatch. A caller who knows only the domain should not be punished for
    not inventing a context.
    """

    utility: str = ""
    context: str = ""
    domain: str = ""
    #: Free-text, when there is any — a question, a verb name, a screen title.
    about: str = ""

    def stated(self) -> tuple[str, ...]:
        return tuple(
            axis for axis in ("utility", "context", "domain") if getattr(self, axis)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "utility": self.utility, "context": self.context,
            "domain": self.domain, "about": self.about,
            "stated": list(self.stated()),
        }


@dataclass(frozen=True, slots=True)
class Selection:
    """A chosen template, and why."""

    template: Template | None
    score: float = 0.0
    matched: tuple[str, ...] = ()
    missed: tuple[str, ...] = ()
    tied_with: tuple[str, ...] = ()
    reason: str = ""

    @property
    def confident(self) -> bool:
        return self.template is not None and self.score >= FLOOR

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template.key if self.template else "",
            "score": round(self.score, 3),
            "confident": self.confident,
            "matched": list(self.matched),
            "missed": list(self.missed),
            "tied_with": list(self.tied_with),
            "reason": self.reason,
        }


#: Words that point at a domain, for a demand that arrived as a sentence.
#: fnmatch-free and deliberately small: this is a *hint* extractor, not a
#: classifier, and a long list would create the impression it understands more
#: than it does.
SIGNALS: Mapping[str, tuple[str, ...]] = {
    "security": ("cve", "vulnerab", "secret", "boundary", "breach", "exposure",
                 "advisory", "compliance"),
    "dependencies": ("depend", "package", "version", "upgrade", "lockfile",
                     "licence", "license", "sbom"),
    "architecture": ("architect", "topology", "togaf", "layer", "coupling",
                     "component", "service map"),
    "operations": ("queue", "worker", "deploy", "replica", "latency", "incident",
                   "uptime", "job"),
    "cost": ("cost", "spend", "budget", "egress", "idle", "bill"),
    "quality": ("coverage", "test", "debt", "duplicate", "complexity"),
}


def classify(about: str) -> str:
    """A domain from free text, or "" when nothing points clearly at one.

    Empty rather than a default. "It could not tell" and "it is about
    dependencies" are different answers, and a classifier that always returned
    something would make the first invisible — which is the same
    `INDETERMINATE`-never-passes-as-upheld rule §25 applies to a verdict.
    """
    lowered = about.lower()
    hits = {
        domain: sum(1 for signal in signals if signal in lowered)
        for domain, signals in SIGNALS.items()
    }
    ranked = sorted(hits.items(), key=lambda pair: (-pair[1], pair[0]))
    best, count = ranked[0]
    if count == 0:
        return ""
    # A tie is no answer either. "a cve in a package" hits security and
    # dependencies once each and is genuinely about both; picking one would
    # hide that the question spans two domains, which is usually the
    # interesting thing about it.
    if len(ranked) > 1 and ranked[1][1] == count:
        return ""
    return best


def score(template: Template, demand: Demand) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    """How well one template answers one demand. Returns `(score, matched, missed)`.

    An axis the demand did not state scores at half weight rather than zero or
    full: the template is not *wrong* for it, and it is not evidence either.
    """
    total = 0.0
    matched: list[str] = []
    missed: list[str] = []

    for axis, weight in WEIGHTS.items():
        wanted = getattr(demand, axis)
        have = getattr(template, axis)
        if not wanted:
            total += weight * 0.5
            continue
        if wanted == have:
            total += weight
            matched.append(axis)
        elif wanted in template.also:
            # A declared second-best. Two thirds, so it can beat a template
            # that matches nothing and lose to one that matches properly.
            total += weight * 0.66
            matched.append(f"{axis} (declared as also serving {wanted})")
        else:
            missed.append(f"{axis}: wanted {wanted}, this is {have}")
    return total, tuple(matched), tuple(missed)


def select(demand: Demand, templates: Sequence[Template]) -> Selection:
    """The best template for this demand, with its reasoning."""
    if not templates:
        return Selection(template=None, reason="no templates are registered")

    # Classification only fills a gap; it never overrides what the caller said.
    if not demand.domain and demand.about:
        found = classify(demand.about)
        if found:
            demand = Demand(utility=demand.utility, context=demand.context,
                            domain=found, about=demand.about)

    scored = sorted(
        ((score(item, demand), item) for item in templates),
        key=lambda pair: (-pair[0][0], pair[1].key),
    )
    (best, matched, missed), winner = scored[0]
    tied = tuple(
        item.key for (points, _, _), item in scored[1:]
        if abs(points - best) < 1e-9
    )

    reason = f"matched {', '.join(matched)}" if matched else "matched nothing stated"
    if tied:
        reason += f"; tied with {', '.join(tied)}, taken in name order"
    if best < FLOOR:
        reason += (
            f"; below the confidence floor ({best:.2f} < {FLOOR}), so this is the "
            f"closest template rather than the right one"
        )
    return Selection(
        template=winner, score=best, matched=matched, missed=missed,
        tied_with=tied, reason=reason,
    )
