"""API keys, composed onto the one that already exists.

`identity/providers.py:ApiKeyProvider` already gets the hard part right: the
secret is stored as a digest and never in the clear, and a miss still runs a
comparison so the timing of a wrong key looks like the timing of a right one.
None of that is rewritten.

What it does not have is what an API manager needs around it: **listing**
(without revealing anything), **rotation** (a new key while the old one still
works, so a consumer can swap without an outage), and **scope** (which
application a key belongs to). This composes rather than forks — the provider
still authenticates; this knows what the key is for.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..connectors.keyring import GrantStatus
from .errors import ApimError, CredentialRefused


@dataclass(frozen=True, slots=True)
class Credential:
    """One key, as the platform remembers it. Never the secret itself."""

    key_id: str
    application_id: str
    environment: str = "production"      # production | sandbox
    status: GrantStatus = GrantStatus.ACTIVE
    issued_at: float = 0.0
    expires_at: float = 0.0
    rotated_from: str = ""
    label: str = ""
    #: The first eight hex of the digest. Enough to say *which* key somebody is
    #: looking at in a list, and useless for authenticating with.
    digest_prefix: str = ""

    def expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def usable(self, *, now: float | None = None) -> bool:
        return self.status is GrantStatus.ACTIVE and not self.expired(now=now)

    def replacing(self, **changes: Any) -> "Credential":
        """A copy with `changes` applied — a supersession, never an edit."""
        return Credential(**{
            "key_id": self.key_id,
            "application_id": self.application_id,
            "environment": self.environment,
            "status": self.status,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "rotated_from": self.rotated_from,
            "label": self.label,
            "digest_prefix": self.digest_prefix,
            **changes,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "application_id": self.application_id,
            "environment": self.environment,
            "status": self.status.value,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "rotated_from": self.rotated_from,
            "label": self.label,
            "digest_prefix": self.digest_prefix,
        }

    def __str__(self) -> str:
        return f"{self.key_id} ({self.status.value}, {self.environment})"


@dataclass
class CredentialStore:
    """Issue, list, rotate and revoke. Authentication stays with the provider."""

    provider: Any = None
    now: Any = time.time
    _by_id: dict[str, Credential] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.provider is None:
            import secrets

            from ..identity.providers import ApiKeyProvider

            # A fresh random salt per store, never a constant. A hardcoded salt
            # in an open-source project is a salt every deployment shares, which
            # makes a rainbow table worth building once — and this store holds
            # nothing across a restart anyway, so nothing is lost by it. A
            # deployment that wants keys to survive one passes its own provider,
            # with its own salt from wherever it keeps secrets.
            self.provider = ApiKeyProvider(salt=secrets.token_bytes(32))

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Credential]:
        return iter(sorted(self._by_id.values(), key=lambda item: item.key_id))

    def issue(
        self,
        application_id: str,
        *,
        environment: str = "production",
        expires_at: float = 0.0,
        label: str = "",
        rotated_from: str = "",
    ) -> tuple[str, Credential]:
        """`(secret, record)`. The secret is returned once and never again.

        Returning it once is the whole discipline: a store that can show a key a
        second time is a store that can leak every key at once.
        """
        if not application_id:
            raise ApimError("a credential must belong to an application")

        # `ApiKeyProvider.register` returns `(secret, record)` — the secret once,
        # and the record it will authenticate against. Only the secret leaves
        # this method, and only on this call.
        presented, minted = self.provider.register(subject=application_id)
        record = Credential(
            key_id=minted.key_id,
            application_id=application_id,
            environment=environment,
            issued_at=float(self.now()),
            expires_at=expires_at,
            label=label,
            rotated_from=rotated_from,
            digest_prefix=str(getattr(minted, "digest", ""))[:8],
        )
        self._by_id[minted.key_id] = record
        return presented, record

    def rotate(self, key_id: str, *, label: str = "") -> tuple[str, Credential]:
        """A new key, with the old one superseded rather than deleted.

        Superseded, not revoked, and the distinction matters: a consumer that is
        mid-swap is not misbehaving, and the two read differently in an audit.
        """
        held = self._by_id.get(key_id)
        if held is None:
            raise CredentialRefused(f"no credential {key_id!r} to rotate")
        if held.status is not GrantStatus.ACTIVE:
            raise CredentialRefused(
                f"credential {key_id!r} is {held.status.value} and cannot rotate"
            )

        presented, fresh = self.issue(
            held.application_id,
            environment=held.environment,
            expires_at=held.expires_at,
            label=label or held.label,
            rotated_from=key_id,
        )
        self._by_id[key_id] = held.replacing(status=GrantStatus.SUPERSEDED)
        return presented, fresh

    def revoke(self, key_id: str, *, reason: str) -> Credential:
        if not reason.strip():
            raise ApimError("revoking a credential needs a reason")
        held = self._by_id.get(key_id)
        if held is None:
            raise CredentialRefused(f"no credential {key_id!r}")
        revoked = held.replacing(
            status=GrantStatus.REVOKED,
            label=f"{held.label} (revoked: {reason})".strip(),
        )
        self._by_id[key_id] = revoked
        if hasattr(self.provider, "revoke"):
            self.provider.revoke(key_id)
        return revoked

    def of(self, application_id: str) -> tuple[Credential, ...]:
        return tuple(item for item in self if item.application_id == application_id)

    def get(self, key_id: str) -> Credential | None:
        return self._by_id.get(key_id)

    def authenticate(
        self, secret: str, *, now: float | None = None,
    ) -> tuple[Any, Credential]:
        """`(principal, credential)`.

        The provider decides the secret is genuine; this decides it is still
        live. Two questions, kept apart — a key can be cryptographically correct
        and revoked, and conflating them produces a store where revocation
        depends on the provider remembering to forget something.
        """
        try:
            principal = self.provider.authenticate(secret)
        except Exception as error:  # noqa: BLE001 - the provider's own refusal
            # Translated so a caller routes on one type. Revocation happens in
            # both places on purpose — the provider forgets the key, so it is
            # dead even to a caller that bypasses this store — and without this
            # the same event surfaces as two unrelated exception classes
            # depending on which layer noticed first.
            raise CredentialRefused(str(error)) from error

        held = self._by_id.get(_key_id(secret))
        if held is None:
            raise CredentialRefused("this key is not known to the API manager")
        if not held.usable(now=now):
            raise CredentialRefused(
                f"this key is {held.status.value}"
                + (" and expired" if held.expired(now=now) else "")
            )
        return principal, held

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "credentials": len(self._by_id),
            "live": sum(1 for item in self if item.usable(now=now)),
            "by_environment": {
                environment: sum(1 for item in self if item.environment == environment)
                for environment in sorted({item.environment for item in self})
            },
        }


def _key_id(secret: str) -> str:
    """`slpie_{key_id}_{secret}` — the shape `identity/providers.py` mints."""
    parts = str(secret).split("_")
    return parts[1] if len(parts) >= 3 else str(secret)[:12]
