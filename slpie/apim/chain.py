"""Mediation: priority-ordered `Situation → Verdict`, first match wins.

This is `gratimos/policy/rules.py`'s shape, **ported rather than imported**, and
the reason is layering rather than convenience. `gratimos` is the generator and
`slpie` is the platform; a kernel import from `slpie/apim/` into `gratimos/`
inverts that, and invariant 8's single-import budget is already spent on the
codegen bridge. The alternative — reaching through the plugin registry for a
hundred lines of first-match dispatch — is more machinery than the duplication
costs. **This is deliberate duplication and it is worth saying so out loud.**

What it decides is everything that is *not* a yes/no access question: which
version to route to, whether a deprecation header belongs on the response,
whether a payload is too large, whether a cached answer may be served. Access
itself stays in `rbac/engine.py` and is not reimplemented here — one
authorisation model, and the API manager is not a second one.

First match wins, and rules are evaluated in priority order with ties broken by
insertion. Not "most specific wins": specificity ordering is how a narrow rule
somebody added last year silently overrides the broad rule somebody is reading
now, and the whole point of a chain is that you can read it top to bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass(frozen=True, slots=True)
class Situation:
    """Everything a rule may look at. Flat on purpose — a rule that has to
    traverse an object graph is a rule nobody can predict the behaviour of."""

    api: str = ""
    version: str = "v1"
    operation: str = ""            # "GET /api/findings"
    method: str = "GET"
    path: str = ""
    application: str = ""
    principal: str = ""
    tenant: str = ""
    tier: str = "gold"
    bytes: int = 0
    target: str = "simulated"
    state: str = "published"
    cacheable: bool = False

    def get(self, name: str, default: Any = None) -> Any:
        return getattr(self, name, default)

    @property
    def properties(self) -> dict[str, Any]:
        """What `governance.policies.Condition` reads through.

        `_attribute` falls through to `node.properties.get(name)` for anything
        that is not an intrinsic, so exposing the fields here is what lets the
        governance parser be *imported* rather than reimplemented — a policy
        author writes `property: target` and the same twelve operators apply.
        """
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "api": self.api, "version": self.version, "operation": self.operation,
            "method": self.method, "path": self.path,
            "application": self.application, "principal": self.principal,
            "tenant": self.tenant, "tier": self.tier, "bytes": self.bytes,
            "target": self.target, "state": self.state, "cacheable": self.cacheable,
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    """What to do about this call, short of allowing or refusing it."""

    action: str                    # route | cache | deprecate | reject | mediate | pass
    detail: str = ""
    rule: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    status: int = 0                # non-zero only for `reject`

    @property
    def refuses(self) -> bool:
        return self.action == "reject"

    def explain(self) -> str:
        """Why, naming the rule. A refusal that cannot name its rule is a
        refusal nobody can argue with, and unarguable refusals get worked
        around rather than fixed."""
        named = f"rule {self.rule!r}: " if self.rule else ""
        return f"{named}{self.detail or self.action}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "detail": self.detail,
            "rule": self.rule,
            "headers": [list(pair) for pair in self.headers],
            "status": self.status,
        }


PASS = Verdict("pass", "no rule matched")


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule: a name, a priority, a test, and what it decides."""

    name: str
    matches: Callable[[Situation], bool]
    verdict: Verdict
    priority: int = 100
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "description": self.description,
            "verdict": self.verdict.to_dict(),
        }


@dataclass
class Chain:
    """An ordered chain, evaluated in full and reported like `iptables -L -v`."""

    rules: list[Rule] = field(default_factory=list)
    default: Verdict = PASS
    _hits: dict[str, int] = field(default_factory=dict, repr=False)
    #: Rules that raised. Counted rather than swallowed: a rule that abstains
    #: every time is a rule that is not doing its job, and if it was written to
    #: reject something then it is failing *open* — which is the direction that
    #: matters and the direction nobody notices without a number.
    _abstained: dict[str, int] = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return len(self.rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._ordered())

    def add(self, rule: Rule) -> Rule:
        self.rules.append(rule)
        return rule

    def _ordered(self) -> list[Rule]:
        # Stable: ties keep insertion order, so a reader can predict the outcome
        # from the file rather than from the sort.
        return sorted(self.rules, key=lambda rule: rule.priority)

    def decide(self, situation: Situation) -> Verdict:
        for rule in self._ordered():
            try:
                hit = rule.matches(situation)
            except Exception:  # noqa: BLE001 - a raising rule abstains
                # The same treatment `governance/RuleSet` gives a raising rule:
                # it abstains rather than taking the chain down with it. A rule
                # that cannot decide is not a rule that decides "no" — but the
                # abstention is counted, because a rule written to reject
                # something and abstaining every time is failing open.
                self._abstained[rule.name] = self._abstained.get(rule.name, 0) + 1
                continue
            if hit:
                self._hits[rule.name] = self._hits.get(rule.name, 0) + 1
                return rule.verdict
        return self.default

    def report(self) -> list[dict[str, Any]]:
        """The chain with its hit counts, which is what `#/gateway` renders."""
        return [
            {
                **rule.to_dict(),
                "hits": self._hits.get(rule.name, 0),
                "abstained": self._abstained.get(rule.name, 0),
            }
            for rule in self._ordered()
        ]


# --- the rules a build starts with -------------------------------------------


def standard(*, max_bytes: int = 1024 * 1024) -> Chain:
    """The chain every build has before anybody writes a policy file.

    Deliberately short. Each of these is a decision that would otherwise be
    scattered across handlers, and having them here means an operator can read
    the whole mediation policy in one place.
    """
    chain = Chain()

    chain.add(Rule(
        name="retired-is-gone",
        priority=10,
        matches=lambda s: s.state == "retired",
        verdict=Verdict(
            "reject", "this API has been retired", rule="retired-is-gone", status=410,
        ),
        description="410 rather than 404: it existed, and saying so is the point",
    ))
    chain.add(Rule(
        name="blocked-is-refused",
        priority=20,
        matches=lambda s: s.state == "blocked",
        verdict=Verdict(
            "reject", "this API is blocked", rule="blocked-is-refused", status=403,
        ),
    ))
    chain.add(Rule(
        name="payload-ceiling",
        priority=30,
        matches=lambda s: s.bytes > max_bytes,
        verdict=Verdict(
            "reject",
            f"a request body over {max_bytes} bytes is refused",
            rule="payload-ceiling",
            status=413,
        ),
    ))
    chain.add(Rule(
        name="deprecated-carries-sunset",
        priority=40,
        matches=lambda s: s.state == "deprecated",
        verdict=Verdict(
            "deprecate",
            "served, and the response says this API is going away",
            rule="deprecated-carries-sunset",
            # RFC 8594's shape, without the dependency. A generic client that
            # understands these headers backs off on its own schedule.
            headers=(("Deprecation", "true"),),
        ),
    ))
    chain.add(Rule(
        name="reads-may-be-cached",
        priority=50,
        matches=lambda s: s.cacheable and s.method == "GET",
        verdict=Verdict(
            "cache", "an edge cache may hold this", rule="reads-may-be-cached",
            headers=(("Cache-Control", "public, max-age=30"),),
        ),
    ))
    return chain
