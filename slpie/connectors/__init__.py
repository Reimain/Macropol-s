"""Connectors that activate when authentication arrives.

A principal signs in; every connector that sign-in unlocks becomes available at
once, with its capabilities registered and its consent recorded. No config file
is edited, nothing restarts, and the operator never learns what an ID token is.

```
sign in ──→ Keyring.offer()   what this identity would unlock, and why not
        ──→ Keyring.grant()   consent, appended to the log
                  └─────────→ capabilities available platform-wide
```

Two properties are load-bearing:

* **The log is the truth.** Grants are appended and superseded, never updated,
  so "who had access on the third of March, and who authorised it?" is a query
  rather than an archaeology exercise.
* **No secret lives here.** A grant holds a reference into the secret backend
  and a digest to detect rotation. A keyring that also held the tokens would be
  the most valuable thing in the deployment to steal.
"""

from __future__ import annotations

from .catalogue import builtin_catalogue
from .keyring import (
    DEFAULT_GRANT_TTL,
    RENEWAL_WINDOW,
    Activation,
    Grant,
    GrantStatus,
    Keyring,
    KeyringEvent,
    Offer,
)
from .spec import (
    ConnectorCatalogue,
    ConnectorError,
    ConnectorKind,
    ConnectorSpec,
    Scope,
)

__all__ = [
    "ConnectorSpec", "ConnectorKind", "ConnectorCatalogue", "Scope", "ConnectorError",
    "Keyring", "Grant", "GrantStatus", "KeyringEvent", "Offer", "Activation",
    "DEFAULT_GRANT_TTL", "RENEWAL_WINDOW",
    "builtin_catalogue",
]
