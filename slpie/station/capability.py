"""Capabilities — what an element will let the platform see, and what it won't.

A refusal is the important half. An IoT broker may grant `topic-list` and refuse
`message-inspect`; a vendored tree may grant `lockfile-read` and refuse
`static-analysis`. If the platform swallowed those refusals it would report a
smaller ecosystem than the one that exists, and report it confidently — which is
worse than reporting nothing.

So every refusal is recorded with its reason and becomes a named gap attached to
each answer whose confidence it limits. That is the whole difference between a
low-confidence answer and a misleading one: the platform states what it could
not see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..domain.evidence import EvidenceKind
from ..domain.finding import Gap, GapKind

#: The capability vocabulary. Closed so that a typo in a connector is a
#: registration error rather than a silently ungranted capability.
KNOWN_CAPABILITIES: dict[str, tuple[EvidenceKind, ...]] = {
    "file-read": (EvidenceKind.MANIFEST_DECLARED, EvidenceKind.BUILD_CONFIG),
    "directory-list": (EvidenceKind.MANIFEST_DECLARED,),
    "lockfile-read": (EvidenceKind.LOCKFILE_PIN,),
    "static-analysis": (EvidenceKind.STATIC_IMPORT, EvidenceKind.ANNOTATION),
    "scm-history": (EvidenceKind.MANIFEST_DECLARED,),
    "container-inspect": (EvidenceKind.CONTAINER_MANIFEST,),
    "iac-read": (EvidenceKind.IAC_DECLARATION,),
    "schema-read": (EvidenceKind.MANIFEST_DECLARED,),
    "contract-read": (EvidenceKind.MANIFEST_DECLARED,),
    "topic-list": (EvidenceKind.CONFIG_REFERENCE,),
    "message-inspect": (EvidenceKind.RUNTIME_TRACE,),
    "runtime-trace": (EvidenceKind.RUNTIME_TRACE,),
    "registry-query": (EvidenceKind.MANIFEST_DECLARED,),
    "secret-scan": (EvidenceKind.CONFIG_REFERENCE,),
    "write": (),
}


@dataclass(frozen=True, slots=True)
class Capability:
    """One capability, granted or refused, with the reason when refused."""

    name: str
    granted: bool = False
    reason: str = ""
    evidence_kinds: tuple[EvidenceKind, ...] = ()

    @classmethod
    def grant(cls, name: str) -> "Capability":
        return cls(
            name=name, granted=True,
            evidence_kinds=KNOWN_CAPABILITIES.get(name, ()),
        )

    @classmethod
    def refuse(cls, name: str, reason: str) -> "Capability":
        """Refuse a capability. A reason is required, not optional.

        A refusal without a reason produces a gap that says only "something is
        missing", which tells the operator nothing they can act on.
        """
        return cls(
            name=name, granted=False,
            reason=reason or "no reason given",
            evidence_kinds=KNOWN_CAPABILITIES.get(name, ()),
        )

    @property
    def known(self) -> bool:
        return self.name in KNOWN_CAPABILITIES

    def as_gap(self, element: str) -> Gap:
        """The gap this refusal creates. Attached to every answer it limits.

        ``confidence_impact`` scales with what the capability would have
        provided: losing `lockfile-read` costs far more than losing
        `message-inspect`, because a lockfile is the only route to certainty.
        """
        return Gap(
            kind=GapKind.CAPABILITY_REFUSED,
            subject=element,
            detail=f"{element} refused {self.name}: {self.reason}",
            remediation=f"grant {self.name} on {element}, or record why it cannot be granted",
            confidence_impact=self.impact,
        )

    @property
    def impact(self) -> float:
        """How much certainty this refusal costs, from the evidence it blocks."""
        if not self.evidence_kinds:
            return 0.05
        best = max(kind.base_confidence for kind in self.evidence_kinds)
        return round(min(best * 0.5, 0.5), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "granted": self.granted, "reason": self.reason,
            "known": self.known, "impact": self.impact,
            "evidence_kinds": [kind.value for kind in self.evidence_kinds],
        }

    def __str__(self) -> str:
        return f"{self.name}: {'granted' if self.granted else f'refused ({self.reason})'}"


@dataclass(frozen=True, slots=True)
class Negotiation:
    """The outcome of asking an element what it will allow.

    Both halves are kept. Knowing only what was granted makes it impossible to
    distinguish "this element has no source to analyse" from "this element would
    not let us look".
    """

    element: str
    granted: tuple[Capability, ...] = ()
    refused: tuple[Capability, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(capability.name for capability in self.granted)

    def allows(self, name: str) -> bool:
        return any(capability.name == name for capability in self.granted)

    def gaps(self) -> tuple[Gap, ...]:
        return tuple(capability.as_gap(self.element) for capability in self.refused)

    @property
    def complete(self) -> bool:
        return not self.refused

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element,
            "granted": [capability.to_dict() for capability in self.granted],
            "refused": [capability.to_dict() for capability in self.refused],
            "complete": self.complete,
        }


def negotiate(element: str, connector: Any, *, wanted: Iterable[str] = ()) -> Negotiation:
    """Ask a connector what it offers, and record everything it withholds.

    ``wanted`` is what the platform would use if it could. Anything wanted and
    not offered is refused *by omission*, which is a real refusal — an element
    that simply cannot do something limits an answer exactly as much as one that
    declines to.
    """
    offered = set(getattr(connector, "capabilities", ()))
    requested = set(wanted) or offered

    # A connector may explain its own refusals; a faulty or restricted one does.
    explanations: dict[str, str] = {}
    if hasattr(connector, "refusals"):
        explanations = {name: reason for name, reason in connector.refusals()}

    granted = tuple(
        Capability.grant(name) for name in sorted(requested & offered)
    )
    refused = tuple(
        Capability.refuse(
            name,
            explanations.get(
                name, f"the connector for {element} does not offer {name}"
            ),
        )
        for name in sorted(requested - offered)
    )
    return Negotiation(element=element, granted=granted, refused=refused)
