"""Who is calling — human, agent, or service — and how well we know it.

Three things are kept separate here that are usually collapsed into one, and
collapsing them is what makes an authorisation model impossible to reason about
later:

* **Who** (`Principal`) — a stable identity that survives re-authentication.
  Keyed on `(issuer, subject)`, never on an email address: people change email
  addresses, and an identity that moves when the address does is not an
  identity.
* **How** (`AuthMethod`) — the mechanism that proved it this time.
* **How well** (`Assurance`) — what that mechanism is worth. A hardware key and
  an SMS code both produce "authenticated", and treating them as equal is how a
  SIM swap reaches production. Connectors declare a minimum assurance, so the
  policy is stated once rather than remembered at each call site.

An agent is a first-class principal, not a human's session borrowed. When an
autonomous agent acts, the ledger records the agent — with the human who
delegated to it recorded alongside, so "who did this" and "on whose authority"
are both answerable and are not the same question.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..errors import SlpieError


class IdentityError(SlpieError):
    """An identity could not be established, or was established too weakly."""


class PrincipalKind(str, Enum):
    """What sort of thing is calling."""

    HUMAN = "human"          # a person, through a browser or a terminal
    AGENT = "agent"          # an autonomous agent acting on a delegation
    SERVICE = "service"      # a machine identity: CI, a worker, another system

    @property
    def is_interactive(self) -> bool:
        """Whether a challenge can be shown. Decides which methods are usable."""
        return self is PrincipalKind.HUMAN


class Assurance(int, Enum):
    """How much the authentication is worth. Ordered, and compared as such."""

    NONE = 0
    WEAK = 1        # an emailed link, an SMS code — possession of a channel
    STANDARD = 2    # a federated login: Google, Apple, an enterprise IdP
    STRONG = 3      # federated with MFA asserted, or a client certificate
    HARDWARE = 4    # a hardware-bound key

    def satisfies(self, required: "Assurance") -> bool:
        return self.value >= required.value


class AuthMethod(str, Enum):
    """The mechanism that proved the identity."""

    GOOGLE = "google"
    APPLE = "apple"
    MICROSOFT = "microsoft"
    GITHUB = "github"
    OIDC = "oidc"                    # any other OpenID Connect issuer
    SAML = "saml"
    EMAIL_LINK = "email_link"
    PHONE_OTP = "phone_otp"
    API_KEY = "api_key"
    MTLS = "mtls"
    DELEGATION = "delegation"        # an agent acting for an authenticated human

    @property
    def is_federated(self) -> bool:
        return self in (
            AuthMethod.GOOGLE, AuthMethod.APPLE, AuthMethod.MICROSOFT,
            AuthMethod.GITHUB, AuthMethod.OIDC, AuthMethod.SAML,
        )

    @property
    def is_passwordless(self) -> bool:
        return self in (AuthMethod.EMAIL_LINK, AuthMethod.PHONE_OTP)

    @property
    def baseline_assurance(self) -> Assurance:
        """What this method is worth before any MFA claim raises it.

        Email and phone are `WEAK` deliberately. They prove control of a channel
        at a moment in time, and both channels are routinely taken over — which
        is a reason to rank them honestly, not a reason to refuse them.
        """
        if self in (AuthMethod.EMAIL_LINK, AuthMethod.PHONE_OTP):
            return Assurance.WEAK
        if self is AuthMethod.MTLS:
            return Assurance.STRONG
        if self is AuthMethod.API_KEY:
            return Assurance.STANDARD
        if self is AuthMethod.DELEGATION:
            return Assurance.STANDARD
        return Assurance.STANDARD


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity, and the terms on which we believe it."""

    issuer: str
    subject: str
    kind: PrincipalKind = PrincipalKind.HUMAN
    method: AuthMethod = AuthMethod.OIDC
    assurance: Assurance = Assurance.STANDARD
    email: str = ""
    email_verified: bool = False
    phone: str = ""
    phone_verified: bool = False
    display: str = ""
    tenant: str = ""
    on_behalf_of: str = ""            # the urn of the human who delegated
    authenticated_at: float = 0.0
    expires_at: float = 0.0
    claims: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.issuer.strip():
            raise IdentityError("a principal must name the issuer that vouched for it")
        if not self.subject.strip():
            raise IdentityError("a principal must have a subject")
        if self.kind is PrincipalKind.AGENT and not self.on_behalf_of:
            # An agent with nobody behind it is an unaccountable actor. Refusing
            # it here is cheaper than discovering it in an audit.
            raise IdentityError(
                f"agent {self.subject!r} names no delegating principal; an agent "
                f"must act on someone's authority"
            )
        if not self.authenticated_at:
            object.__setattr__(self, "authenticated_at", time.time())

    @property
    def urn(self) -> str:
        """Stable across re-authentication, email changes and display renames.

        The issuer is included because subjects are only unique within an
        issuer: Google's `1078412` and an internal IdP's `1078412` are different
        people, and a urn that conflated them would be a privilege escalation.
        """
        material = f"{self.issuer}\x1f{self.subject}"
        digest = hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()
        return f"urn:slpie:principal:{self.kind.value}/{digest}"

    @property
    def identity_key(self) -> tuple[str, str]:
        return (self.issuer, self.subject)

    def expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        return (time.time() if now is None else now) >= self.expires_at

    def satisfies(self, required: Assurance) -> bool:
        return self.assurance.satisfies(required)

    def require(self, required: Assurance) -> None:
        if not self.satisfies(required):
            raise IdentityError(
                f"{self.label} authenticated by {self.method.value} at assurance "
                f"{self.assurance.name}, below the required {required.name}; "
                f"re-authenticate with a stronger method"
            )

    @property
    def label(self) -> str:
        """What to put in a log line or an error, without leaking more than needed."""
        return self.display or self.email or f"{self.kind.value}:{self.subject}"

    def delegate(self, agent_subject: str, *, method: AuthMethod | None = None,
                 expires_at: float = 0.0) -> "Principal":
        """Mint an agent principal acting on this principal's authority.

        The agent inherits the delegator's **method and assurance**, and this is
        the part worth getting right. An agent that carried some generic
        "delegation" method would be unable to reach any connector, because
        every connector states which identities unlock it — a Google connector
        is unlocked by a Google identity. Inheriting the method is what makes
        "act on my behalf" mean anything.

        It inherits *no more* than that. The agent still needs its own grant per
        connector, so delegation conveys the authority to be granted access, not
        the access itself, and revoking the agent never touches the human.
        """
        return Principal(
            issuer=self.issuer, subject=agent_subject, kind=PrincipalKind.AGENT,
            method=self.method if method is None else method,
            assurance=self.assurance, tenant=self.tenant,
            on_behalf_of=self.urn, expires_at=expires_at,
            display=f"agent of {self.label}",
        )

    def to_dict(self) -> dict[str, Any]:
        """Redacted by construction: raw claims never cross this boundary."""
        return {
            "urn": self.urn,
            "issuer": self.issuer,
            "subject": self.subject,
            "kind": self.kind.value,
            "method": self.method.value,
            "assurance": self.assurance.name,
            "email": self.email,
            "email_verified": self.email_verified,
            "phone": _mask_phone(self.phone),
            "phone_verified": self.phone_verified,
            "display": self.display,
            "tenant": self.tenant,
            "on_behalf_of": self.on_behalf_of,
            "authenticated_at": self.authenticated_at,
            "expires_at": self.expires_at,
        }

    def __str__(self) -> str:
        return f"{self.label} ({self.method.value}, {self.assurance.name})"


def _mask_phone(number: str) -> str:
    """Last four digits only. A phone number in a log is a phone number leaked."""
    digits = [character for character in number if character.isdigit()]
    if len(digits) < 4:
        return "***" if digits else ""
    return "***" + "".join(digits[-4:])
