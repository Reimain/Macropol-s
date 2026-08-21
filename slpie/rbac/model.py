"""Permissions, resources and scopes — the vocabulary a decision is made in.

Keycloak's authorisation model is powerful and almost nobody can predict what it
will return. The reason is not complexity for its own sake: it is that policies,
scopes, permissions and resources are four orthogonal concepts that combine
multiplicatively, and the evaluation order is an implementation detail rather
than a stated rule. When an access decision surprises you, the only way to find
out why is to turn on debug logging on a Java server.

The model here is deliberately narrower, and every narrowing is a decision:

* **One permission type**, `(effect, action, resource)`. There is no separate
  "policy" object that permissions reference. A rule you can read in one line
  is a rule an auditor can check.
* **Deny is explicit and always wins.** Not "the most specific rule wins" —
  specificity is a tie-break humans get wrong under pressure. If any matching
  rule denies, the answer is no, and the rule that denied is named.
* **Deny by default.** An action nobody granted is refused, and the refusal says
  "no rule grants this" rather than pretending a rule was consulted.
* **Wildcards are matched, never expanded.** `repo:acme/**` is stored as it was
  written, so the audit surface can find over-broad grants by looking at the
  rules rather than by enumerating a resource universe that does not exist yet.

Actions and resources are both hierarchical strings, because everything real is:
`graph.read` is narrower than `graph.*`, and `repo:acme/payments` sits under
`repo:acme/**`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..errors import SlpieError
from ..identity.principal import Assurance


class RbacError(SlpieError):
    """A role model was malformed, or an assignment was refused."""


class Effect(str, Enum):
    """What a matching rule does. Two values, and DENY always wins."""

    ALLOW = "allow"
    DENY = "deny"


#: Separates a resource's kind from its path: `repo:acme/payments`.
KIND_SEPARATOR = ":"

#: Matches exactly one path segment.
SEGMENT_WILDCARD = "*"

#: Matches this segment and every segment below it.
DEEP_WILDCARD = "**"


def _split(pattern: str) -> tuple[str, tuple[str, ...]]:
    kind, separator, path = pattern.partition(KIND_SEPARATOR)
    if not separator:
        # A bare `*` means "any resource at all", which is legitimate but is
        # exactly what the audit surface looks for.
        return (kind.strip(), ())
    return (kind.strip(), tuple(segment for segment in path.split("/") if segment))


def matches_resource(pattern: str, resource: str) -> bool:
    """Whether a resource pattern covers a concrete resource.

    Segment-wise rather than by regular expression or `fnmatch`, because both of
    those let `*` cross a `/` — and a pattern intended to mean "any repo in this
    org" that quietly also means "any repo anywhere" is a privilege escalation
    that reads as a typo.
    """
    if pattern.strip() in (SEGMENT_WILDCARD, DEEP_WILDCARD):
        return True

    want_kind, want_path = _split(pattern)
    have_kind, have_path = _split(resource)

    if want_kind not in (have_kind, SEGMENT_WILDCARD, DEEP_WILDCARD):
        return False
    if not want_path:
        # `repo:` with no path means the kind as a whole.
        return True

    index = 0
    for index, segment in enumerate(want_path):
        if segment == DEEP_WILDCARD:
            return True
        if index >= len(have_path):
            return False
        if segment != SEGMENT_WILDCARD and segment != have_path[index]:
            return False
    # Every pattern segment matched; the resource must not be deeper, or
    # `repo:acme` would grant `repo:acme/payments`.
    return len(want_path) == len(have_path)


def matches_action(pattern: str, action: str) -> bool:
    """Dotted actions: `graph.*` covers `graph.read`, `*` covers everything.

    A colon in the *action* position is normalised to a dot before matching, in
    both the pattern and the action. `slpie/workspace/plane.py` named its two
    actions `workspace:create` and `dataset:read`, and nothing here understood
    the colon — so `allow workspace.* on "*"` matched neither of them and an
    operator who wrote the obvious grant got a silent refusal.

    Normalising rather than rejecting, because the colon form is in policy files
    that already exist and a hard failure would lock out the very rules meant to
    grant access. A `DeprecationWarning` is not an option: `pyproject.toml` sets
    `filterwarnings = ["error::DeprecationWarning:slpie.*"]`, so warning here
    would turn a legacy policy file into a crash.

    Note this applies to actions only. Resources keep the colon as their kind
    separator — `env:prod/*`, `dataset:sales` — and `matches_resource` is
    untouched.
    """
    want = pattern.strip().replace(":", ".")
    action = action.replace(":", ".")
    if want in (SEGMENT_WILDCARD, DEEP_WILDCARD):
        return True
    if want == action:
        return True
    if want.endswith(".*"):
        return action.startswith(want[:-1])
    return False


def specificity(pattern: str) -> int:
    """How narrow a pattern is. Used for reporting, never for deciding.

    Ranking rules by specificity to resolve conflicts is the design that makes
    authorisation unpredictable; here DENY simply wins. But an auditor still
    wants the broad rules surfaced first, and that is what this is for.
    """
    if pattern.strip() in (SEGMENT_WILDCARD, DEEP_WILDCARD):
        return 0
    kind, path = _split(pattern)
    score = 1
    for segment in path:
        if segment == DEEP_WILDCARD:
            break
        score += 1 if segment == SEGMENT_WILDCARD else 2
    return score


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a grant applies. A role is never global unless it says so.

    The single largest source of accidental privilege in a role system is a role
    that was meant for one team and turns out to apply everywhere. Making the
    scope part of the *binding* rather than of the role means the same
    `reviewer` role can be held in one tenant and not another, without minting
    `payments-reviewer`, `billing-reviewer`, and forty more.
    """

    tenant: str = ""
    realm: str = ""

    #: `Scope.GLOBAL` is attached after the class body. Declaring it inside
    #: would make the dataclass treat it as a third field, which is a genuinely
    #: confusing failure — every Scope would carry a Scope.
    @property
    def is_global(self) -> bool:
        return not self.tenant and not self.realm

    def contains(self, other: "Scope") -> bool:
        """Whether a grant at this scope reaches something at `other`.

        Global contains everything; a tenant contains its realms; a realm
        contains only itself. Deliberately not the reverse — a realm
        administrator must not gain the tenant.
        """
        if self.is_global:
            return True
        if self.tenant != other.tenant:
            return False
        return not self.realm or self.realm == other.realm

    def to_dict(self) -> dict[str, Any]:
        return {"tenant": self.tenant, "realm": self.realm}

    def __str__(self) -> str:
        if self.is_global:
            return "*"
        return f"{self.tenant}/{self.realm}" if self.realm else self.tenant


Scope.GLOBAL = Scope()


@dataclass(frozen=True, slots=True)
class Permission:
    """One rule. Readable in a line, checkable by a human."""

    action: str
    resource: str = SEGMENT_WILDCARD
    effect: Effect = Effect.ALLOW
    assurance: Assurance = Assurance.STANDARD
    condition: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise RbacError("a permission must name an action")
        if not self.resource.strip():
            raise RbacError(
                f"permission on {self.action!r} has an empty resource; write "
                f"'*' if you genuinely mean every resource"
            )

    @property
    def allows(self) -> bool:
        return self.effect is Effect.ALLOW

    @property
    def is_wildcard(self) -> bool:
        """Whether this grants breadth an auditor should look at."""
        return (
            self.action.strip() in (SEGMENT_WILDCARD, DEEP_WILDCARD)
            or self.resource.strip() in (SEGMENT_WILDCARD, DEEP_WILDCARD)
            or self.resource.strip().endswith(DEEP_WILDCARD)
        )

    @property
    def specificity(self) -> int:
        return specificity(self.resource) + (0 if self.action.endswith("*") else 2)

    def matches(self, action: str, resource: str) -> bool:
        return matches_action(self.action, action) and matches_resource(
            self.resource, resource
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "action": self.action,
            "resource": self.resource,
            "assurance": self.assurance.name,
            "condition": self.condition,
            "description": self.description,
        }

    def __str__(self) -> str:
        suffix = f" if {self.condition}" if self.condition else ""
        return f"{self.effect.value} {self.action} on {self.resource}{suffix}"


def allow(action: str, resource: str = SEGMENT_WILDCARD, **settings: Any) -> Permission:
    return Permission(action=action, resource=resource, effect=Effect.ALLOW, **settings)


def deny(action: str, resource: str = SEGMENT_WILDCARD, **settings: Any) -> Permission:
    return Permission(action=action, resource=resource, effect=Effect.DENY, **settings)
