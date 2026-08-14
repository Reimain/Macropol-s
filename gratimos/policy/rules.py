"""The rule engine policymakers reason with.

A policymaker is not a heuristic buried in a method — it is a named rule with a
condition, an action, and a stated reason. That shape matters because the
kernel's decisions have to be explicable after the fact: when the system decides
to spill an entity to cold storage, regenerate a module, or block a migration,
somebody will eventually ask *why*, and "the code did that" is not an answer.

Every verdict carries the rule that produced it and the facts it saw.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..contextflow import ContextFlow, EventKind
from ..errors import PolicyError
from ..ids import new_id, now_ns, slug


@dataclass(frozen=True, slots=True)
class Situation:
    """The facts a decision is made from."""

    kind: str
    subject: str = ""
    facts: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)

    def number(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.facts.get(key, default))
        except (TypeError, ValueError):
            return default

    def flag(self, key: str, default: bool = False) -> bool:
        return bool(self.facts.get(key, default))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject, "facts": dict(self.facts)}

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Situation {self.kind}:{self.subject} facts={sorted(self.facts)}>"


@dataclass(frozen=True, slots=True)
class Verdict:
    """A decision, with its justification attached."""

    action: str
    reason: str = ""
    rule: str = ""
    confidence: float = 1.0
    params: Mapping[str, Any] = field(default_factory=dict)
    subject: str = ""
    id: str = field(default_factory=lambda: new_id("vrd"))
    ts_ns: int = field(default_factory=now_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "reason": self.reason,
            "rule": self.rule,
            "confidence": round(self.confidence, 3),
            "params": dict(self.params),
            "subject": self.subject,
        }

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.action} ({self.rule}: {self.reason})"


Condition = Callable[[Situation], bool]
Outcome = Callable[[Situation], Verdict]


@dataclass(slots=True)
class Rule:
    """A named condition-action pair."""

    name: str
    kind: str
    when: Condition
    then: str | Outcome
    reason: str = ""
    priority: int = 100
    confidence: float = 1.0
    params: Mapping[str, Any] = field(default_factory=dict)
    fired: int = 0

    def applies(self, situation: Situation) -> bool:
        if situation.kind != self.kind:
            return False
        try:
            return bool(self.when(situation))
        except Exception:  # noqa: BLE001 - a broken rule abstains, it does not crash the run
            return False

    def verdict(self, situation: Situation) -> Verdict:
        self.fired += 1
        if callable(self.then):
            produced = self.then(situation)
            return Verdict(
                action=produced.action,
                reason=produced.reason or self.reason,
                rule=self.name,
                confidence=produced.confidence,
                params=produced.params or self.params,
                subject=situation.subject,
            )
        return Verdict(
            action=self.then, reason=self.reason, rule=self.name,
            confidence=self.confidence, params=self.params, subject=situation.subject,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "priority": self.priority,
            "action": self.then if isinstance(self.then, str) else "<computed>",
            "reason": self.reason, "fired": self.fired,
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Rule {self.name} kind={self.kind} priority={self.priority}>"


class PolicyEngine:
    """Holds rules and reaches verdicts.

    First match by priority wins. Rules that abstain (raise, or return False)
    simply do not fire — a policymaker never fails a run because one of its
    rules was written badly.
    """

    def __init__(self, flow: ContextFlow | None = None, *, actor: str = "policy") -> None:
        self.flow = flow
        self.actor = actor
        self._rules: list[Rule] = []
        self._defaults: dict[str, Verdict] = {}
        self._lock = threading.RLock()
        self.history: list[tuple[Situation, Verdict]] = []

    # -- registration ----------------------------------------------------

    def add(self, rule: Rule) -> Rule:
        with self._lock:
            if any(r.name == rule.name for r in self._rules):
                raise PolicyError(f"rule {rule.name!r} is already registered")
            self._rules.append(rule)
            self._rules.sort(key=lambda r: r.priority)
        return rule

    def rule(
        self,
        name: str,
        kind: str,
        when: Condition,
        then: str | Outcome,
        *,
        reason: str = "",
        priority: int = 100,
        confidence: float = 1.0,
        **params: Any,
    ) -> Rule:
        """Register a rule in one call."""
        return self.add(Rule(
            name=name, kind=kind, when=when, then=then, reason=reason,
            priority=priority, confidence=confidence, params=params,
        ))

    def default(self, kind: str, action: str, reason: str = "no rule matched") -> Verdict:
        verdict = Verdict(action=action, reason=reason, rule=f"default:{kind}", confidence=0.5)
        self._defaults[kind] = verdict
        return verdict

    def remove(self, name: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.name != name]
            return len(self._rules) != before

    def rules(self, kind: str = "") -> list[Rule]:
        return [r for r in self._rules if not kind or r.kind == kind]

    # -- deciding --------------------------------------------------------

    def decide(self, situation: Situation, *, record: bool = True) -> Verdict:
        """Reach a verdict, recording it on the flow."""
        for rule in self._rules:
            if rule.applies(situation):
                verdict = rule.verdict(situation)
                if record:
                    self._record(situation, verdict)
                return verdict

        fallback = self._defaults.get(situation.kind)
        if fallback is None:
            raise PolicyError(
                f"no rule and no default for {situation.kind!r} on {situation.subject!r}; "
                f"register one with engine.default({situation.kind!r}, ...)"
            )
        verdict = Verdict(
            action=fallback.action, reason=fallback.reason, rule=fallback.rule,
            confidence=fallback.confidence, subject=situation.subject,
        )
        if record:
            self._record(situation, verdict)
        return verdict

    def decide_all(self, situations: Iterable[Situation]) -> list[Verdict]:
        return [self.decide(s) for s in situations]

    def explain(self, situation: Situation) -> list[dict[str, Any]]:
        """Every rule that would fire, in order — for debugging a policy."""
        return [
            {"rule": r.name, "priority": r.priority, "would_fire": r.applies(situation),
             "action": r.then if isinstance(r.then, str) else "<computed>"}
            for r in self._rules if r.kind == situation.kind
        ]

    def _record(self, situation: Situation, verdict: Verdict) -> None:
        self.history.append((situation, verdict))
        if self.flow is None:
            return
        self.flow.emit(
            EventKind.POLICY,
            actor=self.actor,
            paths=[f"policy.{slug(situation.kind)}.{slug(situation.subject or 'global')}"],
            payload={
                "kind": situation.kind,
                "subject": situation.subject,
                "action": verdict.action,
                "rule": verdict.rule,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
                "params": dict(verdict.params),
                "facts": {k: _short(v) for k, v in situation.facts.items()},
            },
        )

    # -- reporting -------------------------------------------------------

    def report(self) -> dict[str, Any]:
        by_action: dict[str, int] = {}
        for _situation, verdict in self.history:
            by_action[verdict.action] = by_action.get(verdict.action, 0) + 1
        return {
            "rules": [r.to_dict() for r in self._rules],
            "decisions": len(self.history),
            "by_action": dict(sorted(by_action.items())),
            "defaults": {k: v.action for k, v in self._defaults.items()},
        }

    def __len__(self) -> int:
        return len(self._rules)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<PolicyEngine rules={len(self._rules)} decisions={len(self.history)}>"


def _short(value: Any, limit: int = 200) -> Any:
    """Keep recorded facts small — a policy event is an audit record, not a dump."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
