"""Roles, inheritance, and the constraints that keep a role model auditable.

A role model rots in three predictable ways, and each has a guard here rather
than a convention:

**Inheritance cycles.** `admin` inherits `operator` inherits `admin` is easy to
create across two config files and impossible to notice. It is refused at
construction, with the cycle printed, because a cycle makes "what can this role
do?" non-terminating.

**Unbounded depth.** Ten levels of inheritance is a model nobody can hold in
their head, and the effective permission set of a leaf role becomes a mystery.
Depth is bounded and the bound is stated.

**Separation of duties.** The requirement every audit raises and almost no role
system enforces: the person who *requests* a production change must not be the
person who *approves* it. Expressed here as mutually exclusive role sets,
checked when a binding is made rather than discovered in a quarterly review.

Roles carry permissions and nothing else. They do not carry users — a role that
knows who holds it cannot be reasoned about separately from the directory, and
the assignment is a different lifecycle with a different audit trail. Bindings
live in `engine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..identity.principal import Assurance
from .model import Effect, Permission, RbacError

#: Deepest inheritance chain permitted. Past this, "what does this role do?"
#: stops being answerable by reading, which is the whole point of roles.
MAX_INHERITANCE_DEPTH = 8


@dataclass(frozen=True, slots=True)
class Role:
    """A named bundle of permissions, and the roles it builds on."""

    name: str
    permissions: tuple[Permission, ...] = ()
    inherits: tuple[str, ...] = ()
    description: str = ""
    assurance: Assurance = Assurance.STANDARD
    system: bool = False          # shipped by us; may not be edited or deleted

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise RbacError("a role must be named")
        if self.name in self.inherits:
            raise RbacError(f"role {self.name!r} cannot inherit from itself")
        if not self.permissions and not self.inherits:
            raise RbacError(
                f"role {self.name!r} grants nothing and inherits nothing; an "
                f"empty role is a role somebody will assume does something"
            )

    @property
    def denies(self) -> tuple[Permission, ...]:
        return tuple(p for p in self.permissions if p.effect is Effect.DENY)

    @property
    def wildcards(self) -> tuple[Permission, ...]:
        return tuple(p for p in self.permissions if p.is_wildcard and p.allows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inherits": list(self.inherits),
            "assurance": self.assurance.name,
            "system": self.system,
            "permissions": [permission.to_dict() for permission in self.permissions],
        }

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class Separation:
    """Roles that must not be held by one principal at one scope.

    `reason` is required. A separation-of-duties rule without a stated reason
    gets removed by the next person who finds it inconvenient, and nobody can
    say what control it was implementing.
    """

    name: str
    roles: frozenset[str]
    reason: str

    def __post_init__(self) -> None:
        if len(self.roles) < 2:
            raise RbacError(
                f"separation {self.name!r} names fewer than two roles; there is "
                f"nothing to separate"
            )
        if not self.reason.strip():
            raise RbacError(
                f"separation {self.name!r} states no reason; an unexplained "
                f"control is one that will be removed as an obstacle"
            )

    def violated_by(self, held: Iterable[str]) -> frozenset[str]:
        """Which of these roles conflict. Empty when the rule is satisfied."""
        overlap = self.roles & frozenset(held)
        return overlap if len(overlap) > 1 else frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "roles": sorted(self.roles), "reason": self.reason}

    def __str__(self) -> str:
        return f"{self.name}: {' / '.join(sorted(self.roles))} are mutually exclusive"


class RoleGraph:
    """Every defined role, with inheritance resolved and validated.

    Resolution is eager and cached: `effective_permissions` walks the
    inheritance chain once per role, so a decision is a lookup rather than a
    graph traversal. Authorisation runs on every request, and a model that costs
    a traversal per check is a model somebody will cache badly later.
    """

    def __init__(self, roles: Iterable[Role] = (), separations: Iterable[Separation] = ()) -> None:
        self._roles: dict[str, Role] = {}
        self._separations: list[Separation] = []
        self._resolved: dict[str, tuple[Permission, ...]] = {}
        self._ancestors: dict[str, frozenset[str]] = {}
        for role in roles:
            self.add(role)
        for separation in separations:
            self.separate(separation)
        if self._roles:
            self.validate()

    # -- construction -----------------------------------------------------

    def add(self, role: Role) -> Role:
        if role.name in self._roles:
            raise RbacError(f"role {role.name!r} is already defined")
        self._roles[role.name] = role
        self._invalidate()
        return role

    def replace(self, role: Role) -> Role:
        """Redefine a role. System roles are refused.

        A shipped role that a customer can quietly edit is a role whose meaning
        differs per deployment, and every support conversation then begins by
        establishing what `admin` means here.
        """
        existing = self._roles.get(role.name)
        if existing is not None and existing.system:
            raise RbacError(
                f"role {role.name!r} is a system role and cannot be redefined; "
                f"define a new role that inherits from it"
            )
        self._roles[role.name] = role
        self._invalidate()
        return role

    def separate(self, separation: Separation) -> Separation:
        unknown = separation.roles - set(self._roles)
        if unknown:
            raise RbacError(
                f"separation {separation.name!r} names undefined role(s) "
                f"{sorted(unknown)}"
            )
        self._separations.append(separation)
        return separation

    def validate(self) -> None:
        """Refuse dangling parents, cycles, and chains too deep to read."""
        for role in self._roles.values():
            for parent in role.inherits:
                if parent not in self._roles:
                    raise RbacError(
                        f"role {role.name!r} inherits undefined role {parent!r}"
                    )
        for name in self._roles:
            self._walk(name, seen=[], depth=0)
        for name in self._roles:
            depth = self._depth(name, seen=())
            if depth > MAX_INHERITANCE_DEPTH:
                raise RbacError(
                    f"role {name!r} inherits {depth} levels deep, past the limit of "
                    f"{MAX_INHERITANCE_DEPTH}; flatten it, because nobody can read "
                    f"what it grants"
                )

    def _depth(self, name: str, *, seen: tuple[str, ...]) -> int:
        """Longest inheritance chain below a role.

        Computed separately from `_walk` because `_walk` memoises ancestors, and
        a memoised parent short-circuits before the recursion depth has
        accumulated — so the depth guard silently passed for every role
        validated after the first.
        """
        role = self._roles.get(name)
        if role is None or name in seen:
            return 0
        return max(
            (1 + self._depth(parent, seen=(*seen, name)) for parent in role.inherits),
            default=0,
        )

    def _walk(self, name: str, *, seen: list[str], depth: int) -> frozenset[str]:
        if name in seen:
            chain = " → ".join([*seen[seen.index(name):], name])
            raise RbacError(f"inheritance cycle: {chain}")
        cached = self._ancestors.get(name)
        if cached is not None:
            return cached

        role = self._roles[name]
        found: set[str] = set()
        for parent in role.inherits:
            found.add(parent)
            found |= self._walk(parent, seen=[*seen, name], depth=depth + 1)
        resolved = frozenset(found)
        self._ancestors[name] = resolved
        return resolved

    def _invalidate(self) -> None:
        self._resolved.clear()
        self._ancestors.clear()

    # -- lookup -----------------------------------------------------------

    def get(self, name: str) -> Role | None:
        return self._roles.get(name)

    def require(self, name: str) -> Role:
        role = self._roles.get(name)
        if role is None:
            raise RbacError(
                f"no role named {name!r} "
                f"(defined: {', '.join(sorted(self._roles)) or 'none'})"
            )
        return role

    def ancestors(self, name: str) -> frozenset[str]:
        self.require(name)
        return self._walk(name, seen=[], depth=0)

    def closure(self, names: Iterable[str]) -> frozenset[str]:
        """These roles plus everything they inherit."""
        found: set[str] = set()
        for name in names:
            if name in self._roles:
                found.add(name)
                found |= self.ancestors(name)
        return frozenset(found)

    def effective_permissions(self, name: str) -> tuple[Permission, ...]:
        """Everything a role grants, own and inherited, denies first.

        Denies are ordered first so that a linear scan finds them before any
        allow. The engine does not depend on the ordering — it checks every rule
        — but a reader scanning the list should meet the refusals first.
        """
        cached = self._resolved.get(name)
        if cached is not None:
            return cached

        role = self.require(name)
        collected: list[Permission] = list(role.permissions)
        for parent in self.ancestors(name):
            collected.extend(self._roles[parent].permissions)

        ordered = tuple(
            sorted(collected, key=lambda p: (p.effect is Effect.ALLOW, -p.specificity))
        )
        self._resolved[name] = ordered
        return ordered

    def required_assurance(self, names: Iterable[str]) -> Assurance:
        """The strongest assurance any of these roles demands to be exercised."""
        levels = [self.require(name).assurance for name in names if name in self._roles]
        return max(levels, default=Assurance.NONE)

    # -- separation of duties ---------------------------------------------

    @property
    def separations(self) -> tuple[Separation, ...]:
        return tuple(self._separations)

    def conflicts(self, held: Iterable[str]) -> tuple[tuple[Separation, frozenset[str]], ...]:
        """Every separation these roles violate, with the offending roles.

        Evaluated over the inheritance *closure*, not the literal assignment:
        holding a role that inherits `approver` is holding `approver`, and a
        check that missed that would be trivially evaded.
        """
        expanded = self.closure(held)
        found: list[tuple[Separation, frozenset[str]]] = []
        for separation in self._separations:
            overlap = separation.violated_by(expanded)
            if overlap:
                found.append((separation, overlap))
        return tuple(found)

    # -- reporting --------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._roles))

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": [role.to_dict() for role in self._roles.values()],
            "separations": [s.to_dict() for s in self._separations],
        }

    def __len__(self) -> int:
        return len(self._roles)

    def __contains__(self, name: object) -> bool:
        return name in self._roles

    def __iter__(self) -> Iterator[Role]:
        return iter(tuple(self._roles.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<RoleGraph {len(self._roles)} roles, {len(self._separations)} separations>"
