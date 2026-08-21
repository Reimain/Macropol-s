"""The station registry — attach, track, hand over, detach.

Elements attach the way handsets attach to a base station: they announce
themselves, negotiate what they will allow, and stay tracked until they leave.
The analogy is load-bearing rather than decorative, and the part that matters is
**handover**: an element that moves is still the same element. A repository
relocated, a service redeployed to a new cluster — identity survives, and the
history stays attached to it. Re-attaching a moved element under a new identity
would silently split its history in two.

Every state change goes through the command bus, so the registry itself holds no
truth. What it holds is the *live* view, projected from the ledger.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..core.commands import (
    AttachElement,
    DetachElement,
    HandOverElement,
    NegotiateCapability,
    RecordHeartbeat,
)
from ..domain.finding import Gap, GapKind, merge_gaps
from ..environment.manifest import Declaration, Manifest
from ..errors import StationError
from .capability import Capability, Negotiation, negotiate

#: How long an attached element may stay silent before its data is suspect.
#: Deliberately generous — a false "stale" is worse than a slightly old fact,
#: because it makes every answer carry a gap nobody should act on.
STALE_AFTER_NS = 15 * 60 * 1_000_000_000


@dataclass(slots=True)
class Registration:
    """One attached element, as the station tracks it."""

    element: str
    kind: str = ""
    location: str = ""
    attached_at: int = 0
    last_heartbeat: int = 0
    fingerprint: str = ""
    negotiation: Negotiation | None = None
    handovers: tuple[tuple[str, str], ...] = ()
    attached: bool = True

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.negotiation.names if self.negotiation else ()

    def allows(self, capability: str) -> bool:
        return bool(self.negotiation and self.negotiation.allows(capability))

    def stale(self, *, now: int, threshold: int = STALE_AFTER_NS) -> bool:
        if not self.attached or not self.last_heartbeat:
            return False
        return (now - self.last_heartbeat) > threshold

    def gaps(self, *, now: int = 0) -> tuple[Gap, ...]:
        """Everything about this registration that limits an answer."""
        found: list[Gap] = list(self.negotiation.gaps()) if self.negotiation else []
        if not self.attached:
            found.append(Gap(
                kind=GapKind.NOT_ATTACHED,
                subject=self.element,
                detail=f"{self.element} is declared but not currently attached",
                remediation=f"run `slpie attach` for {self.element}",
                confidence_impact=0.5,
            ))
        elif now and self.stale(now=now):
            silence = (now - self.last_heartbeat) // 1_000_000_000
            found.append(Gap(
                kind=GapKind.STALE_HEARTBEAT,
                subject=self.element,
                detail=f"{self.element} has not reported for {silence}s",
                remediation=f"check that {self.element} is reachable",
                confidence_impact=0.2,
            ))
        return tuple(found)

    def to_dict(self, *, now: int = 0) -> dict[str, Any]:
        return {
            "element": self.element,
            "kind": self.kind,
            "location": self.location,
            "attached": self.attached,
            "attached_at": self.attached_at,
            "last_heartbeat": self.last_heartbeat,
            "stale": self.stale(now=now) if now else False,
            "capabilities": list(self.capabilities),
            "refused": [
                capability.to_dict()
                for capability in (self.negotiation.refused if self.negotiation else ())
            ],
            "handovers": [{"from": a, "to": b} for a, b in self.handovers],
        }

    def __str__(self) -> str:
        state = "attached" if self.attached else "detached"
        return f"{self.element} ({state}, {len(self.capabilities)} capabilities)"


class Station:
    """Attaches declared elements and keeps them tracked.

    Holds a command bus and a resolver. Attachment is an *event*, so the station
    is rebuildable like everything else — this class is the operation, the
    StationProjection is the state.
    """

    def __init__(self, commands: Any, resolver: Any, *, clock: Any = None) -> None:
        self.commands = commands
        self.resolver = resolver
        self._clock = clock
        self._registrations: dict[str, Registration] = {}

    def now(self) -> int:
        return self._clock.now() if self._clock is not None else time.time_ns()

    # -- attach ----------------------------------------------------------

    def attach(
        self,
        declaration: Declaration,
        *,
        wanted: Iterable[str] = (),
        actor: str = "system",
    ) -> Registration:
        """Register an element and negotiate what it will let us see."""
        connector = self.resolver.connector_for(declaration.name)
        result = negotiate(declaration.name, connector, wanted=wanted)

        self.commands.dispatch(AttachElement(
            element=declaration.name,
            kind=declaration.kind.value,
            capabilities=result.names,
            properties={
                "location": declaration.location,
                "node_kind": declaration.node_kind.value,
            },
            actor=actor,
        ))
        # Refusals are dispatched too. A refusal the ledger never saw cannot
        # become a gap, and a gap nobody recorded is indistinguishable from a
        # question that was answered.
        for capability in result.refused:
            self.commands.dispatch(NegotiateCapability(
                element=declaration.name,
                capability=capability.name,
                granted=False,
                reason=capability.reason,
                actor=actor,
            ))

        registration = Registration(
            element=declaration.name,
            kind=declaration.kind.value,
            location=declaration.location,
            attached_at=self.now(),
            last_heartbeat=self.now(),
            negotiation=result,
        )
        self._registrations[declaration.name] = registration
        return registration

    def attach_all(
        self, manifest: Manifest, *, wanted: Iterable[str] = (), actor: str = "system"
    ) -> tuple[Registration, ...]:
        return tuple(
            self.attach(declaration, wanted=wanted, actor=actor)
            for declaration in manifest
        )

    # -- track -----------------------------------------------------------

    def heartbeat(self, element: str, *, fingerprint: str = "") -> Registration:
        registration = self._require(element)
        registration.last_heartbeat = self.now()
        registration.fingerprint = fingerprint or registration.fingerprint
        self.commands.dispatch(RecordHeartbeat(element=element, fingerprint=fingerprint))
        return registration

    def handover(self, element: str, *, to: str, actor: str = "system") -> Registration:
        """Move an element without changing what it is.

        The identity is preserved on purpose. A relocated repository that
        re-attached under a new name would split its history in two, and every
        question about "how long has this been here" would answer wrongly.
        """
        registration = self._require(element)
        previous = registration.location
        registration.handovers = (*registration.handovers, (previous, to))
        registration.location = to
        self.commands.dispatch(HandOverElement(
            element=element, from_location=previous, to_location=to, actor=actor,
        ))
        return registration

    def detach(self, element: str, *, reason: str = "", actor: str = "system") -> Registration:
        registration = self._require(element)
        registration.attached = False
        self.commands.dispatch(DetachElement(element=element, reason=reason, actor=actor))
        return registration

    def _require(self, element: str) -> Registration:
        registration = self._registrations.get(element)
        if registration is None:
            raise StationError(f"{element} is not attached")
        return registration

    # -- reading ---------------------------------------------------------

    def registration(self, element: str) -> Registration | None:
        return self._registrations.get(element)

    @property
    def registrations(self) -> tuple[Registration, ...]:
        return tuple(self._registrations.values())

    @property
    def attached(self) -> tuple[Registration, ...]:
        return tuple(r for r in self._registrations.values() if r.attached)

    def with_capability(self, capability: str) -> tuple[Registration, ...]:
        """Which attached elements can answer a given kind of question."""
        return tuple(
            registration for registration in self.attached
            if registration.allows(capability)
        )

    def gaps(self) -> tuple[Gap, ...]:
        """Every gap the station knows about, deduplicated.

        This is what gets attached to an answer. It is computed rather than
        accumulated, so a capability granted after an earlier refusal stops
        producing a gap without anyone having to remember to clear it.
        """
        now = self.now()
        return merge_gaps(*(
            registration.gaps(now=now) for registration in self._registrations.values()
        ))

    def refusals(self) -> tuple[tuple[str, str, str], ...]:
        """(element, capability, reason) for everything withheld."""
        return tuple(
            (registration.element, capability.name, capability.reason)
            for registration in self._registrations.values()
            if registration.negotiation
            for capability in registration.negotiation.refused
        )

    def coverage(self) -> float:
        """The fraction of attached elements that granted everything asked."""
        if not self._registrations:
            return 0.0
        complete = sum(
            1 for registration in self._registrations.values()
            if registration.negotiation and registration.negotiation.complete
        )
        return round(complete / len(self._registrations), 4)

    def to_dict(self) -> dict[str, Any]:
        now = self.now()
        return {
            "elements": [r.to_dict(now=now) for r in self._registrations.values()],
            "attached": len(self.attached),
            "coverage": self.coverage(),
            "gaps": [gap.to_dict() for gap in self.gaps()],
            "refusals": [
                {"element": e, "capability": c, "reason": r}
                for e, c, r in self.refusals()
            ],
        }

    def __len__(self) -> int:
        return len(self._registrations)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Station {len(self.attached)}/{len(self._registrations)} attached>"
