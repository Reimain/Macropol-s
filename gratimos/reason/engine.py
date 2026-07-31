"""Forward and backward chaining over the same rules.

**Backward** answers a question. Given a goal — "does anything satisfy this
need?" — it works down through rule heads to find what would have to be true,
and stops the moment it has a proof. It only touches the rules that could bear
on the goal, which is why it is the right engine for a query.

**Forward** propagates a discovery. A new fact arrives — the crawler found a
package, an agent validated a capability, a release was yanked — and forward
chaining works out everything that now follows, including answers to questions
nobody has asked yet. It runs to a fixpoint, which is why it is the right engine
for background work and the wrong one for a query.

The pair is the point. Backward is what makes the platform answer; forward is
what makes it *learn* — when the loop distils a verdict, forward chaining is
what turns that one verdict into every conclusion it implies, so the next
question is already answered before it is asked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .facts import (
    INFERENCE_CEILING,
    Fact,
    FactBase,
    Pattern,
    Provenance,
    ReasoningError,
    combine_confidence,
    is_variable,
)
from .rules import Rule, RuleSet

#: Backward chaining recursion limit. A rule set with a cycle the guards miss
#: would otherwise recurse until the stack goes.
MAX_DEPTH = 12

#: Forward chaining iteration limit. A fixpoint is normal; this catches the
#: pathological case where a rule keeps deriving marginally different facts.
MAX_PASSES = 24


@dataclass(frozen=True, slots=True)
class Derivation:
    """How one conclusion was reached — the audit trail for an inference."""

    fact: Fact
    rule: str
    premises: tuple[Fact, ...] = ()
    depth: int = 0

    @property
    def confidence(self) -> float:
        return self.fact.confidence

    def render(self, *, indent: int = 0) -> str:
        pad = "  " * indent
        lines = [f"{pad}{self.fact} ({self.fact.confidence:.2f} via {self.rule})"]
        for premise in self.premises:
            lines.append(f"{pad}  ← {premise} ({premise.confidence:.2f})")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact.to_dict(), "rule": self.rule, "depth": self.depth,
            "premises": [premise.to_dict() for premise in self.premises],
        }


@dataclass(frozen=True, slots=True)
class Proof:
    """The answer to a backward query, with everything that supports it."""

    goal: Pattern
    bindings: tuple[Mapping[str, str], ...] = ()
    derivations: tuple[Derivation, ...] = ()
    confidence: float = 0.0
    exhausted: bool = False

    @property
    def proven(self) -> bool:
        return bool(self.bindings)

    @property
    def answers(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(binding) for binding in self.bindings)

    def values_of(self, variable: str) -> tuple[str, ...]:
        """What the query bound one variable to, in order and deduplicated."""
        return tuple(dict.fromkeys(
            binding[variable] for binding in self.bindings if variable in binding
        ))

    def render(self) -> str:
        if not self.proven:
            reason = "search exhausted" if self.exhausted else "no supporting facts"
            return f"{self.goal}: not proven ({reason})"
        lines = [f"{self.goal}: proven, confidence {self.confidence:.2f}"]
        lines.extend(derivation.render(indent=1) for derivation in self.derivations)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": str(self.goal), "proven": self.proven,
            "confidence": round(self.confidence, 4),
            "answers": [dict(binding) for binding in self.bindings],
            "derivations": [derivation.to_dict() for derivation in self.derivations],
            "exhausted": self.exhausted,
        }

    def __bool__(self) -> bool:
        return self.proven


@dataclass(slots=True)
class ForwardReport:
    """What a forward pass worked out, and what it cost."""

    derived: tuple[Fact, ...] = ()
    passes: int = 0
    fired: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0
    saturated: bool = False

    @property
    def count(self) -> int:
        return len(self.derived)

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived": len(self.derived), "passes": self.passes,
            "fired": dict(self.fired), "duration": round(self.duration, 4),
            "saturated": self.saturated,
            "facts": [fact.to_dict() for fact in self.derived],
        }

    def __str__(self) -> str:
        state = " (hit the pass limit)" if self.saturated else ""
        return f"{len(self.derived)} facts in {self.passes} passes{state}"


class Engine:
    """Reasons over a fact base with a rule set, in either direction."""

    def __init__(
        self,
        facts: FactBase | None = None,
        rules: RuleSet | None = None,
        *,
        min_confidence: float = 0.1,
    ) -> None:
        self.facts = facts if facts is not None else FactBase()
        self.rules = rules if rules is not None else RuleSet()
        self.min_confidence = min_confidence

    # -- backward: prove a goal ------------------------------------------

    def prove(self, goal: Pattern, *, max_depth: int = MAX_DEPTH) -> Proof:
        """Can this goal be established, and how?

        Explores rules only where they could bear on the goal, so an
        irrelevant rule set costs nothing. Returns every distinct binding,
        because "which packages satisfy this need" wants all of them.
        """
        derivations: list[Derivation] = []
        bindings: list[Mapping[str, str]] = []
        strengths: list[float] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        exhausted = False

        for binding, confidence, trail in self._prove(
            goal, bindings={}, depth=0, max_depth=max_depth, guard=frozenset(),
        ):
            key = tuple(sorted(binding.items()))
            if key in seen:
                continue
            seen.add(key)
            bindings.append(binding)
            derivations.extend(trail)
            # The confidence of the route that proved it. A goal satisfied from
            # memory carries that fact's confidence — reporting 1.0 because no
            # rule had to fire would make a remembered guess look certain.
            strengths.append(confidence)

        if not bindings and self._would_recurse(goal, max_depth):
            exhausted = True

        return Proof(
            goal=goal, bindings=tuple(bindings), derivations=tuple(derivations),
            confidence=round(max(strengths), 4) if strengths else 0.0,
            exhausted=exhausted,
        )

    def _prove(
        self,
        goal: Pattern,
        *,
        bindings: Mapping[str, str],
        depth: int,
        max_depth: int,
        guard: frozenset[str],
    ) -> Iterator[tuple[dict[str, str], float, list[Derivation]]]:
        """Yield every way the goal can be established under these bindings."""
        if depth > max_depth:
            return

        bound = goal.bind(bindings)

        # A fact already in memory is the cheapest proof there is.
        for fact in self.facts.match(bound):
            extended = bound.match(fact, bindings)
            if extended is not None:
                yield extended, fact.confidence, []

        # Then rules whose head could produce it.
        signature = f"{bound.subject}|{bound.predicate}|{bound.object}"
        if signature in guard:
            # Already proving this exact goal higher up the stack. Continuing
            # would be a loop, and a loop that yields nothing is the correct
            # answer for a goal that only supports itself.
            return
        inner_guard = guard | {signature}

        for rule in self.rules.concluding(bound.predicate):
            head_bindings = rule.then.match(_as_fact(bound), {}) if bound.is_ground else None
            if bound.is_ground and head_bindings is None:
                continue

            start = head_bindings if head_bindings is not None else _partial(rule.then, bound)
            if start is None:
                continue

            yield from self._prove_antecedents(
                rule,
                index=0,
                bindings=start,
                outer=bindings,
                goal=bound,
                depth=depth,
                max_depth=max_depth,
                guard=inner_guard,
            )

    def _prove_antecedents(
        self,
        rule: Rule,
        *,
        index: int,
        bindings: dict[str, str],
        outer: Mapping[str, str],
        goal: Pattern,
        depth: int,
        max_depth: int,
        guard: frozenset[str],
    ) -> Iterator[tuple[dict[str, str], float, list[Derivation]]]:
        """Prove a rule's antecedents left to right, threading the bindings."""
        if index >= len(rule.when):
            if rule.guard is not None and not rule.guard(bindings, self.facts):
                return

            premises = self._premises(rule, bindings)
            confidence = combine_confidence(
                [premise.confidence for premise in premises] or [1.0],
                factor=rule.strength,
            )
            if confidence < self.min_confidence:
                return

            try:
                conclusion = rule.then.as_fact(
                    bindings,
                    provenance=rule.provenance,
                    confidence=confidence,
                    derived_from=tuple(premise.id for premise in premises),
                    rule=rule.name,
                )
            except ReasoningError:
                return

            merged = dict(outer)
            for variable in goal.variables:
                if variable in bindings:
                    merged[variable] = bindings[variable]
            # A goal variable the rule never bound leaves the answer partial,
            # which is not an answer.
            if any(variable not in merged for variable in goal.variables):
                return

            yield merged, confidence, [
                Derivation(
                    fact=conclusion, rule=rule.name,
                    premises=tuple(premises), depth=depth,
                )
            ]
            return

        antecedent = rule.when[index]
        for sub_bindings, _confidence, trail in self._prove(
            antecedent,
            bindings=bindings,
            depth=depth + 1,
            max_depth=max_depth,
            guard=guard,
        ):
            merged = {**bindings, **sub_bindings}
            for answer, confidence, rest in self._prove_antecedents(
                rule,
                index=index + 1,
                bindings=merged,
                outer=outer,
                goal=goal,
                depth=depth,
                max_depth=max_depth,
                guard=guard,
            ):
                yield answer, confidence, trail + rest

    def _premises(self, rule: Rule, bindings: Mapping[str, str]) -> list[Fact]:
        """The facts standing behind a firing, for the derivation trail."""
        found: list[Fact] = []
        for pattern in rule.when:
            bound = pattern.bind(bindings)
            if not bound.is_ground:
                continue
            matches = self.facts.match(bound)
            if matches:
                found.append(max(matches, key=lambda fact: fact.confidence))
        return found

    def _would_recurse(self, goal: Pattern, max_depth: int) -> bool:
        """Whether a rule could have proven this goal but the search ran out."""
        return bool(self.rules.concluding(goal.predicate)) and max_depth > 0

    def holds(self, subject: str, predicate: str, object: str = "") -> bool:
        """Convenience: is this true, by memory or by inference?"""
        return self.prove(Pattern(subject, predicate, object)).proven

    def ask(self, subject: str, predicate: str, object: str = "") -> Proof:
        return self.prove(Pattern(subject, predicate, object))

    # -- forward: propagate what is now known -----------------------------

    def saturate(self, *, max_passes: int = MAX_PASSES) -> ForwardReport:
        """Derive everything that follows, to a fixpoint.

        This is the background half. It is deliberately run when new knowledge
        arrives rather than when a question is asked — the whole value is that
        the conclusions are already sitting there by the time somebody wants
        them.
        """
        started = time.monotonic()
        derived: list[Fact] = []
        fired: dict[str, int] = {}
        passes = 0

        while passes < max_passes:
            passes += 1
            new_this_pass: list[Fact] = []

            for rule in self.rules:
                for bindings in self._match_antecedents(rule):
                    if rule.guard is not None and not rule.guard(bindings, self.facts):
                        continue

                    premises = self._premises(rule, bindings)
                    confidence = combine_confidence(
                        [premise.confidence for premise in premises] or [1.0],
                        factor=rule.strength,
                    )
                    if confidence < self.min_confidence:
                        continue

                    try:
                        conclusion = rule.then.as_fact(
                            bindings,
                            provenance=rule.provenance,
                            confidence=confidence,
                            derived_from=tuple(premise.id for premise in premises),
                            rule=rule.name,
                        )
                    except ReasoningError:
                        continue

                    existing = self.facts.get(conclusion.id)
                    if existing is not None and not conclusion.stronger_than(existing):
                        continue

                    self.facts.assert_fact(conclusion)
                    new_this_pass.append(conclusion)
                    fired[rule.name] = fired.get(rule.name, 0) + 1

            derived.extend(new_this_pass)
            if not new_this_pass:
                # Fixpoint. Nothing further follows from what is known.
                return ForwardReport(
                    derived=tuple(derived), passes=passes, fired=fired,
                    duration=time.monotonic() - started, saturated=False,
                )

        return ForwardReport(
            derived=tuple(derived), passes=passes, fired=fired,
            duration=time.monotonic() - started, saturated=True,
        )

    def _match_antecedents(self, rule: Rule) -> Iterator[dict[str, str]]:
        """Every consistent binding of a rule's antecedents against memory."""
        def walk(index: int, bindings: dict[str, str]) -> Iterator[dict[str, str]]:
            if index >= len(rule.when):
                yield bindings
                return
            pattern = rule.when[index].bind(bindings)
            for fact in self.facts.match(pattern):
                extended = rule.when[index].match(fact, bindings)
                if extended is not None:
                    yield from walk(index + 1, extended)

        yield from walk(0, {})

    def learn(self, *facts: Fact, propagate: bool = True) -> ForwardReport:
        """Add facts and work out what now follows.

        The default is to propagate, because a fact added without propagation
        leaves the fact base holding a claim whose consequences nobody has
        drawn — which is exactly the state where backward chaining starts
        answering questions it should already know.
        """
        self.facts.extend(facts)
        return self.saturate() if propagate else ForwardReport()

    # -- reporting -------------------------------------------------------

    def explain(self, fact: Fact | str) -> tuple[Fact, ...]:
        return self.facts.explain(fact if isinstance(fact, str) else fact.id)

    def status(self) -> dict[str, Any]:
        inferred = [f for f in self.facts if f.provenance.is_inferred]
        return {
            "facts": len(self.facts),
            "rules": len(self.rules),
            "inferred": len(inferred),
            "observed": len(self.facts) - len(inferred),
            "ungrounded": len(self.facts.ungrounded()),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Engine {len(self.facts)} facts, {len(self.rules)} rules>"


def _as_fact(pattern: Pattern) -> Fact:
    """A ground pattern viewed as a fact, for head unification."""
    return Fact(
        subject=pattern.subject, predicate=pattern.predicate, object=pattern.object,
        provenance=Provenance.ASSUMED,
    )


def _partial(head: Pattern, goal: Pattern) -> dict[str, str] | None:
    """Bind a rule head against a goal that still has variables in it.

    A variable on the goal side constrains nothing — it is what we are asking
    about — so it binds only where the head is concrete.
    """
    bindings: dict[str, str] = {}
    for head_term, goal_term in zip(
        (head.subject, head.predicate, head.object),
        (goal.subject, goal.predicate, goal.object),
    ):
        if is_variable(head_term):
            if not is_variable(goal_term) and goal_term:
                existing = bindings.get(head_term)
                if existing is not None and existing != goal_term:
                    return None
                bindings[head_term] = goal_term
        elif not is_variable(goal_term) and goal_term and head_term != goal_term:
            return None
    return bindings
