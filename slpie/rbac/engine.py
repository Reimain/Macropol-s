"""The decision engine. Every answer says why, including the refusals.

An authorisation engine is judged on the day something is wrongly denied in
production and on the day an auditor asks who could have done a thing. Both are
questions about *explanation*, and both are why this returns a `Decision` object
rather than a boolean.

```
check(principal, action, resource, scope)
      │
      ├─ bindings in scope ──→ roles ──→ inheritance closure
      │                                        │
      │                          effective permissions
      │                                        │
      ├─ any DENY matches?  ────────────→ refused, naming the rule
      ├─ any ALLOW matches? ──→ assurance sufficient?
      │                            no ──→ refused, with a step-up obligation
      │                            yes ─→ allowed, naming the rule and the role
      └─ nothing matched  ──────────────→ refused: no rule grants this
```

Four properties, each of which exists because its absence causes a specific
production failure:

* **Deny by default.** An unmatched action is refused. Systems that default to
  allow are secure only for as long as the deny list is complete, and it never
  is.
* **Explicit deny always wins.** Not "most specific wins". Specificity ordering
  means a broad grant added later can silently overturn a narrow refusal, and
  nobody reviewing the broad grant will notice.
* **A decision names the rule and the role.** `"denied"` is useless at 3am.
  `"denied by rule 'deny deploy.apply on env:prod' from role 'contractor'"` is
  a fix.
* **Every denial is recorded.** Denials are the security signal — a burst of
  them is either an attack or a broken deployment, and both need to be visible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, Sequence

from ..identity.principal import Assurance, Principal
from .model import Effect, Permission, RbacError, Scope
from .role import Role, RoleGraph, Separation

#: How long a binding lasts when nobody says. Zero means "until revoked", which
#: is right for a job function and wrong for everything else — see `elevate`.
PERMANENT = 0.0

#: Break-glass elevations are capped hard. An emergency that lasts longer than
#: this is not an emergency, it is an unrequested role change.
MAX_ELEVATION = 4 * 3600.0


class Outcome(str, Enum):
    """Why a decision went the way it did. Distinguishes the three refusals."""

    ALLOWED = "allowed"
    DENIED_BY_RULE = "denied_by_rule"           # a DENY permission matched
    DENIED_NO_RULE = "denied_no_rule"           # deny by default
    DENIED_ASSURANCE = "denied_assurance"       # allowed, but sign-in too weak
    DENIED_CONDITION = "denied_condition"       # a custom condition refused
    DENIED_EXPIRED = "denied_expired"           # the session or binding lapsed

    @property
    def allowed(self) -> bool:
        return self is Outcome.ALLOWED

    @property
    def recoverable(self) -> bool:
        """Whether the caller could do something about it and retry."""
        return self in (Outcome.DENIED_ASSURANCE, Outcome.DENIED_EXPIRED)


class Condition(Protocol):
    """A customer-specific check, plugged in by name rather than by patching.

    This is the extension seam: a policy file says `condition: business-hours`
    and the deployment registers a callable under that name. Custom logic lives
    in the customer's code, the kernel keeps its boundary, and upgrading does
    not replay their patches.
    """

    def __call__(self, principal: Principal, resource: str,
                 context: Mapping[str, Any]) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class Binding:
    """One principal holding one role, in one scope, for a period.

    Bindings are appended and superseded, never edited — the same rule as the
    connector keyring, for the same reason: "who held this role in March?" must
    be answerable after the role was removed.
    """

    principal_urn: str
    role: str
    scope: Scope = Scope.GLOBAL
    granted_at: float = 0.0
    expires_at: float = PERMANENT
    granted_by: str = ""
    reason: str = ""
    revoked_at: float = 0.0
    revoked_by: str = ""
    elevation: bool = False
    sequence: int = 0

    @property
    def active(self) -> bool:
        return not self.revoked_at

    def live(self, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self.revoked_at:
            return False
        return not self.expires_at or moment < self.expires_at

    def applies_to(self, scope: Scope) -> bool:
        return self.scope.contains(scope)

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal_urn,
            "role": self.role,
            "scope": str(self.scope),
            "granted_at": self.granted_at,
            "granted_by": self.granted_by,
            "expires_at": self.expires_at,
            "reason": self.reason,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "elevation": self.elevation,
            "sequence": self.sequence,
        }

    def __str__(self) -> str:
        window = "permanent" if not self.expires_at else f"until {self.expires_at:.0f}"
        return f"{self.role}@{self.scope} ({window})"


@dataclass(frozen=True, slots=True)
class Decision:
    """The answer, and everything needed to act on it or defend it."""

    outcome: Outcome
    action: str
    resource: str
    principal_urn: str
    scope: Scope = Scope.GLOBAL
    rule: Permission | None = None
    role: str = ""
    roles_held: tuple[str, ...] = ()
    required_assurance: Assurance = Assurance.NONE
    held_assurance: Assurance = Assurance.NONE
    reason: str = ""
    decided_at: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.outcome.allowed

    def __bool__(self) -> bool:
        return self.allowed

    @property
    def obligation(self) -> str:
        """What the caller must do to turn this refusal into an allow, if anything."""
        if self.outcome is Outcome.DENIED_ASSURANCE:
            return f"re-authenticate at {self.required_assurance.name} or above"
        if self.outcome is Outcome.DENIED_EXPIRED:
            return "sign in again"
        return ""

    def explain(self) -> str:
        """One line, written to be pasted into a ticket unedited."""
        head = f"{self.action} on {self.resource}"
        if self.allowed:
            return f"allowed: {head} — {self.reason}"
        obligation = f" ({self.obligation})" if self.obligation else ""
        return f"denied: {head} — {self.reason}{obligation}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "allowed": self.allowed,
            "action": self.action,
            "resource": self.resource,
            "principal": self.principal_urn,
            "scope": str(self.scope),
            "rule": self.rule.to_dict() if self.rule else None,
            "role": self.role,
            "roles_held": list(self.roles_held),
            "required_assurance": self.required_assurance.name,
            "held_assurance": self.held_assurance.name,
            "reason": self.reason,
            "obligation": self.obligation,
            "decided_at": self.decided_at,
        }

    def __str__(self) -> str:
        return self.explain()


class AccessEngine:
    """Roles, bindings and decisions. Embedded, deterministic, explainable.

    No server, no database, no admin console. The whole engine is a role graph
    and an append-only binding log, which is what makes it possible to run the
    same authorisation in a CLI, in a worker and in an API and get identical
    answers — the property a separate policy server cannot offer, because it
    is reachable from only one of the three.
    """

    def __init__(
        self,
        roles: RoleGraph | None = None,
        *,
        conditions: Mapping[str, Condition] | None = None,
        record_allows: bool = False,
        emit: Callable[[Decision], None] | None = None,
    ) -> None:
        self.roles = roles if roles is not None else RoleGraph()
        self.conditions: dict[str, Condition] = dict(conditions or {})
        self.record_allows = record_allows
        self._emit = emit
        self._log: list[Binding] = []
        self._bindings: dict[str, list[Binding]] = {}
        self._decisions: list[Decision] = []
        self._sequence = 0

    # -- extension --------------------------------------------------------

    def register_condition(self, name: str, condition: Condition) -> None:
        """Plug in customer logic without touching the kernel.

        Refuses to replace an existing name: silently shadowing a condition
        another policy file already depends on changes decisions with no diff to
        review.
        """
        if name in self.conditions:
            raise RbacError(f"a condition named {name!r} is already registered")
        self.conditions[name] = condition

    # -- bindings ---------------------------------------------------------

    def bind(
        self,
        principal_urn: str,
        role: str,
        *,
        scope: Scope = Scope.GLOBAL,
        granted_by: str = "",
        reason: str = "",
        expires_at: float = PERMANENT,
        elevation: bool = False,
        now: float | None = None,
    ) -> Binding:
        """Assign a role. Refuses anything that would break a separation rule."""
        moment = time.time() if now is None else now
        self.roles.require(role)

        held = {b.role for b in self.bindings_of(principal_urn, now=moment)}
        conflicts = self.roles.conflicts({*held, role})
        if conflicts:
            separation, overlap = conflicts[0]
            raise RbacError(
                f"assigning {role!r} would give {principal_urn} both "
                f"{' and '.join(sorted(overlap))}, which {separation.name} "
                f"forbids: {separation.reason}"
            )

        if elevation:
            if not reason.strip():
                raise RbacError("an elevation must state why it was needed")
            if not expires_at:
                raise RbacError(
                    "an elevation must expire; a permanent emergency is a role change"
                )
            if expires_at - moment > MAX_ELEVATION:
                raise RbacError(
                    f"an elevation may not exceed {MAX_ELEVATION / 3600:.0f} hours; "
                    f"request a standing role instead"
                )

        binding = Binding(
            principal_urn=principal_urn, role=role, scope=scope,
            granted_at=moment, expires_at=expires_at, granted_by=granted_by,
            reason=reason, elevation=elevation,
        )
        return self._append(binding)

    def elevate(self, principal_urn: str, role: str, *, reason: str,
                seconds: float = 3600.0, granted_by: str = "",
                scope: Scope = Scope.GLOBAL, now: float | None = None) -> Binding:
        """Break-glass: time-boxed, reasoned, and impossible to make permanent."""
        moment = time.time() if now is None else now
        return self.bind(
            principal_urn, role, scope=scope, granted_by=granted_by, reason=reason,
            expires_at=moment + seconds, elevation=True, now=moment,
        )

    def unbind(self, principal_urn: str, role: str, *, revoked_by: str = "",
               scope: Scope | None = None, now: float | None = None) -> int:
        """Withdraw a role. Supersedes rather than deletes."""
        moment = time.time() if now is None else now
        removed = 0
        for binding in list(self._bindings.get(principal_urn, ())):
            if binding.role != role or not binding.live(now=moment):
                continue
            if scope is not None and binding.scope != scope:
                continue
            self._append(replace(binding, revoked_at=moment, revoked_by=revoked_by))
            removed += 1
        return removed

    def bindings_of(self, principal_urn: str, *, scope: Scope | None = None,
                    now: float | None = None) -> tuple[Binding, ...]:
        live = [
            binding for binding in self._bindings.get(principal_urn, ())
            if binding.live(now=now)
            and (scope is None or binding.applies_to(scope))
        ]
        return tuple(live)

    def roles_of(self, principal_urn: str, *, scope: Scope = Scope.GLOBAL,
                 now: float | None = None) -> frozenset[str]:
        """Every role in force here, inheritance included."""
        direct = {b.role for b in self.bindings_of(principal_urn, scope=scope, now=now)}
        return self.roles.closure(direct)

    def _append(self, binding: Binding) -> Binding:
        self._sequence += 1
        recorded = replace(binding, sequence=self._sequence)
        self._log.append(recorded)
        current = self._bindings.setdefault(recorded.principal_urn, [])
        # A revocation replaces the live row; a grant appends a new one.
        for index, existing in enumerate(current):
            if (existing.role, existing.scope) == (recorded.role, recorded.scope):
                current[index] = recorded
                break
        else:
            current.append(recorded)
        return recorded

    # -- the decision -----------------------------------------------------

    def check(
        self,
        principal: Principal,
        action: str,
        resource: str = "*",
        *,
        scope: Scope = Scope.GLOBAL,
        context: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> Decision:
        """Decide, and say why. Never raises for a refusal — see :meth:`require`."""
        moment = time.time() if now is None else now
        held = self.roles_of(principal.urn, scope=scope, now=moment)
        ordered = tuple(sorted(held))

        def decide(outcome: Outcome, reason: str, **extra: Any) -> Decision:
            decision = Decision(
                outcome=outcome, action=action, resource=resource,
                principal_urn=principal.urn, scope=scope, roles_held=ordered,
                held_assurance=principal.assurance, reason=reason,
                decided_at=moment, **extra,
            )
            self._record(decision)
            return decision

        if principal.expired(now=moment):
            return decide(
                Outcome.DENIED_EXPIRED,
                f"{principal.label} signed in but that session has expired",
            )

        if not held:
            return decide(
                Outcome.DENIED_NO_RULE,
                f"{principal.label} holds no role at scope {scope}",
            )

        matching: list[tuple[str, Permission]] = []
        for role_name in ordered:
            for permission in self.roles.effective_permissions(role_name):
                if permission.matches(action, resource):
                    matching.append((role_name, permission))

        # Explicit deny first, and it is final.
        for role_name, permission in matching:
            if permission.effect is Effect.DENY:
                return decide(
                    Outcome.DENIED_BY_RULE,
                    f"refused by rule '{permission}' from role '{role_name}'",
                    rule=permission, role=role_name,
                )

        allows = [(name, p) for name, p in matching if p.allows]
        if not allows:
            return decide(
                Outcome.DENIED_NO_RULE,
                f"no rule in {', '.join(ordered)} grants {action} on {resource}",
            )

        # Prefer the narrowest matching allow when reporting, so the explanation
        # names the rule an operator would have expected to see.
        role_name, permission = max(allows, key=lambda pair: pair[1].specificity)
        required = max(permission.assurance, self.roles.require(role_name).assurance)

        if not principal.assurance.satisfies(required):
            return decide(
                Outcome.DENIED_ASSURANCE,
                f"'{permission}' from role '{role_name}' requires {required.name} "
                f"assurance; this sign-in is {principal.assurance.name}",
                rule=permission, role=role_name, required_assurance=required,
            )

        if permission.condition:
            check = self.conditions.get(permission.condition)
            if check is None:
                # An unregistered condition denies. Treating it as satisfied
                # would mean a typo in a policy file silently widens access.
                return decide(
                    Outcome.DENIED_CONDITION,
                    f"'{permission}' requires condition {permission.condition!r}, "
                    f"which is not registered in this deployment",
                    rule=permission, role=role_name,
                )
            try:
                satisfied = bool(check(principal, resource, dict(context or {})))
            except Exception as exc:  # noqa: BLE001 - customer code, any failure denies
                return decide(
                    Outcome.DENIED_CONDITION,
                    f"condition {permission.condition!r} failed to evaluate ({exc}); "
                    f"refusing rather than assuming",
                    rule=permission, role=role_name,
                )
            if not satisfied:
                return decide(
                    Outcome.DENIED_CONDITION,
                    f"condition {permission.condition!r} was not satisfied",
                    rule=permission, role=role_name,
                )

        return decide(
            Outcome.ALLOWED,
            f"granted by rule '{permission}' from role '{role_name}'",
            rule=permission, role=role_name, required_assurance=required,
        )

    def allows(self, principal: Principal, action: str, resource: str = "*",
               **settings: Any) -> bool:
        return self.check(principal, action, resource, **settings).allowed

    def require(self, principal: Principal, action: str, resource: str = "*",
                **settings: Any) -> Decision:
        """Decide, and raise on refusal with the explanation in the message."""
        decision = self.check(principal, action, resource, **settings)
        if not decision.allowed:
            raise RbacError(decision.explain())
        return decision

    def permitted(self, principal: Principal, resource: str = "*", *,
                  scope: Scope = Scope.GLOBAL, now: float | None = None
                  ) -> tuple[str, ...]:
        """Every action this principal may take here — the UI's question.

        Answered from the allow rules that are not overridden by a deny, so a
        menu can be rendered without probing the engine once per button.
        """
        held = self.roles_of(principal.urn, scope=scope, now=now)
        denied: set[str] = set()
        allowed: set[str] = set()
        for role_name in sorted(held):
            for permission in self.roles.effective_permissions(role_name):
                from .model import matches_resource

                if not matches_resource(permission.resource, resource):
                    continue
                (denied if permission.effect is Effect.DENY else allowed).add(
                    permission.action
                )
        return tuple(sorted(allowed - denied))

    # -- the audit trail --------------------------------------------------

    def _record(self, decision: Decision) -> None:
        if self.record_allows or not decision.allowed:
            self._decisions.append(decision)
        if self._emit is not None:
            self._emit(decision)

    @property
    def log(self) -> tuple[Binding, ...]:
        """Every binding transition, in order."""
        return tuple(self._log)

    @property
    def decisions(self) -> tuple[Decision, ...]:
        """Recorded decisions. Denials always; allows only if asked for."""
        return tuple(self._decisions)

    def denials(self, *, since: float = 0.0) -> tuple[Decision, ...]:
        return tuple(d for d in self._decisions if not d.allowed and d.decided_at >= since)

    def at(self, sequence: int) -> dict[tuple[str, str, str], Binding]:
        """Who held what at a point in the log — the breach-investigation query."""
        state: dict[tuple[str, str, str], Binding] = {}
        for binding in self._log:
            if binding.sequence > sequence:
                break
            state[(binding.principal_urn, binding.role, str(binding.scope))] = binding
        return state

    def history(self, principal_urn: str = "", role: str = "") -> tuple[Binding, ...]:
        return tuple(
            binding for binding in self._log
            if (not principal_urn or binding.principal_urn == principal_urn)
            and (not role or binding.role == role)
        )

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        live = [b for bindings in self._bindings.values() for b in bindings if b.live(now=now)]
        return {
            "roles": len(self.roles),
            "separations": len(self.roles.separations),
            "principals": len({b.principal_urn for b in live}),
            "bindings": len(live),
            "elevations": len([b for b in live if b.elevation]),
            "conditions": sorted(self.conditions),
            "events": len(self._log),
            "denials": len([d for d in self._decisions if not d.allowed]),
        }

    def __repr__(self) -> str:  # pragma: no cover - display only
        status = self.status()
        return (
            f"<AccessEngine {status['roles']} roles, {status['bindings']} bindings, "
            f"{status['denials']} denials>"
        )
