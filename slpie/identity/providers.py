"""Sign-in providers. Google, Apple, any OIDC issuer, email, phone, API key.

All of them satisfy one protocol and return one type, because the thing that
consumes an identity — the connector keyring — must not care how it was
obtained. Adding an enterprise Okta tenant is then a configuration entry, not a
code change, which is the whole point of putting the seam here.

```
credential ──→ Provider.authenticate() ──→ Principal ──→ Keyring
   (id token, magic link, OTP, API key)      (who, how, how well)
```

**Nothing here reaches the network.** A JWKS document is *supplied* to the
provider, refreshed by whatever the caller wants to refresh it with. That keeps
the kernel offline-capable and, more usefully, makes every one of these tests
run against real signatures with no fixture server. The ring-1 adapter that
fetches and caches JWKS is a thin loop around `OidcProvider.with_keys`.

**Email and phone are first-class, and honestly ranked.** They are how most
people actually sign in, so refusing them would be posturing — but they prove
control of a channel, not identity, so they carry `Assurance.WEAK` and a
connector that needs more will say so. Both are HMAC-derived and stateless: no
table of outstanding codes, nothing to leak, and a challenge that cannot be
enumerated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .jwt import JwtError, Key, b64url_decode, b64url_encode, verify
from .principal import (
    Assurance,
    AuthMethod,
    IdentityError,
    Principal,
    PrincipalKind,
)

#: Issuers we know the quirks of. A custom OIDC provider supplies its own.
GOOGLE_ISSUER = "https://accounts.google.com"
APPLE_ISSUER = "https://appleid.apple.com"
MICROSOFT_ISSUER = "https://login.microsoftonline.com/{tenant}/v2.0"

#: How long a magic link or an OTP stays usable. Short, because the whole
#: security of a possession factor is that the window is small.
LINK_TTL = 900.0     # 15 minutes
OTP_TTL = 300.0      # 5 minutes

#: Digits in a one-time code. Six is the universal convention; the rate limit,
#: not the length, is what makes it safe.
OTP_DIGITS = 6

#: Attempts allowed against one challenge before it is burned. Without this a
#: six-digit code falls to a million guesses, which is seconds of scripting.
OTP_MAX_ATTEMPTS = 5


@runtime_checkable
class Provider(Protocol):
    """One way of proving who you are."""

    name: str
    method: AuthMethod

    def authenticate(self, credential: str, **context: Any) -> Principal:
        """Turn a credential into a principal, or raise. Never returns None."""
        ...


# --- OpenID Connect ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimMap:
    """Where each provider hides the same information.

    Every issuer spells these differently and several change over time — this is
    the one place that knowledge lives, so supporting a new IdP is a `ClaimMap`
    rather than a branch in the verifier.
    """

    subject: str = "sub"
    email: str = "email"
    email_verified: str = "email_verified"
    phone: str = "phone_number"
    phone_verified: str = "phone_number_verified"
    display: str = "name"
    tenant: str = ""
    amr: str = "amr"                  # authentication methods, if asserted


def _truthy(value: Any) -> bool:
    """Apple sends `email_verified` as the *string* `"true"`.

    A plain `bool(value)` would then be true for the string `"false"` as well,
    which is worse than not checking at all — so the string forms are handled
    explicitly and anything unrecognised is false.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return bool(value)
    return False


class OidcProvider:
    """Verifies an OpenID Connect ID token. Google and Apple are instances of it."""

    def __init__(
        self,
        *,
        name: str,
        issuer: str,
        audience: str,
        keys: Iterable[Key] = (),
        method: AuthMethod = AuthMethod.OIDC,
        claim_map: ClaimMap | None = None,
        leeway: float = 60.0,
        require_verified_email: bool = False,
        mfa_values: Sequence[str] = ("mfa", "hwk", "otp", "swk"),
    ) -> None:
        if not issuer:
            raise IdentityError("an OIDC provider must name its issuer")
        if not audience:
            raise IdentityError(
                f"provider {name!r} has no audience; without it any token minted "
                f"by {issuer} for any application would be accepted here"
            )
        self.name = name
        self.issuer = issuer
        self.audience = audience
        self.method = method
        self.claim_map = claim_map or ClaimMap()
        self.leeway = leeway
        self.require_verified_email = require_verified_email
        self.mfa_values = tuple(mfa_values)
        self._keys: tuple[Key, ...] = tuple(keys)

    @property
    def keys(self) -> tuple[Key, ...]:
        return self._keys

    def with_keys(self, keys: Iterable[Key]) -> "OidcProvider":
        """A copy carrying a refreshed key set — how rotation is handled.

        Returning a new provider rather than mutating means an in-flight request
        keeps the key set it started with, instead of losing it mid-verification
        to a background refresh.
        """
        clone = OidcProvider(
            name=self.name, issuer=self.issuer, audience=self.audience,
            method=self.method, claim_map=self.claim_map, leeway=self.leeway,
            require_verified_email=self.require_verified_email,
            mfa_values=self.mfa_values,
        )
        clone._keys = tuple(keys)
        return clone

    @classmethod
    def from_jwks(cls, document: Mapping[str, Any], **settings: Any) -> "OidcProvider":
        """Build from a JWKS document exactly as an issuer publishes it.

        Keys the verifier cannot handle — EC keys, encryption keys — are skipped
        rather than fatal: Google publishes a mixed set, and refusing the whole
        document because one entry is ES256 would break sign-in entirely.
        """
        keys: list[Key] = []
        for jwk in document.get("keys", ()):
            if not isinstance(jwk, Mapping):
                continue
            if str(jwk.get("use") or "sig") != "sig":
                continue
            try:
                keys.append(Key.from_jwk(jwk, issuer=settings.get("issuer", "")))
            except JwtError:
                continue
        provider = cls(**settings)
        provider._keys = tuple(keys)
        return provider

    def authenticate(self, credential: str, **context: Any) -> Principal:
        nonce = str(context.get("nonce") or "")
        now = context.get("now")

        try:
            token = verify(
                credential, self._keys,
                issuer=self.issuer, audience=self.audience, nonce=nonce,
                now=now, leeway=self.leeway,
            )
        except JwtError as exc:
            raise IdentityError(f"{self.name} sign-in failed: {exc}") from exc

        claims = token.payload
        mapping = self.claim_map
        email = str(claims.get(mapping.email) or "")
        verified = _truthy(claims.get(mapping.email_verified))

        if self.require_verified_email and email and not verified:
            raise IdentityError(
                f"{self.name} asserts {email} but has not verified it; an "
                f"unverified address is not an identity"
            )

        return Principal(
            issuer=self.issuer,
            subject=str(claims.get(mapping.subject) or ""),
            kind=PrincipalKind.HUMAN,
            method=self.method,
            assurance=self._assurance(claims),
            email=email,
            email_verified=verified,
            phone=str(claims.get(mapping.phone) or ""),
            phone_verified=_truthy(claims.get(mapping.phone_verified)),
            display=str(claims.get(mapping.display) or ""),
            tenant=str(claims.get(mapping.tenant) or "") if mapping.tenant else "",
            authenticated_at=float(claims.get("auth_time") or claims.get("iat") or 0.0),
            expires_at=float(claims.get("exp") or 0.0),
            claims=dict(claims),
        )

    def _assurance(self, claims: Mapping[str, Any]) -> Assurance:
        """Raise the baseline only when the issuer actually asserts MFA.

        `amr` is the standard place for it. Inferring MFA from anything else —
        a long session, a trusted device cookie — would be inventing a security
        property the issuer never claimed.
        """
        asserted = claims.get(self.claim_map.amr)
        values = (
            {str(entry).lower() for entry in asserted}
            if isinstance(asserted, (list, tuple)) else set()
        )
        if values & {value.lower() for value in self.mfa_values}:
            return Assurance.STRONG
        return self.method.baseline_assurance

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<OidcProvider {self.name} {self.issuer} keys={len(self._keys)}>"


def google(*, audience: str, keys: Iterable[Key] = ()) -> OidcProvider:
    """Google Sign-In. Verified email is required — Google always asserts it."""
    return OidcProvider(
        name="google", issuer=GOOGLE_ISSUER, audience=audience, keys=keys,
        method=AuthMethod.GOOGLE, require_verified_email=True,
    )


def apple(*, audience: str, keys: Iterable[Key] = ()) -> OidcProvider:
    """Sign in with Apple.

    Two Apple-specific facts are handled rather than discovered in production:
    `email_verified` arrives as the string `"true"` (see :func:`_truthy`), and
    the email claim is present only on the *first* authorisation — afterwards
    the token carries `sub` alone. So verified email is not required here, and
    the principal is keyed on `sub`, which is exactly why identity is keyed on
    `(issuer, subject)` and never on an address.
    """
    return OidcProvider(
        name="apple", issuer=APPLE_ISSUER, audience=audience, keys=keys,
        method=AuthMethod.APPLE, require_verified_email=False,
    )


def microsoft(*, tenant: str, audience: str, keys: Iterable[Key] = ()) -> OidcProvider:
    """Microsoft Entra ID. The issuer is per-tenant, which is the usual trap."""
    return OidcProvider(
        name="microsoft", issuer=MICROSOFT_ISSUER.format(tenant=tenant),
        audience=audience, keys=keys, method=AuthMethod.MICROSOFT,
        claim_map=ClaimMap(email="preferred_username", tenant="tid"),
    )


def custom(*, name: str, issuer: str, audience: str, keys: Iterable[Key] = (),
           claim_map: ClaimMap | None = None, **settings: Any) -> OidcProvider:
    """Any other OIDC issuer — Okta, Auth0, Keycloak, an internal IdP.

    This is the seam that matters commercially: onboarding an enterprise's own
    identity provider is a call to this function, not a pull request.
    """
    return OidcProvider(
        name=name, issuer=issuer, audience=audience, keys=keys,
        method=AuthMethod.OIDC, claim_map=claim_map, **settings,
    )


# --- passwordless: the same philosophy, honestly ranked ------------------


@dataclass(frozen=True, slots=True)
class Challenge:
    """An issued but unredeemed proof-of-possession."""

    identifier: str        # the email address or phone number
    token: str             # what gets sent to it
    expires_at: float
    method: AuthMethod

    @property
    def display(self) -> str:
        return self.token if self.method is AuthMethod.PHONE_OTP else "<link token>"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier, "method": self.method.value,
            "expires_at": self.expires_at,
        }


class _HmacChallenger:
    """Shared machinery: stateless, signed, time-boxed challenges.

    Stateless is the design decision worth defending. The obvious build keeps a
    table of outstanding codes; that table is a store of live credentials, it
    needs expiry sweeping, and it does not survive a restart or exist in more
    than one process. Deriving the code from `HMAC(secret, identifier ‖ window)`
    instead means there is nothing to store, nothing to leak, and any replica
    can verify — at the cost of needing an explicit attempt counter, which is
    below.
    """

    def __init__(self, secret: bytes, *, ttl: float) -> None:
        if len(secret) < 32:
            raise IdentityError(
                "the challenge secret must be at least 32 bytes; a short secret "
                "makes every code in the system forgeable"
            )
        self._secret = secret
        self.ttl = ttl
        self._attempts: dict[str, int] = {}

    def _mac(self, material: str) -> bytes:
        return hmac.new(self._secret, material.encode("utf-8"), hashlib.sha256).digest()

    def _spend_attempt(self, identifier: str) -> None:
        used = self._attempts.get(identifier, 0) + 1
        self._attempts[identifier] = used
        if used > OTP_MAX_ATTEMPTS:
            raise IdentityError(
                f"too many failed attempts for {identifier}; the challenge is "
                f"burned and a new one must be requested"
            )

    def reset(self, identifier: str) -> None:
        self._attempts.pop(identifier, None)


class EmailLinkProvider(_HmacChallenger):
    """Magic links. The most-used sign-in on earth, treated as what it is."""

    name = "email"
    method = AuthMethod.EMAIL_LINK

    def __init__(self, secret: bytes, *, issuer: str = "urn:slpie:idp:email",
                 ttl: float = LINK_TTL) -> None:
        super().__init__(secret, ttl=ttl)
        self.issuer = issuer

    def issue(self, email: str, *, now: float | None = None) -> Challenge:
        address = _normalise_email(email)
        expires = (time.time() if now is None else now) + self.ttl
        payload = json.dumps(
            {"e": address, "x": expires, "n": secrets.token_urlsafe(9)},
            separators=(",", ":"),
        ).encode()
        body = b64url_encode(payload)
        return Challenge(
            identifier=address,
            token=f"{body}.{b64url_encode(self._mac(body))}",
            expires_at=expires,
            method=self.method,
        )

    def authenticate(self, credential: str, **context: Any) -> Principal:
        now = float(context.get("now") or time.time())
        body, _, signature = credential.partition(".")
        if not body or not signature:
            raise IdentityError("the sign-in link is malformed")

        # Verified before parsing, so a forged payload is never even read.
        if not hmac.compare_digest(b64url_encode(self._mac(body)), signature):
            raise IdentityError("the sign-in link has been altered or was not issued here")

        try:
            payload = json.loads(b64url_decode(body))
        except Exception as exc:  # noqa: BLE001
            raise IdentityError("the sign-in link is malformed") from exc

        address = str(payload.get("e") or "")
        if now >= float(payload.get("x") or 0):
            raise IdentityError(
                f"this sign-in link expired; links are valid for "
                f"{self.ttl / 60:.0f} minutes"
            )

        self.reset(address)
        return Principal(
            issuer=self.issuer, subject=address, kind=PrincipalKind.HUMAN,
            method=self.method, assurance=Assurance.WEAK,
            email=address, email_verified=True, display=address,
            authenticated_at=now, expires_at=now + 86400,
        )


class PhoneOtpProvider(_HmacChallenger):
    """One-time codes by SMS. Weak assurance, stated plainly, and rate limited."""

    name = "phone"
    method = AuthMethod.PHONE_OTP

    def __init__(self, secret: bytes, *, issuer: str = "urn:slpie:idp:phone",
                 ttl: float = OTP_TTL) -> None:
        super().__init__(secret, ttl=ttl)
        self.issuer = issuer

    def _code(self, number: str, window: int) -> str:
        digest = self._mac(f"{number}\x1f{window}")
        value = int.from_bytes(digest[:4], "big") % (10 ** OTP_DIGITS)
        return str(value).zfill(OTP_DIGITS)

    def _window(self, now: float) -> int:
        return int(now // self.ttl)

    def issue(self, phone: str, *, now: float | None = None) -> Challenge:
        number = _normalise_phone(phone)
        moment = time.time() if now is None else now
        self.reset(number)
        return Challenge(
            identifier=number, token=self._code(number, self._window(moment)),
            expires_at=(self._window(moment) + 1) * self.ttl, method=self.method,
        )

    def authenticate(self, credential: str, **context: Any) -> Principal:
        number = _normalise_phone(str(context.get("phone") or ""))
        if not number:
            raise IdentityError("a one-time code must be presented with its phone number")
        now = float(context.get("now") or time.time())
        window = self._window(now)

        # The previous window is accepted too: a code issued at 4:59 must still
        # work at 5:01, or every user near a boundary sees a spurious failure.
        candidates = (self._code(number, window), self._code(number, window - 1))
        supplied = credential.strip().replace(" ", "").replace("-", "")
        if not any(hmac.compare_digest(candidate, supplied) for candidate in candidates):
            self._spend_attempt(number)
            raise IdentityError("that code is not correct or has expired")

        self.reset(number)
        return Principal(
            issuer=self.issuer, subject=number, kind=PrincipalKind.HUMAN,
            method=self.method, assurance=Assurance.WEAK,
            phone=number, phone_verified=True, display=_mask(number),
            authenticated_at=now, expires_at=now + 86400,
        )


# --- machine identity ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    """A registered key, stored as a digest. The key itself is never kept."""

    key_id: str
    digest: str
    subject: str
    kind: PrincipalKind = PrincipalKind.SERVICE
    tenant: str = ""
    on_behalf_of: str = ""
    expires_at: float = 0.0
    label: str = ""


class ApiKeyProvider:
    """How agents and CI authenticate — the non-interactive half of the story.

    Keys are held as salted digests, so the store is not a list of live
    credentials. Comparison is constant-time and, critically, an *unknown* key
    is compared against a decoy digest before failing: without that, an unknown
    key returns measurably faster than a known-but-wrong one, and the timing
    difference enumerates valid key ids.
    """

    name = "api_key"
    method = AuthMethod.API_KEY

    def __init__(self, salt: bytes, *, issuer: str = "urn:slpie:idp:apikey") -> None:
        if len(salt) < 16:
            raise IdentityError("the API-key salt must be at least 16 bytes")
        self._salt = salt
        self.issuer = issuer
        self._records: dict[str, ApiKeyRecord] = {}
        self._decoy = self._digest("decoy-never-matches")

    def _digest(self, secret: str) -> str:
        return hashlib.blake2b(
            secret.encode("utf-8"), key=self._salt, digest_size=32
        ).hexdigest()

    def register(self, subject: str, *, kind: PrincipalKind = PrincipalKind.SERVICE,
                 tenant: str = "", on_behalf_of: str = "", expires_at: float = 0.0,
                 label: str = "") -> tuple[str, ApiKeyRecord]:
        """Mint a key. **Returned once and never recoverable** — only its digest
        is retained, so a compromised key store yields nothing usable."""
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        presented = f"slpie_{key_id}_{secret}"
        record = ApiKeyRecord(
            key_id=key_id, digest=self._digest(presented), subject=subject,
            kind=kind, tenant=tenant, on_behalf_of=on_behalf_of,
            expires_at=expires_at, label=label,
        )
        self._records[key_id] = record
        return presented, record

    def revoke(self, key_id: str) -> bool:
        return self._records.pop(key_id, None) is not None

    def authenticate(self, credential: str, **context: Any) -> Principal:
        now = float(context.get("now") or time.time())
        parts = credential.split("_", 2)
        key_id = parts[1] if len(parts) == 3 and parts[0] == "slpie" else ""
        record = self._records.get(key_id)

        supplied = self._digest(credential)
        expected = record.digest if record is not None else self._decoy
        matched = hmac.compare_digest(supplied, expected)

        if record is None or not matched:
            raise IdentityError("that API key is not recognised")
        if record.expires_at and now >= record.expires_at:
            raise IdentityError(f"API key {key_id} expired")

        return Principal(
            issuer=self.issuer, subject=record.subject, kind=record.kind,
            method=self.method, assurance=Assurance.STANDARD,
            tenant=record.tenant, on_behalf_of=record.on_behalf_of,
            display=record.label or record.subject, authenticated_at=now,
            expires_at=record.expires_at,
        )


# --- the registry --------------------------------------------------------


class ProviderRegistry:
    """Every configured way in, addressable by name.

    A caller says "authenticate this, it came from Google" and gets a
    `Principal`; it never learns what an ID token is. That is what makes adding
    an enterprise IdP a configuration change.
    """

    def __init__(self, *providers: Provider) -> None:
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: Provider) -> Provider:
        if provider.name in self._providers:
            raise IdentityError(f"a provider named {provider.name!r} is already registered")
        self._providers[provider.name] = provider
        return provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def authenticate(self, name: str, credential: str, **context: Any) -> Principal:
        provider = self._providers.get(name)
        if provider is None:
            raise IdentityError(
                f"no sign-in provider named {name!r} is configured "
                f"(available: {', '.join(sorted(self._providers)) or 'none'})"
            )
        return provider.authenticate(credential, **context)

    def methods(self) -> tuple[AuthMethod, ...]:
        return tuple(sorted({p.method for p in self._providers.values()}, key=lambda m: m.value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": sorted(self._providers),
            "methods": [method.value for method in self.methods()],
        }

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    def __iter__(self):
        return iter(tuple(self._providers.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<ProviderRegistry {sorted(self._providers)}>"


def _normalise_email(address: str) -> str:
    cleaned = address.strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise IdentityError(f"{address!r} is not an email address")
    return cleaned


def _normalise_phone(number: str) -> str:
    """E.164-ish: keep the digits and a leading plus, drop the presentation.

    `+1 (555) 010-0000` and `+15550100000` are one number, and treating them as
    two means a user who typed it differently gets a code that never matches.
    """
    cleaned = number.strip()
    if not cleaned:
        return ""
    digits = "".join(character for character in cleaned if character.isdigit())
    if not digits:
        raise IdentityError(f"{number!r} contains no digits")
    return ("+" if cleaned.lstrip().startswith("+") else "") + digits


def _mask(number: str) -> str:
    return "***" + number[-4:] if len(number) >= 4 else "***"
