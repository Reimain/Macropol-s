"""What a user may see, and where it lives.

A dataset is not a file path — it is a **grant**: a name, a scope it belongs to,
a visibility, and a storage tier. The path is derived from the grant, never the
other way round, and that ordering is the whole security property. Code that
started from a path and then asked whether the caller was allowed to read it has
already leaked the path's existence, and usually leaks the parent listing too.

Two tiers, because the two kinds of data have opposite requirements:

=============  =========================================================
`Tier.WORK`    per-user working data. Small, mutable, private by default.
`Tier.SHARED`  corpora many users read. Large, immutable, explicitly
               granted.
=============  =========================================================

The key rule for `SHARED`: a shared dataset is **read-only through the grant**.
Two users reading one corpus must not be able to reach each other through it, and
the cheapest way to guarantee that is to refuse writes rather than to police them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import SlpieError
from ..rbac import Scope


class DatasetError(SlpieError):
    """A dataset grant is malformed, or names something it must not."""


#: Names that become path segments. Anything outside this cannot be a name,
#: because a name is concatenated into a storage key and `..` in a key is a way
#: out of a tenant's prefix. Refused rather than sanitised — a sanitiser turns a
#: hostile name into a valid one nobody chose.
NAME = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,61}[a-z0-9])?$")


class Tier(str, Enum):
    """Where a dataset lives, and therefore what it costs to reach."""

    WORK = "work"        # per-user, mutable, filesystem-backed
    SHARED = "shared"    # many-reader corpora, immutable, object-store-backed

    @property
    def writable(self) -> bool:
        """Whether a workspace may write here.

        `SHARED` is false so that two tenants reading one corpus cannot reach
        each other through it. Refusing the write is cheaper and more certain
        than auditing it afterwards.
        """
        return self is Tier.WORK


class Visibility(str, Enum):
    """Who a grant reaches, within the scope that owns it."""

    PRIVATE = "private"    # the one principal named on the grant
    REALM = "realm"        # everybody in the realm
    TENANT = "tenant"      # everybody in the tenant
    PUBLIC = "public"      # every tenant — a published corpus


@dataclass(frozen=True, slots=True)
class Dataset:
    """A named body of data, owned by a scope."""

    name: str
    scope: Scope
    tier: Tier = Tier.WORK
    description: str = ""
    bytes: int = 0
    digest: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not NAME.match(self.name):
            raise DatasetError(
                f"{self.name!r} is not a usable dataset name: lowercase letters, "
                f"digits, dots, dashes and underscores, 1 to 63 characters. "
                f"Storage keys are built from this, so it is refused rather than "
                f"sanitised"
            )

    @property
    def key(self) -> str:
        """The storage key. Derived from the grant, never supplied by a caller.

        Tenant first, so a prefix listing can never cross a tenant boundary even
        if something above this forgets to check.
        """
        tenant = self.scope.tenant or "_global"
        realm = self.scope.realm or "_"
        return f"{self.tier.value}/{tenant}/{realm}/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "scope": self.scope.to_dict(),
            "tier": self.tier.value, "description": self.description,
            "bytes": self.bytes, "digest": self.digest, "key": self.key,
            "labels": dict(self.labels),
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.tier.value}, {str(self.scope) or 'global'})"


@dataclass(frozen=True, slots=True)
class DatasetGrant:
    """Permission for a principal or a scope to reach one dataset.

    Held separately from the `Dataset` on purpose. A dataset is a thing that
    exists; a grant is a decision somebody made about it, and decisions are
    revoked, expire, and need an author. Folding them together makes "who
    granted this and when" unanswerable.
    """

    dataset: Dataset
    visibility: Visibility = Visibility.PRIVATE
    principal_urn: str = ""          # required when PRIVATE
    writable: bool = False
    granted_by: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.visibility is Visibility.PRIVATE and not self.principal_urn:
            raise DatasetError(
                f"a private grant on {self.dataset.name!r} names no principal; "
                f"a grant that reaches nobody is a grant nobody meant to make"
            )
        if self.writable and not self.dataset.tier.writable:
            raise DatasetError(
                f"{self.dataset.name!r} is on the {self.dataset.tier.value} tier, "
                f"which is read-only: two tenants reading one corpus must not be "
                f"able to reach each other through it"
            )

    def reaches(self, *, principal_urn: str, scope: Scope) -> bool:
        """Whether this grant admits that principal in that scope.

        Deliberately does not consult the RBAC engine — this is the *grant's*
        own reach, and `ControlPlane.datasets_for` checks it against a role
        decision as well. Two independent conditions, both of which must hold,
        so a bug in either one narrows access rather than widening it.
        """
        if self.visibility is Visibility.PUBLIC:
            return True
        if self.visibility is Visibility.PRIVATE:
            return principal_urn == self.principal_urn
        owner = self.dataset.scope
        if self.visibility is Visibility.TENANT:
            return bool(owner.tenant) and scope.tenant == owner.tenant
        # REALM
        return scope.tenant == owner.tenant and scope.realm == owner.realm

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset.to_dict(),
            "visibility": self.visibility.value,
            "principal_urn": self.principal_urn,
            "writable": self.writable,
            "granted_by": self.granted_by, "reason": self.reason,
        }
