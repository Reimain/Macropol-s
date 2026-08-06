"""The ladder from suggestion to one click — and the rails that make it safe.

The goal is a routine that runs itself. The danger is the same routine running
itself once the world has moved: the command that worked forty times, on a
machine that has since changed, executed by nobody watching.

So promotion is earned and revocable, and it is gated on three things at once:

```
SUGGESTED ──(a human ran it and it worked)──→ CONFIRMED
CONFIRMED ──(enough clean runs, same machine)──→ TRUSTED
TRUSTED   ──(guard says ALLOW unattended)────→ AUTOMATED   ← one click
     ↑                                              │
     └──────── any failure, or the machine changed ─┘
```

**Risk caps the ladder, and evidence cannot lift it.** A catastrophic command
never reaches AUTOMATED however many times it has worked, because the argument
"it worked forty times" is exactly the argument that precedes the forty-first.
Success is evidence about the past; risk is a statement about the blast radius,
and only one of those is relevant to running it unattended.

**The environment fingerprint travels with the routine.** A routine recorded on
one machine and replayed on another is a script in a language the box has since
forgotten — the failure mode where knowledge survives as text but nothing can
verify it any more. On drift the routine is demoted to CONFIRMED and a human
looks at it again. It is not deleted: the knowledge is still worth having, it
just stops being trusted silently.

**Demotion is one step per failure, promotion is many runs.** Asymmetric on
purpose. Trust should be slow to gain and quick to lose, because the cost of the
two mistakes is not the same.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..errors import ShellError
from .command import Risk
from .environment import Environment
from .guard import Guard, Stance, Verdict

#: Clean runs needed at each step up. Deliberately small — the point is to reach
#: automation, not to make it unreachable — and paired with instant demotion.
PROMOTION_RUNS: dict[int, int] = {0: 1, 1: 3, 2: 5}

#: Highest risk that may ever be automated, whatever its history.
AUTOMATABLE = Risk.CAUTION


class Trust(IntEnum):
    """How far a routine has earned its way. Ordered."""

    SUGGESTED = 0     # proposed; never yet run here
    CONFIRMED = 1     # a human ran it and it worked
    TRUSTED = 2       # repeatedly clean on this machine
    AUTOMATED = 3     # runs on one click, unattended

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def unattended(self) -> bool:
        return self is Trust.AUTOMATED


@dataclass(frozen=True, slots=True)
class Outcome:
    """One recorded run. The evidence promotion is built from."""

    succeeded: bool
    at: float
    fingerprint: str = ""
    note: str = ""
    approved_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded, "at": self.at,
            "fingerprint": self.fingerprint, "note": self.note,
            "approved_by": self.approved_by,
        }


@dataclass(frozen=True, slots=True)
class Routine:
    """A command that has earned some standing, and the record behind it."""

    signature: str                  # the need this answers, from the ontology
    command: str
    rationale: str = ""
    trust: Trust = Trust.SUGGESTED
    risk: Risk = Risk.SAFE
    fingerprint: str = ""
    outcomes: tuple[Outcome, ...] = ()
    promoted_at: float = 0.0
    demoted_reason: str = ""

    @property
    def successes(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.succeeded)

    @property
    def failures(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.succeeded)

    @property
    def clean_streak(self) -> int:
        """Consecutive successes since the last failure. Promotion runs on this.

        Total successes would let a routine that fails one run in three climb
        the ladder anyway, which is exactly the routine that should not.
        """
        streak = 0
        for outcome in reversed(self.outcomes):
            if not outcome.succeeded:
                break
            streak += 1
        return streak

    @property
    def automatable(self) -> bool:
        """Whether this could ever be automated. Fixed by risk, not by history."""
        return self.risk <= AUTOMATABLE

    def drifted(self, environment: Environment) -> bool:
        return bool(self.fingerprint) and self.fingerprint != environment.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "command": self.command,
            "rationale": self.rationale,
            "trust": self.trust.label,
            "risk": self.risk.label,
            "fingerprint": self.fingerprint,
            "successes": self.successes,
            "failures": self.failures,
            "clean_streak": self.clean_streak,
            "automatable": self.automatable,
            "promoted_at": self.promoted_at,
            "demoted_reason": self.demoted_reason,
        }

    def __str__(self) -> str:
        return f"{self.command} [{self.trust.label}, {self.risk.label}]"


@dataclass(frozen=True, slots=True)
class Transition:
    """A move on the ladder, and why. Every one of these is worth recording."""

    routine: Routine
    was: Trust
    now: Trust
    reason: str
    at: float = 0.0

    @property
    def promoted(self) -> bool:
        return self.now > self.was

    @property
    def demoted(self) -> bool:
        return self.now < self.was

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.routine.command, "was": self.was.label,
            "now": self.now.label, "reason": self.reason, "at": self.at,
        }

    def __str__(self) -> str:
        if self.now == self.was:
            return f"{self.routine.command}: stays {self.now.label} — {self.reason}"
        arrow = "↑" if self.promoted else "↓"
        return f"{self.routine.command}: {self.was.label} {arrow} {self.now.label} — {self.reason}"


class Ladder:
    """Holds routines and moves them up and down. Never runs anything."""

    def __init__(self, guard: Guard | None = None) -> None:
        self.guard = guard
        self._routines: dict[str, Routine] = {}
        self._log: list[Transition] = []

    # -- learning ---------------------------------------------------------

    def propose(self, signature: str, command: str, *, rationale: str = "",
                risk: Risk = Risk.SAFE, environment: Environment | None = None) -> Routine:
        """Record a suggestion. Nothing is trusted at this point."""
        key = f"{signature}|{command}"
        existing = self._routines.get(key)
        if existing is not None:
            return existing
        routine = Routine(
            signature=signature, command=command, rationale=rationale, risk=risk,
            fingerprint=environment.fingerprint if environment else "",
        )
        self._routines[key] = routine
        return routine

    def record(
        self,
        signature: str,
        command: str,
        *,
        succeeded: bool,
        environment: Environment | None = None,
        approved_by: str = "",
        note: str = "",
        now: float | None = None,
    ) -> Transition:
        """Log an outcome and move the routine if it has earned it."""
        moment = time.time() if now is None else now
        key = f"{signature}|{command}"
        routine = self._routines.get(key)
        if routine is None:
            routine = self.propose(signature, command, environment=environment)

        outcome = Outcome(
            succeeded=succeeded, at=moment, approved_by=approved_by, note=note,
            fingerprint=environment.fingerprint if environment else "",
        )
        routine = replace(routine, outcomes=(*routine.outcomes, outcome))
        self._routines[key] = routine

        if not succeeded:
            return self._move(
                key, max(Trust(routine.trust - 1), Trust.SUGGESTED),
                f"a run failed{f': {note}' if note else ''}", moment,
            )
        return self._consider(key, environment=environment, now=moment)

    def _consider(self, key: str, *, environment: Environment | None,
                  now: float) -> Transition:
        routine = self._routines[key]
        current = routine.trust

        if environment is not None and routine.drifted(environment):
            return self._move(
                key, min(current, Trust.CONFIRMED),
                "the machine has changed since this was recorded; it needs "
                "looking at before it is trusted again",
                now,
            )

        if current is Trust.AUTOMATED:
            return self._move(key, current, "already automated", now)

        needed = PROMOTION_RUNS.get(int(current))
        if needed is None or routine.clean_streak < needed:
            return self._move(
                key, current,
                f"{routine.clean_streak} clean run(s); {needed} needed to move up"
                if needed else "at the top of what it can reach",
                now,
            )

        target = Trust(current + 1)

        if target is Trust.AUTOMATED:
            if not routine.automatable:
                return self._move(
                    key, current,
                    f"is {routine.risk.label} and will never run unattended, however "
                    f"many times it has worked — history is about the past, risk is "
                    f"about the blast radius",
                    now,
                )
            if self.guard is not None:
                verdict = self.guard.review(
                    routine.command, environment=environment, attended=False, now=now
                )
                if verdict.stance is not Stance.ALLOW:
                    return self._move(
                        key, current,
                        f"the guard would not run this unattended: "
                        f"{verdict.reasons[0] if verdict.reasons else verdict.stance.value}",
                        now,
                    )

        return self._move(
            key, target,
            f"{routine.clean_streak} clean run(s) on this machine", now,
        )

    def demote(self, signature: str, command: str, *, reason: str,
               to: Trust = Trust.SUGGESTED, now: float | None = None) -> Transition:
        """Take trust away by hand. Always available, and always recorded."""
        if not reason.strip():
            raise ShellError("a demotion must state a reason")
        return self._move(f"{signature}|{command}", to, reason,
                          time.time() if now is None else now)

    def _move(self, key: str, target: Trust, reason: str, now: float) -> Transition:
        routine = self._routines[key]
        was = routine.trust
        updated = replace(
            routine, trust=target,
            promoted_at=now if target > was else routine.promoted_at,
            demoted_reason=reason if target < was else "",
        )
        self._routines[key] = updated
        transition = Transition(routine=updated, was=was, now=target,
                                reason=reason, at=now)
        if target != was:
            self._log.append(transition)
        return transition

    # -- reading ----------------------------------------------------------

    def get(self, signature: str, command: str) -> Routine | None:
        return self._routines.get(f"{signature}|{command}")

    def for_need(self, signature: str) -> tuple[Routine, ...]:
        """Everything known for one need, most trusted first — the suggestion list."""
        found = [r for r in self._routines.values() if r.signature == signature]
        return tuple(sorted(found, key=lambda r: (-r.trust, -r.clean_streak, r.command)))

    def jackpot(self, signature: str, environment: Environment | None = None
                ) -> Routine | None:
        """The one-click answer for this need, if one has earned it.

        Returns None rather than the next-best routine. Offering a CONFIRMED
        routine where the caller asked for an automated one would make "one
        click" mean "one click and a dialogue", which is not the same promise.
        """
        for routine in self.for_need(signature):
            if routine.trust is not Trust.AUTOMATED:
                continue
            if environment is not None and routine.drifted(environment):
                continue
            return routine
        return None

    @property
    def transitions(self) -> tuple[Transition, ...]:
        return tuple(self._log)

    def status(self) -> dict[str, Any]:
        by_trust: dict[str, int] = {}
        for routine in self._routines.values():
            by_trust[routine.trust.label] = by_trust.get(routine.trust.label, 0) + 1
        automated = [r for r in self._routines.values() if r.trust is Trust.AUTOMATED]
        return {
            "routines": len(self._routines),
            "by_trust": by_trust,
            "automated": len(automated),
            "capped_by_risk": len([
                r for r in self._routines.values()
                if not r.automatable and r.trust >= Trust.TRUSTED
            ]),
            "transitions": len(self._log),
        }

    def render(self) -> str:
        lines = [f"{len(self._routines)} routines"]
        for routine in sorted(self._routines.values(),
                              key=lambda r: (-r.trust, r.command)):
            mark = "⚡" if routine.trust is Trust.AUTOMATED else " "
            lines.append(f" {mark} {routine}")
            if routine.demoted_reason:
                lines.append(f"      ↓ {routine.demoted_reason}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._routines)

    def __iter__(self) -> Iterator[Routine]:
        return iter(tuple(self._routines.values()))

    def __repr__(self) -> str:  # pragma: no cover - display only
        status = self.status()
        return f"<Ladder {status['automated']}/{status['routines']} automated>"
