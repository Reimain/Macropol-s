"""The API manager's own failures, so a caller can route on type.

Every subsystem here raises its own class rather than a builtin, for the reason
`slpie/errors.py` opens with: subsystems route on exception *type*, never on
message text, and a `ValueError` from a throttle is indistinguishable from a
`ValueError` from a parse.
"""

from __future__ import annotations

from ..errors import SlpieError


class ApimError(SlpieError):
    """Anything the API manager refuses."""


class LifecycleRefused(ApimError):
    """A transition the state machine does not have.

    Carries the legal moves, because the useful thing to say to somebody who
    tried to publish a retired API is which states it can actually reach — not
    that this one was wrong.
    """

    def __init__(self, frm: str, to: str, allowed: tuple[str, ...]) -> None:
        self.frm = frm
        self.to = to
        self.allowed = allowed
        moves = ", ".join(allowed) if allowed else "nothing — this state is terminal"
        super().__init__(f"an API in {frm!r} cannot move to {to!r}; it may reach {moves}")


class SubscriptionRefused(ApimError):
    """No live subscription covers this call."""


class ThrottleRefused(ApimError):
    """The tier's window is full."""


class CredentialRefused(ApimError):
    """A key that is absent, expired, superseded or revoked."""
