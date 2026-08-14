"""Fault injection — because the interesting cases are the ones that go wrong.

A platform tested only against cooperative elements will confidently report an
ecosystem it could only half see. These faults make the failure modes
reproducible: an element that refuses a capability, one that times out, one that
returns half its data, one that contradicts its own declaration.

Faults wrap a connector rather than replacing it. The wrapped connector is the
real one, doing real work, and the fault decides only whether that work is
allowed to succeed — so a partially-faulty element behaves exactly like a real
partially-broken one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from ..binding.connector import Connector, Resource
from ..errors import BindingError, CapabilityRefused


class FaultKind(str, Enum):
    """What can go wrong with an element. Each maps to a real failure mode."""

    REFUSE_CAPABILITY = "refuse_capability"   # a vendored tree, an ACL, a policy
    TIMEOUT = "timeout"                       # a broker that never answers
    PARTIAL_DATA = "partial_data"             # a truncated listing, a paged API
    CORRUPT_CONTENT = "corrupt_content"       # a half-written lockfile
    UNAVAILABLE = "unavailable"               # the element is simply down
    SLOW = "slow"                             # answers, eventually
    CONTRADICT = "contradict"                 # reality disagrees with the manifest

    @property
    def becomes_gap(self) -> bool:
        """Whether this shows up as a reported gap rather than as a finding."""
        return self in (
            FaultKind.REFUSE_CAPABILITY, FaultKind.TIMEOUT,
            FaultKind.UNAVAILABLE, FaultKind.PARTIAL_DATA,
        )


@dataclass(frozen=True, slots=True)
class Fault:
    """One injected failure, scoped to an element and optionally a capability."""

    kind: FaultKind
    element: str
    capability: str = ""
    reason: str = ""
    fraction: float = 0.5          # for PARTIAL_DATA: how much survives
    delay_seconds: float = 0.0     # for SLOW
    replacement: Mapping[str, Any] = field(default_factory=dict)  # for CONTRADICT

    def applies_to(self, capability: str = "") -> bool:
        return not self.capability or self.capability == capability

    @property
    def explanation(self) -> str:
        return self.reason or _DEFAULT_REASONS[self.kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "element": self.element,
            "capability": self.capability, "reason": self.explanation,
            "fraction": self.fraction,
        }


_DEFAULT_REASONS = {
    FaultKind.REFUSE_CAPABILITY: "the element refused this capability",
    FaultKind.TIMEOUT: "the element did not answer within the timeout",
    FaultKind.PARTIAL_DATA: "the element returned only part of its contents",
    FaultKind.CORRUPT_CONTENT: "the content could not be read as expected",
    FaultKind.UNAVAILABLE: "the element is unreachable",
    FaultKind.SLOW: "the element answered slowly",
    FaultKind.CONTRADICT: "the element contradicts its declaration",
}


class FaultyConnector:
    """A real connector with a fault in front of it.

    Delegates everything it is not asked to break, so an element with one
    refused capability still answers every other one — which is the situation
    that actually arises, and the one a platform is most likely to get wrong.
    """

    def __init__(self, inner: Connector, faults: tuple[Fault, ...]) -> None:
        self._inner = inner
        self._faults = faults
        self.mode = inner.mode
        self.capabilities = tuple(
            capability for capability in inner.capabilities
            if not self._refuses(capability)
        )

    def _refuses(self, capability: str) -> bool:
        return any(
            fault.kind is FaultKind.REFUSE_CAPABILITY and fault.applies_to(capability)
            for fault in self._faults
        )

    def _fault(self, *kinds: FaultKind, capability: str = "") -> Fault | None:
        return next(
            (
                fault for fault in self._faults
                if fault.kind in kinds and fault.applies_to(capability)
            ),
            None,
        )

    def available(self) -> bool:
        if self._fault(FaultKind.UNAVAILABLE, FaultKind.TIMEOUT):
            return False
        return self._inner.available()

    def read(self, uri: str) -> Resource:
        if fault := self._fault(FaultKind.UNAVAILABLE, FaultKind.TIMEOUT, capability="file-read"):
            raise BindingError(f"cannot read {uri}: {fault.explanation}")
        if fault := self._fault(FaultKind.SLOW):
            time.sleep(min(fault.delay_seconds, 0.05))

        resource = self._inner.read(uri)

        if fault := self._fault(FaultKind.CORRUPT_CONTENT):
            # Truncated mid-file, which is what a half-written lockfile looks
            # like — the parser must fail cleanly rather than misread it.
            keep = max(int(len(resource.content) * fault.fraction), 1)
            return Resource(
                uri=resource.uri, content=resource.content[:keep],
                media_type=resource.media_type, size=keep,
                metadata={**dict(resource.metadata), "fault": fault.kind.value},
            )
        if fault := self._fault(FaultKind.CONTRADICT):
            return Resource(
                uri=resource.uri,
                content=_contradicted(resource.content, fault.replacement),
                media_type=resource.media_type,
                metadata={**dict(resource.metadata), "fault": fault.kind.value},
            )
        return resource

    def list(self, uri: str, *, pattern: str = "") -> tuple[str, ...]:
        if fault := self._fault(
            FaultKind.UNAVAILABLE, FaultKind.TIMEOUT, capability="directory-list"
        ):
            raise BindingError(f"cannot list {uri}: {fault.explanation}")

        found = self._inner.list(uri, pattern=pattern)

        if fault := self._fault(FaultKind.PARTIAL_DATA):
            keep = max(int(len(found) * fault.fraction), 1) if found else 0
            return found[:keep]
        return found

    def exists(self, uri: str) -> bool:
        try:
            self.read(uri)
            return True
        except BindingError:
            return False

    def describe(self) -> Mapping[str, Any]:
        return {
            **dict(self._inner.describe()),
            "faults": [fault.to_dict() for fault in self._faults],
            "capabilities": list(self.capabilities),
        }

    def refusals(self) -> tuple[tuple[str, str], ...]:
        """Refused capabilities as (capability, reason) — these become gaps."""
        return tuple(
            (fault.capability or "*", fault.explanation)
            for fault in self._faults
            if fault.kind is FaultKind.REFUSE_CAPABILITY
        )


def _contradicted(content: bytes, replacement: Mapping[str, Any]) -> bytes:
    """Rewrite JSON content so the observation disagrees with the declaration."""
    import json

    if not replacement:
        return content
    try:
        document = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content
    if isinstance(document, dict):
        document.update(replacement)
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


@dataclass(slots=True)
class FaultInjector:
    """Holds the faults a scenario has armed, and applies them at bind time."""

    faults: list[Fault] = field(default_factory=list)

    def inject(self, fault: Fault) -> Fault:
        self.faults.append(fault)
        return fault

    def refuse(self, element: str, capability: str, *, reason: str = "") -> Fault:
        return self.inject(Fault(
            kind=FaultKind.REFUSE_CAPABILITY, element=element,
            capability=capability, reason=reason,
        ))

    def clear(self, element: str = "") -> None:
        self.faults = (
            [] if not element else [f for f in self.faults if f.element != element]
        )

    def for_element(self, element: str) -> tuple[Fault, ...]:
        return tuple(fault for fault in self.faults if fault.element == element)

    def wrap(self, element: str, connector: Connector) -> Connector:
        """Apply this element's faults, or hand the connector back untouched."""
        applicable = self.for_element(element)
        return FaultyConnector(connector, applicable) if applicable else connector

    def to_dict(self) -> dict[str, Any]:
        return {"faults": [fault.to_dict() for fault in self.faults]}

    def __len__(self) -> int:
        return len(self.faults)
