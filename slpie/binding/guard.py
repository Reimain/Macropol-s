"""The live-target guard — the one place a dangerous action is stopped.

Two rules, and both are enforced here rather than in the interface, because a
guard that lives in the UI is one an API call, a script or an agent walks
straight past:

1. **Binding to a live target requires explicit confirmation.** Not a default,
   not an inferred intent, not "the config said live so presumably they meant
   it". A flag somebody had to set.
2. **Live is read-only unless writing was separately granted.** Confirming that
   you want to look at production is not the same as agreeing that the platform
   may change it.

The guard records what it allowed as well as what it refused. "Nobody ever
confirmed a live binding" and "the audit log has no record of it" are different
statements, and only the first is worth anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..environment.manifest import Target
from ..errors import TargetRefused
from .target import TargetSelection

#: Capabilities that change the target rather than observe it.
WRITE_CAPABILITIES = frozenset({
    "write", "delete", "upgrade", "pin", "apply", "deploy", "execute",
})


@dataclass(slots=True)
class Guard:
    """Gatekeeper for anything that reaches the real environment."""

    selection: TargetSelection = field(default_factory=TargetSelection)
    writes_granted: bool = False
    allowed: list[tuple[str, str]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)

    def check_binding(self, element: str) -> Target:
        """Decide an element's target, refusing an unconfirmed live binding."""
        target = self.selection.for_element(element)
        if target is Target.LIVE and not self.selection.confirmed:
            self._refuse(
                f"bind {element}",
                "binding to a live environment requires explicit confirmation",
            )
        self.allowed.append((f"bind {element}", target.value))
        return target

    def check_capability(self, element: str, capability: str) -> None:
        """Refuse a write against a live target unless writing was granted."""
        target = self.selection.for_element(element)
        if capability in WRITE_CAPABILITIES and target is Target.LIVE and not self.writes_granted:
            self._refuse(
                f"{capability} on {element}",
                "SLPIE is read-only against a live target; writing needs a separate grant",
            )
        self.allowed.append((f"{capability} on {element}", target.value))

    def grant_writes(self, *, reason: str) -> None:
        """Permit writes to the live environment. Deliberately explicit.

        Takes a reason because the grant is recorded, and a grant nobody can
        explain later is one nobody should have made.
        """
        if not reason:
            raise ValueError("granting write access to a live target requires a reason")
        self.writes_granted = True
        self.allowed.append(("grant writes", reason))

    def _refuse(self, operation: str, reason: str) -> None:
        self.refused.append((operation, reason))
        raise TargetRefused(operation, reason)

    @property
    def clean(self) -> bool:
        return not self.refused

    def audit(self) -> dict[str, Any]:
        """What the guard permitted and blocked — rendered in the Simulator view."""
        return {
            "selection": self.selection.to_dict(),
            "writes_granted": self.writes_granted,
            "allowed": [{"operation": o, "target": t} for o, t in self.allowed],
            "refused": [{"operation": o, "reason": r} for o, r in self.refused],
        }
