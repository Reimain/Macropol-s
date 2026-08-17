"""API policies as files, in the vocabulary operators already know.

`slpie/governance/policies.py` solved this exact problem for governance rules,
and its properties are precisely what an APIM policy file needs:

* a **closed** twelve-operator vocabulary, so a file cannot express something
  the evaluator does not understand;
* **fnmatch rather than regex**, so a policy file cannot be a ReDoS — an
  operator writing a pattern should not be able to hang the gateway;
* `to_rule()` compiling to a **closure**, not to generated code that is parsed
  and executed;
* a malformed file that **records an error and lets the rest load**, because one
  bad rule should not take every other rule down with it.

So the parser is imported rather than rewritten. What is defined here is only
the thing governance does not have: a policy whose subject is a *call* rather
than a node, compiling into a `chain.Rule`.

This is the third of the three policy patterns, and the last: authorisation is
`rbac/engine.py`, mediation is `chain.py`, and the file format is this. A fourth
is not invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..governance.policies import Condition
from .chain import Chain, Rule, Situation, Verdict
from .errors import ApimError

#: What a policy may decide. Deliberately not "allow" — allowing is
#: authorisation, and authorisation is not in this file's remit.
ACTIONS = frozenset({"route", "cache", "deprecate", "reject", "mediate", "pass"})


@dataclass(frozen=True, slots=True)
class ApimPolicy:
    """One rule, as written in a policy file."""

    id: str
    title: str = ""
    priority: int = 100
    action: str = "pass"
    detail: str = ""
    status: int = 0
    headers: tuple[tuple[str, str], ...] = ()
    all_of: tuple[Condition, ...] = ()
    any_of: tuple[Condition, ...] = ()
    source: str = ""
    line: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise ApimError("a policy must have an id")
        if self.action not in ACTIONS:
            raise ApimError(
                f"policy {self.id!r} decides {self.action!r}; expected one of "
                f"{', '.join(sorted(ACTIONS))}"
            )
        if not self.all_of and not self.any_of:
            # A policy with no conditions matches every call, which is never
            # what anybody meant — the same guard `governance.Policy` has, and
            # for a worse failure mode: here it would reject all traffic.
            raise ApimError(f"policy {self.id!r} states no conditions")
        if self.action == "reject" and not self.detail:
            raise ApimError(
                f"policy {self.id!r} rejects and says nothing; a refusal that "
                f"cannot explain itself gets worked around rather than fixed"
            )

    def matches(self, situation: Situation) -> bool:
        if self.all_of and not all(c.holds(situation) for c in self.all_of):
            return False
        if self.any_of and not any(c.holds(situation) for c in self.any_of):
            return False
        return True

    @property
    def reason(self) -> str:
        parts = [str(c) for c in self.all_of]
        if self.any_of:
            parts.append("(" + " or ".join(str(c) for c in self.any_of) + ")")
        return " and ".join(parts)

    def to_rule(self) -> Rule:
        """Compile to a `chain.Rule`. Nothing is generated or executed.

        The returned rule holds this frozen dataclass and asks it questions,
        which is the same compilation `governance.Policy.to_rule` performs.
        """
        policy = self
        return Rule(
            name=policy.id,
            priority=policy.priority,
            matches=policy.matches,
            verdict=Verdict(
                action=policy.action,
                detail=policy.detail or policy.reason,
                rule=policy.id,
                headers=policy.headers,
                status=policy.status,
            ),
            description=policy.title or policy.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "priority": self.priority,
            "action": self.action,
            "detail": self.detail,
            "status": self.status,
            "headers": [list(pair) for pair in self.headers],
            "conditions": {
                "all_of": [c.to_dict() for c in self.all_of],
                "any_of": [c.to_dict() for c in self.any_of],
            },
            "source": self.source,
            "line": self.line,
        }


@dataclass
class PolicySet:
    """Policies loaded from files, with the ones that failed named."""

    policies: list[ApimPolicy] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.policies)

    def chain(self, *, base: Chain | None = None) -> Chain:
        """The mediation chain these policies describe, over a base chain.

        The base rules keep their priorities, so a policy file can sit before or
        after them by choosing a number — rather than by a load order nobody can
        see from the file.
        """
        from .chain import standard

        built = base if base is not None else standard()
        for policy in self.policies:
            built.add(policy.to_rule())
        return built

    def to_dict(self) -> dict[str, Any]:
        return {
            "policies": [policy.to_dict() for policy in self.policies],
            "errors": list(self.errors),
        }


def parse(documents: Iterable[tuple[str, Sequence[Any]]]) -> PolicySet:
    """Build a policy set from already-parsed documents.

    Reading the files is the caller's job — the manifest loader already knows
    how, and duplicating YAML handling here would be a second parser to keep in
    step with the first. Each entry is `(source, [mapping, ...])`.
    """
    found = PolicySet()

    for source, entries in documents:
        for index, entry in enumerate(entries or (), start=1):
            try:
                found.policies.append(_one(entry, source=source, line=index))
            except (ApimError, ValueError, TypeError, KeyError) as error:
                # Recorded, not raised. One malformed rule must not take every
                # other rule in the deployment down with it.
                found.errors.append(f"{source}[{index}]: {error}")

    return found


def _one(entry: Any, *, source: str, line: int) -> ApimPolicy:
    if not isinstance(entry, dict):
        raise ApimError("a policy must be a mapping")

    return ApimPolicy(
        id=str(entry.get("id", "")),
        title=str(entry.get("title", "")),
        priority=int(entry.get("priority", 100)),
        action=str(entry.get("action", "pass")),
        detail=str(entry.get("detail", "")),
        status=int(entry.get("status", 0)),
        headers=tuple(
            (str(name), str(value))
            for name, value in (entry.get("headers") or {}).items()
        ),
        all_of=_conditions(entry.get("all_of")),
        any_of=_conditions(entry.get("any_of")),
        source=source,
        line=line,
    )


def _conditions(raw: Any) -> tuple[Condition, ...]:
    if not raw:
        return ()
    built: list[Condition] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ApimError("a condition must be a mapping")
        prop = item.get("property")
        if not prop:
            raise ApimError("a condition must name a property")
        for operator, value in item.items():
            if operator == "property":
                continue
            built.append(Condition(property=str(prop), operator=operator, value=value))
    return tuple(built)
