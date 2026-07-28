"""Rules — how the engine gets from what it knows to what follows.

A rule is antecedents implying a consequent, plus a strength. Strength matters
because not every implication is equally safe: "a package that declares a
licence has that licence" is near-certain, while "a package named `*-retry`
probably provides retry" is a naming heuristic and must never produce a
confident conclusion however many times it fires.

The same rule set drives both directions. Forward chaining matches antecedents
against known facts to derive consequents; backward chaining matches a goal
against consequents to discover what would have to be true. Writing the rules
once for both is the point — two rule sets would drift, and the drift would be
invisible until the two directions disagreed about the same question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .facts import Fact, FactBase, Pattern, Provenance, ReasoningError, is_variable

#: A guard sees the candidate bindings and vetoes the firing. Used for the
#: conditions that are awkward as triples — version comparisons, thresholds.
Guard = Callable[[Mapping[str, str], FactBase], bool]


@dataclass(frozen=True, slots=True)
class Rule:
    """``when all of these hold, then that follows``."""

    name: str
    when: tuple[Pattern, ...]
    then: Pattern
    strength: float = 1.0
    guard: Guard | None = None
    description: str = ""
    provenance: Provenance = Provenance.DERIVED

    def __post_init__(self) -> None:
        if not self.name:
            raise ReasoningError("a rule must be named")
        if not self.when:
            raise ReasoningError(f"rule {self.name!r} has no antecedents")
        if not 0.0 < self.strength <= 1.0:
            raise ReasoningError(
                f"rule {self.name!r} has strength {self.strength}; expected (0, 1]"
            )

        unbound = set(self.then.variables) - {
            variable for pattern in self.when for variable in pattern.variables
        }
        if unbound:
            # A head variable no antecedent binds cannot be grounded, so the
            # rule would raise every time it fired. Better to refuse it here,
            # where the author is looking, than at 3am in a crawl.
            raise ReasoningError(
                f"rule {self.name!r} concludes about unbound {', '.join(sorted(unbound))}"
            )

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(
            variable
            for pattern in (*self.when, self.then)
            for variable in pattern.variables
        )

    @property
    def heuristic(self) -> bool:
        """Rules weak enough that their conclusions must stay tentative."""
        return self.strength < 0.6

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "when": [str(pattern) for pattern in self.when],
            "then": str(self.then),
            "strength": self.strength,
            "heuristic": self.heuristic,
            "description": self.description,
        }

    def __str__(self) -> str:
        antecedents = " AND ".join(str(pattern) for pattern in self.when)
        return f"{self.name}: {antecedents} -> {self.then}"


class RuleSet:
    """The rules, indexed by what they conclude about.

    Backward chaining asks "which rules could prove this goal?" on every step,
    so the predicate index is the difference between a search that terminates
    and one that walks every rule at every depth.
    """

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._rules: dict[str, Rule] = {}
        self._by_conclusion: dict[str, list[str]] = {}
        for rule in rules:
            self.add(rule)

    def add(self, rule: Rule) -> Rule:
        if rule.name in self._rules:
            raise ReasoningError(f"rule {rule.name!r} is already defined")
        self._rules[rule.name] = rule
        self._by_conclusion.setdefault(rule.then.predicate, []).append(rule.name)
        return rule

    def define(
        self,
        name: str,
        when: Sequence[tuple[str, ...]],
        then: tuple[str, ...],
        **attributes: Any,
    ) -> Rule:
        """Terser `add`, for writing rules as tuples."""
        return self.add(Rule(
            name=name,
            when=tuple(Pattern(*parts) for parts in when),
            then=Pattern(*then),
            **attributes,
        ))

    def concluding(self, predicate: str) -> tuple[Rule, ...]:
        """Rules whose head could prove a goal with this predicate.

        A rule whose head predicate is itself a variable could prove anything,
        so it is always a candidate.
        """
        names = list(self._by_conclusion.get(predicate, ()))
        names.extend(
            name for name, rule in self._rules.items()
            if is_variable(rule.then.predicate)
        )
        return tuple(self._rules[name] for name in dict.fromkeys(names))

    def get(self, name: str) -> Rule | None:
        return self._rules.get(name)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules.values())

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self._rules.values()]}

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules.values())

    def __contains__(self, name: object) -> bool:
        return name in self._rules

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<RuleSet {len(self._rules)} rules>"


def reuse_rules(ontology: Any = None) -> RuleSet:
    """The rules that turn crawled facts into reuse conclusions.

    Strengths are the whole design. `declares-licence → has-licence` is
    near-certain because it is a reading, not an inference. `named-like-X →
    provides-X` is 0.35 because it is a guess, and a conclusion drawn only from
    guesses must never look like a fact — the same discipline as SLPIE's
    heuristic ceiling, expressed as rule strength instead of an evidence kind.
    """
    rules = RuleSet()

    # --- reading, not inferring: these are near-certain -------------------
    rules.define(
        "licence-from-declaration",
        when=[("?pkg", "declares-licence", "?licence")],
        then=("?pkg", "has-licence", "?licence"),
        strength=0.98,
        description="a declared licence is the licence, absent evidence otherwise",
    )
    rules.define(
        "capability-from-validation",
        when=[("?pkg", "validated-provides", "?concept")],
        then=("?pkg", "provides", "?concept"),
        strength=0.97,
        provenance=Provenance.VALIDATED,
        description="an agent checked this and agreed",
    )

    # --- subsumption: the lattice made available to the engine ------------
    rules.define(
        "provides-broader",
        when=[("?pkg", "provides", "?narrow"), ("?narrow", "is-a", "?broad")],
        then=("?pkg", "provides", "?broad"),
        strength=0.95,
        description="providing the specific concept provides the general one",
    )
    rules.define(
        "is-a-transitive",
        when=[("?a", "is-a", "?b"), ("?b", "is-a", "?c")],
        then=("?a", "is-a", "?c"),
        strength=0.99,
        description="subsumption is transitive",
    )

    # --- structural reuse -------------------------------------------------
    rules.define(
        "reuse-candidate",
        when=[
            ("?pkg", "provides", "?concept"),
            ("?need", "requires", "?concept"),
            ("?pkg", "licence-compatible-with", "?need"),
        ],
        then=("?pkg", "satisfies", "?need"),
        strength=0.90,
        description="provides what is required, under a licence we can accept",
    )
    rules.define(
        "transitive-dependency",
        when=[("?a", "depends-on", "?b"), ("?b", "depends-on", "?c")],
        then=("?a", "depends-on-transitively", "?c"),
        strength=0.95,
    )

    # --- health, which decides whether a candidate is worth offering ------
    rules.define(
        "maintained-implies-viable",
        when=[("?pkg", "released-within", "year"), ("?pkg", "has-licence", "?licence")],
        then=("?pkg", "viable", "true"),
        strength=0.85,
        description="recently released and licensed is the bar for being offered",
    )
    rules.define(
        "archived-implies-unmaintained",
        when=[("?pkg", "archived", "true")],
        then=("?pkg", "unmaintained", "true"),
        strength=0.96,
    )
    rules.define(
        "deprecated-blocks-reuse",
        when=[("?pkg", "deprecated", "true")],
        then=("?pkg", "blocked", "deprecated"),
        strength=0.95,
    )
    rules.define(
        "unmaintained-blocks-reuse",
        when=[("?pkg", "unmaintained", "true")],
        then=("?pkg", "blocked", "unmaintained"),
        strength=0.80,
    )

    # --- heuristics: deliberately weak ------------------------------------
    rules.define(
        "name-suggests-capability",
        when=[("?pkg", "named-like", "?concept")],
        then=("?pkg", "provides", "?concept"),
        strength=0.35,
        description="a naming coincidence is a hint, never a finding",
    )
    rules.define(
        "popularity-suggests-viability",
        when=[("?pkg", "widely-used", "true")],
        then=("?pkg", "viable", "true"),
        strength=0.55,
        description="popular is not the same as suitable, or as maintained",
    )

    return rules
