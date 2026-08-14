"""The keyring — a ledger of grants, and connectors that light up when auth arrives.

This is the piece that turns a library into a platform. A principal signs in;
every connector that sign-in unlocks becomes available *immediately*, with its
capabilities registered and its scopes recorded. Nothing is edited, nothing
restarts, and the operator never learns what an ID token is.

```
Principal ──→ Keyring.offer()      what would this sign-in unlock?
          ──→ Keyring.grant()      consent, recorded as a ledger event
                    │
                    ├─→ capabilities registered
                    └─→ Grant appended: who, what, which scopes, when, until
```

**Every grant, revocation, rotation and expiry is an append-only ledger event.**
That is not bookkeeping for its own sake. "Who authorised this system to read
that repository, when, and on whose authority?" is the question every security
review opens with, and a keyring that stores current state in a table can only
answer it for the present. Here it is answerable for any point in time, because
the grant was never overwritten — it was superseded.

**Secrets are not stored here.** A grant holds a *reference* to a credential
held by whatever secret backend is configured, plus the digest needed to detect
that it changed. A keyring that also stored the tokens would be the single most
valuable thing to steal in the deployment.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..identity.principal import Assurance, AuthMethod, Principal
from .spec import ConnectorCatalogue, ConnectorError, ConnectorSpec, Scope

#: How long a grant lasts when nobody says. Ninety days is long enough not to
#: be a nuisance and short enough that an abandoned integration lapses rather
#: than living forever.
DEFAULT_GRANT_TTL = 90 * 86400.0

#: Grants inside this window of expiry are reported as `expiring`, so a renewal
#: can happen before something breaks rather than after.
RENEWAL_WINDOW = 7 * 86400.0


class GrantStatus(str, Enum):
    """Where a grant is in its life. Terminal states are never reopened."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"      # replaced by a re-grant, e.g. widened scopes

    @property
    def is_terminal(self) -> bool:
        return self is not GrantStatus.ACTIVE


class KeyringEvent(str, Enum):
    """What gets appended. One per state transition, never an update in place."""

    GRANTED = "connector_granted"
    REVOKED = "connector_revoked"
    EXPIRED = "connector_expired"
    ROTATED = "connector_rotated"
    REFUSED = "connector_refused"


@dataclass(frozen=True, slots=True)
class Grant:
    """One principal's authorisation of one connector, at one moment."""

    connector_id: str
    principal_urn: str
    scopes: tuple[str, ...]
    method: AuthMethod
    assurance: Assurance
    granted_at: float
    expires_at: float = 0.0
    status: GrantStatus = GrantStatus.ACTIVE
    credential_ref: str = ""          # a pointer into the secret backend
    credential_digest: str = ""       # detects rotation without holding the secret
    tenant: str = ""
    on_behalf_of: str = ""
    reason: str = ""                  # why revoked or refused, when it was
    sequence: int = 0

    @property
    def id(self) -> str:
        material = (
            f"{self.connector_id}\x1f{self.principal_urn}\x1f{self.granted_at}"
            f"\x1f{','.join(sorted(self.scopes))}"
        )
        return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()

    def expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (time.time() if now is None else now) >= self.expires_at

    def expiring(self, *, now: float | None = None, window: float = RENEWAL_WINDOW) -> bool:
        if not self.expires_at:
            return False
        moment = time.time() if now is None else now
        return 0 <= self.expires_at - moment <= window

    def usable(self, *, now: float | None = None) -> bool:
        return self.status is GrantStatus.ACTIVE and not self.expired(now=now)

    def covers(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> dict[str, Any]:
        """Safe to render anywhere: there is no secret in a grant to redact."""
        return {
            "id": self.id,
            "connector": self.connector_id,
            "principal": self.principal_urn,
            "scopes": list(self.scopes),
            "method": self.method.value,
            "assurance": self.assurance.name,
            "status": self.status.value,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "credential_ref": self.credential_ref,
            "tenant": self.tenant,
            "on_behalf_of": self.on_behalf_of,
            "reason": self.reason,
            "sequence": self.sequence,
        }

    def __str__(self) -> str:
        return f"{self.connector_id} → {self.principal_urn} ({self.status.value})"


@dataclass(frozen=True, slots=True)
class Offer:
    """What one sign-in would unlock, computed before anybody consents.

    Returned by :meth:`Keyring.offer`, and it is the screen a user sees after
    signing in: here is what you can connect, here is what each will be able to
    do, here is what is already connected.
    """

    spec: ConnectorSpec
    available: bool
    reason: str
    scopes: tuple[Scope, ...] = ()
    already_granted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.spec.id,
            "title": self.spec.title,
            "available": self.available,
            "reason": self.reason,
            "already_granted": self.already_granted,
            "scopes": [scope.to_dict() for scope in self.scopes],
        }

    def __str__(self) -> str:
        mark = "✓" if self.already_granted else ("+" if self.available else "·")
        return f"{mark} {self.spec.title} — {self.reason}"


@dataclass(frozen=True, slots=True)
class Activation:
    """The result of granting: what is now possible that was not before."""

    grant: Grant
    spec: ConnectorSpec
    capabilities: tuple[str, ...]
    degraded: tuple[Scope, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.degraded

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant": self.grant.to_dict(),
            "connector": self.spec.id,
            "capabilities": list(self.capabilities),
            "degraded": [scope.to_dict() for scope in self.degraded],
            "complete": self.complete,
        }

    def __str__(self) -> str:
        if self.complete:
            return f"{self.spec.title}: {len(self.capabilities)} capabilities available"
        missing = ", ".join(scope.name for scope in self.degraded)
        return f"{self.spec.title}: degraded, missing {missing}"


class Keyring:
    """Grants, as an append-only log with a derived view of the present.

    The log is the truth and `_current` is a projection of it — the same
    arrangement as the graph over the ledger, for the same reason: state that
    can be rebuilt from its history can be audited, replicated and repaired,
    and state that cannot only be trusted.
    """

    def __init__(
        self,
        catalogue: ConnectorCatalogue | None = None,
        *,
        default_ttl: float = DEFAULT_GRANT_TTL,
        emit: Callable[[KeyringEvent, Grant], None] | None = None,
    ) -> None:
        self.catalogue = catalogue if catalogue is not None else ConnectorCatalogue()
        self.default_ttl = default_ttl
        self._emit = emit
        self._log: list[Grant] = []
        self._current: dict[tuple[str, str], Grant] = {}
        self._sequence = 0

    # -- offering ---------------------------------------------------------

    def offer(self, principal: Principal, *, now: float | None = None) -> tuple[Offer, ...]:
        """Everything this principal could connect, and why not where they cannot.

        Includes the unavailable ones deliberately. A list showing only what is
        possible answers "what can I do?" but never "why can I not do the thing
        I came here for?", and the second question is the one that generates
        support tickets.
        """
        offers: list[Offer] = []
        for spec in self.catalogue:
            available, reason = spec.unlocked_by(principal.method, principal.assurance)
            existing = self._current.get((spec.id, principal.urn))
            offers.append(Offer(
                spec=spec,
                available=available,
                reason=reason,
                scopes=spec.scopes,
                already_granted=bool(existing and existing.usable(now=now)),
            ))
        return tuple(offers)

    def available(self, principal: Principal) -> tuple[ConnectorSpec, ...]:
        return tuple(offer.spec for offer in self.offer(principal) if offer.available)

    # -- granting ---------------------------------------------------------

    def grant(
        self,
        principal: Principal,
        connector_id: str,
        *,
        scopes: Sequence[str] | None = None,
        credential_ref: str = "",
        credential: str = "",
        ttl: float | None = None,
        now: float | None = None,
    ) -> Activation:
        """Record consent and activate the connector.

        `scopes` defaults to everything the connector declared *required* —
        never to everything it declared, because silently granting optional
        write scopes because they were listed is precisely the consent failure
        this design exists to avoid.
        """
        moment = time.time() if now is None else now
        spec = self.catalogue.require(connector_id)

        allowed, reason = spec.unlocked_by(principal.method, principal.assurance)
        if not allowed:
            refusal = Grant(
                connector_id=connector_id, principal_urn=principal.urn,
                scopes=(), method=principal.method, assurance=principal.assurance,
                granted_at=moment, status=GrantStatus.REVOKED, reason=reason,
                tenant=principal.tenant, on_behalf_of=principal.on_behalf_of,
            )
            self._append(KeyringEvent.REFUSED, refusal)
            raise ConnectorError(reason)

        if principal.expired(now=moment):
            raise ConnectorError(
                f"{principal.label} signed in but that session has expired; "
                f"a grant must be made by a live principal"
            )

        requested = tuple(scopes) if scopes is not None else tuple(
            scope.name for scope in spec.required_scopes
        )
        unknown = set(requested) - {scope.name for scope in spec.scopes}
        if unknown:
            raise ConnectorError(
                f"{spec.title} does not define scope(s) {sorted(unknown)}; "
                f"granting an undeclared scope would mean nothing"
            )

        # A re-grant supersedes rather than mutates, so the widening is visible.
        previous = self._current.get((connector_id, principal.urn))
        if previous is not None and previous.status is GrantStatus.ACTIVE:
            self._append(
                KeyringEvent.ROTATED,
                replace(previous, status=GrantStatus.SUPERSEDED,
                        reason="superseded by a new grant"),
            )

        grant = Grant(
            connector_id=connector_id,
            principal_urn=principal.urn,
            scopes=tuple(sorted(requested)),
            method=principal.method,
            assurance=principal.assurance,
            granted_at=moment,
            expires_at=moment + (self.default_ttl if ttl is None else ttl),
            credential_ref=credential_ref,
            credential_digest=_digest(credential) if credential else "",
            tenant=principal.tenant,
            on_behalf_of=principal.on_behalf_of,
        )
        self._append(KeyringEvent.GRANTED, grant)

        missing = spec.missing_scopes(grant.scopes)
        return Activation(
            grant=grant, spec=spec,
            capabilities=spec.capabilities_for(grant.scopes),
            degraded=missing,
        )

    def revoke(self, principal_urn: str, connector_id: str, *, reason: str,
               now: float | None = None) -> bool:
        """Withdraw a grant. A reason is required and is not decorative.

        Revocations without a stated reason are indistinguishable from mistakes
        six months later, and "why did this integration stop working?" is then
        unanswerable.
        """
        if not reason.strip():
            raise ConnectorError("a revocation must state a reason")
        existing = self._current.get((connector_id, principal_urn))
        if existing is None or existing.status is not GrantStatus.ACTIVE:
            return False
        self._append(
            KeyringEvent.REVOKED,
            replace(
                existing, status=GrantStatus.REVOKED, reason=reason,
                expires_at=time.time() if now is None else now,
            ),
        )
        return True

    def revoke_all(self, principal_urn: str, *, reason: str) -> int:
        """Everything one principal authorised — the offboarding call."""
        revoked = 0
        for (connector_id, urn) in list(self._current):
            if urn == principal_urn and self.revoke(urn, connector_id, reason=reason):
                revoked += 1
        return revoked

    def sweep(self, *, now: float | None = None) -> tuple[Grant, ...]:
        """Move lapsed grants to EXPIRED, appending as it goes.

        Expiry is materialised rather than merely computed so that the lapse is
        an event with a sequence number. "It stopped working on the 14th" should
        be a query, not an inference from an absent row.
        """
        moment = time.time() if now is None else now
        lapsed: list[Grant] = []
        for grant in list(self._current.values()):
            if grant.status is GrantStatus.ACTIVE and grant.expired(now=moment):
                expired = replace(grant, status=GrantStatus.EXPIRED,
                                  reason="the grant reached its expiry")
                self._append(KeyringEvent.EXPIRED, expired)
                lapsed.append(expired)
        return tuple(lapsed)

    # -- reading ----------------------------------------------------------

    def grants_of(self, principal_urn: str, *, now: float | None = None,
                  usable_only: bool = True) -> tuple[Grant, ...]:
        found = [
            grant for (_, urn), grant in self._current.items()
            if urn == principal_urn and (not usable_only or grant.usable(now=now))
        ]
        return tuple(sorted(found, key=lambda grant: grant.connector_id))

    def grant_for(self, principal_urn: str, connector_id: str, *,
                  now: float | None = None) -> Grant | None:
        grant = self._current.get((connector_id, principal_urn))
        return grant if grant is not None and grant.usable(now=now) else None

    def capabilities_of(self, principal_urn: str, *, now: float | None = None
                        ) -> tuple[str, ...]:
        """Everything this principal can currently do, across every connector.

        The single question the rest of the platform asks the keyring. A
        capability absent from this tuple becomes a named gap on any answer that
        would have needed it.
        """
        found: set[str] = set()
        for grant in self.grants_of(principal_urn, now=now):
            spec = self.catalogue.get(grant.connector_id)
            if spec is not None:
                found.update(spec.capabilities_for(grant.scopes))
        return tuple(sorted(found))

    def can(self, principal_urn: str, capability: str, *, now: float | None = None) -> bool:
        return capability in self.capabilities_of(principal_urn, now=now)

    def expiring(self, *, now: float | None = None,
                 window: float = RENEWAL_WINDOW) -> tuple[Grant, ...]:
        return tuple(
            grant for grant in self._current.values()
            if grant.status is GrantStatus.ACTIVE and grant.expiring(now=now, window=window)
        )

    def rotated(self, principal_urn: str, connector_id: str, credential: str) -> bool:
        """Whether the underlying credential changed since it was granted.

        Detected from the digest, which is why the digest is kept even though
        the secret is not: a rotated credential that nobody noticed is a
        connector that will fail at the worst possible moment.
        """
        grant = self._current.get((connector_id, principal_urn))
        if grant is None or not grant.credential_digest:
            return False
        return grant.credential_digest != _digest(credential)

    # -- the log ----------------------------------------------------------

    @property
    def log(self) -> tuple[Grant, ...]:
        """Every transition, in order. The audit answer."""
        return tuple(self._log)

    def history(self, principal_urn: str = "", connector_id: str = "") -> tuple[Grant, ...]:
        return tuple(
            grant for grant in self._log
            if (not principal_urn or grant.principal_urn == principal_urn)
            and (not connector_id or grant.connector_id == connector_id)
        )

    def at(self, sequence: int) -> dict[tuple[str, str], Grant]:
        """The keyring as it stood at a point in the log.

        This is what makes "who had access on the 3rd of March?" answerable —
        the question a breach investigation opens with, and one a table of
        current grants cannot answer at all.
        """
        state: dict[tuple[str, str], Grant] = {}
        for grant in self._log:
            if grant.sequence > sequence:
                break
            state[(grant.connector_id, grant.principal_urn)] = grant
        return state

    def _append(self, event: KeyringEvent, grant: Grant) -> Grant:
        self._sequence += 1
        recorded = replace(grant, sequence=self._sequence)
        self._log.append(recorded)
        if event is not KeyringEvent.REFUSED:
            self._current[(recorded.connector_id, recorded.principal_urn)] = recorded
        if self._emit is not None:
            self._emit(event, recorded)
        return recorded

    # -- reporting --------------------------------------------------------

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        active = [g for g in self._current.values() if g.usable(now=now)]
        return {
            "catalogue": len(self.catalogue),
            "grants": len(self._current),
            "active": len(active),
            "expiring": len(self.expiring(now=now)),
            "events": len(self._log),
            "sequence": self._sequence,
            "capabilities": sorted({
                capability
                for grant in active
                for capability in (
                    self.catalogue.get(grant.connector_id).capabilities_for(grant.scopes)
                    if self.catalogue.get(grant.connector_id) else ()
                )
            }),
        }

    def __len__(self) -> int:
        return len(self._current)

    def __iter__(self) -> Iterator[Grant]:
        return iter(tuple(self._current.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Keyring {len(self._current)} grants, {len(self._log)} events>"


def _digest(secret: str) -> str:
    return hashlib.blake2b(secret.encode("utf-8"), digest_size=16).hexdigest()
