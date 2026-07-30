"""Where the reviewer's attention actually is — inferred, then made deterministic.

A click is a poor signal of confusion. Somebody who clicks immediately knew what
they wanted; somebody who **hovers for four seconds and clicks nothing** did not
find what they needed and is about to give up. That inversion is the useful part,
and it is invisible to any system that only listens for clicks.

So attention is read from a small set of signals — dwell, re-approach, repeat
clicks, text selection, scroll-back, idling — and reduced to a **focus score per
subject**. The subject with the highest score becomes the `Touch`, and the score's
shape decides how *stuck* the reviewer is, which in turn decides whether they get
one obvious next step or a menu.

Three commitments make this defensible rather than creepy, and they are not
optional:

1. **No trails are stored.** Raw coordinates, timings and paths are reduced to
   per-subject aggregates in memory and discarded. What persists is "focus landed
   on this finding for 4.2s and produced no action" — never a mouse path. A
   reviewer's cursor is not evidence about the architecture, and keeping it would
   be collecting something we have no use for.
2. **Attention decides *what* was touched, never *what is offered*.** The
   suggestions themselves still come from the type graph. Attention can be wrong
   about where you were looking; it cannot make the platform propose something it
   cannot do.
3. **Below a floor, it declines to guess.** Weak, scattered signals yield no focus
   at all rather than a confident answer about the largest element on screen.

The CLI gets the same treatment with different signals — time between commands,
retyping a near-identical command, repeated `--help`, repeated failures. A reviewer
stuck at a prompt looks different from one stuck at a screen, and both are worth
catching.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .touch import Touch, TouchKind


class SignalKind(str, Enum):
    """One observable act of attention. Deliberately few and unambiguous."""

    # screen
    DWELL = "dwell"                  # hovered, in seconds
    CLICK = "click"
    REPEAT_CLICK = "repeat_click"    # the same thing again — it did not answer
    SELECT = "select"                # selected text: about to copy it elsewhere
    SCROLL_BACK = "scroll_back"      # re-read something above
    RE_ENTRY = "re_entry"            # left and came back
    HESITATE = "hesitate"            # approached and did not act
    IDLE = "idle"                    # stopped entirely

    # prompt
    RETYPE = "retype"                # a near-identical command again
    HELP = "help"                    # asked for help
    FAILURE = "failure"              # a command failed
    PAUSE = "pause"                  # a long gap before the next command

    @property
    def weight(self) -> float:
        """How much this signal indicates *confusion*, not interest.

        A click is close to zero because clicking is what a person does when
        things are going well. Selecting text is the highest single signal on a
        screen: people select a word to paste it into a search engine, which is a
        direct admission that the interface did not explain it.
        """
        return {
            SignalKind.DWELL: 0.30,
            SignalKind.CLICK: 0.05,
            SignalKind.REPEAT_CLICK: 0.55,
            SignalKind.SELECT: 0.70,
            SignalKind.SCROLL_BACK: 0.40,
            SignalKind.RE_ENTRY: 0.45,
            SignalKind.HESITATE: 0.50,
            SignalKind.IDLE: 0.35,
            SignalKind.RETYPE: 0.60,
            SignalKind.HELP: 0.65,
            SignalKind.FAILURE: 0.80,
            SignalKind.PAUSE: 0.25,
        }[self]

    @property
    def resolving(self) -> bool:
        """Whether this signal suggests the reviewer is *getting* somewhere.

        Only a plain click. Everything else is either neutral or a sign of
        difficulty, which is why a stream of clicks should never trip the
        suggestion machinery.
        """
        return self is SignalKind.CLICK


#: Dwell shorter than this is passing the cursor over something, not reading it.
#: Below it, dwell contributes nothing — otherwise moving the mouse across a list
#: would register interest in everything it crossed.
DWELL_FLOOR = 0.8

#: Dwell beyond this stops adding confusion. Somebody who left the tab open for
#: four minutes is at lunch, not stuck, and letting that dominate would make the
#: strongest signal in the system a coffee break.
DWELL_CEILING = 12.0

#: Dwell beyond this, with nothing resolving, is read as hesitation rather than
#: reading. Two seconds is long enough to have read a short label and not acted.
HESITATION_AFTER = 2.0

#: What hesitation adds. It is a *bonus computed at focus time* rather than a
#: signal weight, because the inference is about an absence: dwelling and then
#: doing nothing. Summing signal weights can never express "and then nothing
#: happened", and without this the headline case — a four-second hover that
#: produced no click — scored below the floor and produced no help at all.
HESITATION_BONUS = 0.30

#: Below this focus score, attention declines to name a subject at all.
FOCUS_FLOOR = 0.35

#: At or above this, the reviewer is treated as blocked rather than browsing.
STUCK_AT = 0.65


@dataclass(frozen=True, slots=True)
class Signal:
    """One observation about attention. Aggregated, then discarded."""

    kind: SignalKind
    subject: str
    seconds: float = 0.0
    at: float = 0.0
    detail: str = ""

    def score(self) -> float:
        """This signal's contribution. Dwell scales with time; the rest do not."""
        if self.kind is not SignalKind.DWELL:
            return self.kind.weight
        if self.seconds < DWELL_FLOOR:
            return 0.0
        span = min(self.seconds, DWELL_CEILING) - DWELL_FLOOR
        ceiling = DWELL_CEILING - DWELL_FLOOR
        return self.kind.weight * (0.4 + 0.6 * (span / ceiling))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value, "subject": self.subject,
            "seconds": round(self.seconds, 2), "score": round(self.score(), 4),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Focus:
    """Where attention settled, and how stuck the reviewer appears to be."""

    subject: str
    score: float
    signals: tuple[Signal, ...] = ()
    kind: TouchKind = TouchKind.NODE
    detail: str = ""

    @property
    def found(self) -> bool:
        return bool(self.subject) and self.score >= FOCUS_FLOOR

    @property
    def stuck(self) -> bool:
        return self.score >= STUCK_AT

    @property
    def dwell(self) -> float:
        return sum(
            item.seconds for item in self.signals
            if item.kind is SignalKind.DWELL
        )

    @property
    def acted(self) -> bool:
        """Whether anything resolving happened. Dwell without action is the case."""
        return any(item.kind.resolving for item in self.signals)

    def explain(self) -> str:
        if not self.found:
            return (
                "no clear focus: the signals were too weak or too scattered to "
                "name what you were looking at, and guessing would be worse than "
                "waiting"
            )
        parts = [f"focus {self.score:.2f} on {self.subject}"]
        if self.dwell >= HESITATION_AFTER and not self.acted:
            parts.append(
                f"held for {self.dwell:.1f}s without acting, which usually means "
                f"it did not answer"
            )
        counts: dict[str, int] = {}
        for signal in self.signals:
            counts[signal.kind.value] = counts.get(signal.kind.value, 0) + 1
        parts.append(", ".join(
            f"{name}×{count}" for name, count in sorted(counts.items())
        ))
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "score": round(self.score, 4),
            "kind": self.kind.value, "found": self.found, "stuck": self.stuck,
            "dwell": round(self.dwell, 2), "acted": self.acted,
            "explanation": self.explain(),
            # Aggregates only. The individual signals are summarised by kind, not
            # replayed, because a per-event log is a trail by another name.
            "signals": sorted({item.kind.value for item in self.signals}),
        }

    def touch(self, **context: Any) -> Touch:
        """The focus, as the trigger the suggestion engine consumes."""
        return Touch(
            kind=self.kind, subject=self.subject, surface="screen",
            context={
                "focus": round(self.score, 3),
                "dwell": round(self.dwell, 2),
                "acted": self.acted,
                **context,
            },
        )


class Attention:
    """Accumulates signals and reduces them to one focus. Holds no trail.

    Deliberately not a store. Signals go in, aggregates come out, and nothing is
    written anywhere — the only thing that outlives this object is the `Touch` it
    produces and, if the reviewer acts on a suggestion, the acceptance record.
    """

    def __init__(self, *, decay: float = 30.0) -> None:
        #: Signals older than this stop counting. Attention is about *now*; a
        #: hover from two minutes ago says nothing about the current screen, and
        #: letting it linger makes focus drift towards whatever was busiest.
        self.decay = decay
        self._signals: list[Signal] = []
        self._kinds: dict[str, TouchKind] = {}

    # -- recording -------------------------------------------------------

    def observe(
        self,
        kind: SignalKind | str,
        subject: str,
        *,
        seconds: float = 0.0,
        touch_kind: TouchKind | str = TouchKind.NODE,
        detail: str = "",
        at: float | None = None,
    ) -> Signal:
        signal = Signal(
            kind=SignalKind(kind), subject=subject, seconds=float(seconds),
            at=time.monotonic() if at is None else at, detail=detail,
        )
        self._signals.append(signal)
        self._kinds.setdefault(subject, TouchKind(touch_kind))
        return signal

    # Convenience recorders, so a surface reads clearly.
    def hovered(self, subject: str, seconds: float, **options: Any) -> Signal:
        return self.observe(SignalKind.DWELL, subject, seconds=seconds, **options)

    def clicked(self, subject: str, **options: Any) -> Signal:
        repeat = any(
            item.kind in (SignalKind.CLICK, SignalKind.REPEAT_CLICK)
            and item.subject == subject
            for item in self._live()
        )
        return self.observe(
            SignalKind.REPEAT_CLICK if repeat else SignalKind.CLICK,
            subject, **options,
        )

    def selected(self, text: str, **options: Any) -> Signal:
        return self.observe(
            SignalKind.SELECT, text, touch_kind=TouchKind.TERM, **options,
        )

    def failed(self, command: str, **options: Any) -> Signal:
        return self.observe(
            SignalKind.FAILURE, command, touch_kind=TouchKind.ERROR, **options,
        )

    def retyped(self, command: str, **options: Any) -> Signal:
        return self.observe(
            SignalKind.RETYPE, command, touch_kind=TouchKind.ERROR, **options,
        )

    def asked_help(self, topic: str, **options: Any) -> Signal:
        return self.observe(
            SignalKind.HELP, topic, touch_kind=TouchKind.TERM, **options,
        )

    # -- reduction -------------------------------------------------------

    def _live(self) -> list[Signal]:
        now = time.monotonic()
        return [
            item for item in self._signals
            if item.at == 0.0 or (now - item.at) <= self.decay
        ]

    def scores(self) -> dict[str, float]:
        """Focus per subject, capped at 1. Aggregate only — no ordering kept.

        Hesitation is applied here rather than as a signal weight, because it is an
        inference about an *absence*: dwelled, then did nothing. A sum of weights
        cannot express "and then nothing happened", and the headline case — a four
        second hover that produced no click — is exactly that shape.
        """
        totals: dict[str, float] = {}
        dwell: dict[str, float] = {}
        acted: set[str] = set()

        for signal in self._live():
            totals[signal.subject] = totals.get(signal.subject, 0.0) + signal.score()
            if signal.kind is SignalKind.DWELL:
                dwell[signal.subject] = dwell.get(signal.subject, 0.0) + signal.seconds
            if signal.kind.resolving:
                acted.add(signal.subject)

        for subject, seconds in dwell.items():
            if seconds >= HESITATION_AFTER and subject not in acted:
                totals[subject] = totals.get(subject, 0.0) + HESITATION_BONUS

        return {name: min(1.0, value) for name, value in totals.items()}

    def focus(self) -> Focus:
        """Where attention settled. Declines to guess when nothing stands out."""
        totals = self.scores()
        if not totals:
            return Focus(subject="", score=0.0)

        subject, score = max(totals.items(), key=lambda entry: (entry[1], entry[0]))

        # A near-tie is not a focus. Two subjects within a hair of each other means
        # the reviewer's attention was split, and naming one of them would be a
        # coin toss presented as an inference.
        ranked = sorted(totals.values(), reverse=True)
        if len(ranked) > 1 and (ranked[0] - ranked[1]) < 0.08:
            return Focus(
                subject="", score=ranked[0],
                detail="attention was split between subjects",
            )

        signals = tuple(item for item in self._live() if item.subject == subject)
        return Focus(
            subject=subject, score=score, signals=signals,
            kind=self._kinds.get(subject, TouchKind.NODE),
        )

    def touch(self, **context: Any) -> Touch | None:
        """The touch to act on, or None when nothing is clear enough."""
        focus = self.focus()
        return focus.touch(**context) if focus.found else None

    def clear(self) -> None:
        """Forget everything. Called on navigation, and after producing a touch."""
        self._signals.clear()
        self._kinds.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "focus": self.focus().to_dict(),
            "subjects": len(self.scores()),
            "signals": len(self._live()),
            "decay": self.decay,
        }

    def __len__(self) -> int:
        return len(self._live())

    def __iter__(self) -> Iterator[Signal]:
        return iter(self._live())


def from_events(events: Iterable[Mapping[str, Any]], *, decay: float = 30.0) -> Attention:
    """Build attention from what a client reports.

    The client sends already-aggregated events — `{"kind": "dwell", "subject": …,
    "seconds": 4.2}` — never a stream of coordinates. That boundary is where the
    privacy commitment is actually enforced: the kernel cannot store a trail it is
    never sent.
    """
    attention = Attention(decay=decay)
    for event in events:
        try:
            attention.observe(
                str(event.get("kind", "")),
                str(event.get("subject", "")),
                seconds=float(event.get("seconds", 0) or 0),
                touch_kind=str(event.get("touch_kind", "node")),
                detail=str(event.get("detail", "")),
                at=0.0,
            )
        except (ValueError, TypeError):
            # An unknown signal kind is dropped rather than failing the batch: a
            # newer client sending a signal this build does not know about must
            # not break guidance entirely.
            continue
    return attention
