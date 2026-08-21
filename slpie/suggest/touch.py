"""What the user touched, and why that is the moment to help.

The axiom this package implements: *if the user touches one thing they cannot
understand as a reviewer, it fires smart suggestion paths with deterministic
guidance.*

A `Touch` is the trigger. It is deliberately not "the user asked a question" —
asking is the easy case, and the planner already handles it. The hard case is the
reviewer who does not know enough to form a question: they clicked a finding, they
hit an error, they ran something and got nothing back. Those are the moments a
conventional help system says nothing at all, and they are the moments a reviewer
quietly stops reviewing.

So `ERROR` and `EMPTY_RESULT` are first-class touch kinds rather than edge cases.
They are where the value is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..domain.identity import digest


class TouchKind(str, Enum):
    """What sort of thing was touched. Each has its own useful next moves."""

    NODE = "node"                   # a package, service, table in the graph
    FINDING = "finding"             # something reported wrong
    GAP = "gap"                     # something the platform could not see
    TERM = "term"                   # a word in the output they do not know
    VERB = "verb"                   # a capability they have not used
    ERROR = "error"                 # a command that failed
    EMPTY_RESULT = "empty_result"   # a command that returned nothing
    KIND = "kind"                   # a flow kind: "what is a RESOLUTION?"

    @property
    def stuck(self) -> bool:
        """Whether this is a moment the reviewer is blocked rather than curious.

        Drives urgency: a blocked reviewer needs one obvious next step, and a
        curious one can be offered several. Getting this backwards buries the
        answer of somebody who cannot proceed under a menu.
        """
        return self in (TouchKind.ERROR, TouchKind.EMPTY_RESULT, TouchKind.GAP)

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


@dataclass(frozen=True, slots=True)
class Touch:
    """One moment where the platform should offer a path rather than a definition.

    `context` carries whatever the surface knows — the flow that was on screen, the
    command that failed, the pipeline that returned nothing. It is what lets a
    suggestion be about *this* situation rather than about the subject in general,
    which is the whole difference between guidance and a glossary.
    """

    kind: TouchKind
    subject: str
    context: Mapping[str, Any] = field(default_factory=dict)
    surface: str = "cli"            # cli · screen · mobile · api
    actor: str = "user"

    @property
    def id(self) -> str:
        """Content-addressed, so the same touch in the same situation is one touch.

        Excludes the actor and the surface: the *situation* is what a suggestion
        answers, and keying on who touched it would prevent one reviewer's
        acceptance from ever helping another.
        """
        return digest({
            "kind": self.kind.value,
            "subject": self.subject,
            "situation": sorted(
                (str(key), str(value))
                for key, value in self.context.items()
                if key in SITUATION_KEYS
            ),
        })

    @property
    def stuck(self) -> bool:
        return self.kind.stuck

    def value(self, name: str, default: Any = None) -> Any:
        return self.context.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind.value, "subject": self.subject,
            "surface": self.surface, "actor": self.actor,
            "stuck": self.stuck, "context": dict(self.context),
        }

    def __str__(self) -> str:
        return f"{self.kind.label} {self.subject!r}"


#: Context keys that make one situation different from another. Everything else —
#: timestamps, screen sizes, request ids — is noise that would fragment the
#: acceptance history into single-use buckets nothing could ever be learned from.
SITUATION_KEYS = frozenset({
    "pipeline", "kind", "verb", "error", "severity", "ecosystem", "empty",
})


def on_node(subject: str, **context: Any) -> Touch:
    return Touch(kind=TouchKind.NODE, subject=subject, context=context)


def on_finding(subject: str, **context: Any) -> Touch:
    return Touch(kind=TouchKind.FINDING, subject=subject, context=context)


def on_gap(subject: str, **context: Any) -> Touch:
    return Touch(kind=TouchKind.GAP, subject=subject, context=context)


def on_term(word: str, **context: Any) -> Touch:
    return Touch(kind=TouchKind.TERM, subject=word, context=context)


def on_error(message: str, **context: Any) -> Touch:
    return Touch(
        kind=TouchKind.ERROR, subject=message,
        context={"error": message, **context},
    )


def on_empty(pipeline: str, **context: Any) -> Touch:
    return Touch(
        kind=TouchKind.EMPTY_RESULT, subject=pipeline,
        context={"pipeline": pipeline, "empty": True, **context},
    )
