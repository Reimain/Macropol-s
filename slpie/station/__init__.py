"""The station — elements attach, negotiate, stay tracked, and hand over.

```
attach()   → identity assigned, capabilities negotiated, registration recorded
heartbeat()→ the registration stays live
handover() → the element moved; its identity and history do not
detach()   → registration released, history preserved
```

Refused capabilities are the valuable output. Each becomes a named gap carried
by every answer whose confidence it limits, which is what lets the platform say
"I could not read the payments repository" instead of quietly describing a
smaller ecosystem than the one that exists.
"""

from __future__ import annotations

from .capability import (
    KNOWN_CAPABILITIES,
    Capability,
    Negotiation,
    negotiate,
)
from .registry import STALE_AFTER_NS, Registration, Station

__all__ = [
    "Station", "Registration", "STALE_AFTER_NS",
    "Capability", "Negotiation", "negotiate", "KNOWN_CAPABILITIES",
]
