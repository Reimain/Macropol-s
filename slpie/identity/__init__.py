"""Who is calling, proved — with nothing installed.

The kernel verifies real Google and Apple ID tokens, magic links, one-time
codes and API keys using the standard library alone. RSA *verification* is one
modular exponentiation, and `pow()` is native, so the usual `cryptography`
dependency buys nothing here except a compiled artefact to keep patched.

```
credential ─→ Provider ─→ Principal ─→ Keyring ─→ connectors light up
```

Reading order: `principal` (who, how, and how well we know it), `jwt` (the
verification arithmetic and the attacks it refuses), `providers` (Google, Apple,
any OIDC issuer, email, phone, API key).
"""

from __future__ import annotations

from .jwt import (
    ClaimRejected,
    JwtError,
    Key,
    SignatureInvalid,
    Token,
    TokenExpired,
    parse,
    verify,
)
from .principal import (
    Assurance,
    AuthMethod,
    IdentityError,
    Principal,
    PrincipalKind,
)
from .providers import (
    APPLE_ISSUER,
    GOOGLE_ISSUER,
    ApiKeyProvider,
    ApiKeyRecord,
    Challenge,
    ClaimMap,
    EmailLinkProvider,
    OidcProvider,
    PhoneOtpProvider,
    Provider,
    ProviderRegistry,
    apple,
    custom,
    google,
    microsoft,
)

__all__ = [
    "Principal", "PrincipalKind", "AuthMethod", "Assurance", "IdentityError",
    "Key", "Token", "parse", "verify",
    "JwtError", "SignatureInvalid", "TokenExpired", "ClaimRejected",
    "Provider", "ProviderRegistry", "OidcProvider", "ClaimMap",
    "google", "apple", "microsoft", "custom",
    "GOOGLE_ISSUER", "APPLE_ISSUER",
    "EmailLinkProvider", "PhoneOtpProvider", "Challenge",
    "ApiKeyProvider", "ApiKeyRecord",
]
