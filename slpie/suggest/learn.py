"""Teaching by acceptance — the learned half, kept auditable.

The system does not learn from configuration and does not learn from watching
silently. It learns from **acceptance**: the reviewer takes a suggested path, or
dismisses it, and each of those is a recorded fact.

Three things follow from recording it as facts rather than as weights in a file,
and all three matter more than the accuracy of the ranking itself:

* **it can be explained.** "Why is it offering this first" has a real answer —
  the gain calculation, plus what has been accepted here before. A suggestion the
  reviewer cannot interrogate is one they stop trusting the first time it is
  wrong, and it will be wrong sometimes.
* **it can be rebuilt.** The ranking is a projection of the record, so it can be
  recomputed from the beginning like every other projection in the platform.
* **dismissal counts.** A suggestion dismissed repeatedly stops being offered, and
  says so when asked. Systems that only learn from acceptance keep proposing the
  thing you have refused four times, which is how a helpful feature becomes an
  irritant people disable.

The weight this produces is deliberately bounded. It **reorders** candidates; it
can never introduce or remove one — removal is the separate, explicit retirement
rule below. So the worst a badly-learned weight can do is put a useful path
second.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

#: How many dismissals retire a suggestion for a given situation. Three, because
#: one is a mis-click, two is a coincidence, and three is a decision.
RETIRE_AFTER = 3

#: The range a learned weight may move a candidate within. Bounded on purpose: an
#: unbounded weight would let history dominate the gain calculation entirely, and
#: a reviewer's habits are evidence about preference, not about importance.
MIN_WEIGHT = 0.5
MAX_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class Reaction:
    """One recorded response to one suggestion, in one situation."""

    touch_id: str
    suggestion_id: str
    pipeline: str
    accepted: bool
    actor: str = "user"
    at: int = 0
    reason: str = ""            # why it was dismissed, when given

    def to_dict(self) -> dict[str, Any]:
        return {
            "touch": self.touch_id, "suggestion": self.suggestion_id,
            "pipeline": self.pipeline, "accepted": self.accepted,
            "actor": self.actor, "at": self.at, "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> "Reaction":
        return cls(
            touch_id=body.get("touch", ""),
            suggestion_id=body.get("suggestion", ""),
            pipeline=body.get("pipeline", ""),
            accepted=bool(body.get("accepted")),
            actor=body.get("actor", "user"),
            at=int(body.get("at", 0)),
            reason=body.get("reason", ""),
        )


class History:
    """The acceptance record, and the ranking derived from it.

    Backed by a plain append-only JSONL file rather than the SQLite ledger, on
    purpose: this has to work before an environment exists, since the most useful
    suggestions are the ones offered to somebody who has not set anything up yet.
    When an environment *is* present the same reactions are also dispatched as
    domain events, so the ledger holds them too — but nothing here depends on it.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._reactions: list[Reaction] = []
        if self.path and self.path.exists():
            self._load()

    # -- recording -------------------------------------------------------

    def record(
        self,
        touch: Any,
        suggestion: Any,
        *,
        accepted: bool,
        actor: str = "user",
        reason: str = "",
    ) -> Reaction:
        reaction = Reaction(
            touch_id=getattr(touch, "id", str(touch)),
            suggestion_id=getattr(suggestion, "id", ""),
            pipeline=getattr(suggestion, "pipeline", str(suggestion)),
            accepted=accepted, actor=actor, reason=reason,
            at=int(time.time()),
        )
        self._reactions.append(reaction)
        self._append(reaction)
        return reaction

    def accept(self, touch: Any, suggestion: Any, **options: Any) -> Reaction:
        return self.record(touch, suggestion, accepted=True, **options)

    def dismiss(self, touch: Any, suggestion: Any, **options: Any) -> Reaction:
        return self.record(touch, suggestion, accepted=False, **options)

    # -- the learned part ------------------------------------------------

    def weight(self, touch: Any, suggestion: Any) -> float:
        """How much to favour this suggestion here. Bounded, always.

        Acceptances in the *same situation* count fully; acceptances of the same
        pipeline in a *different* situation count for less but not for nothing —
        a reviewer who always wants to see the evidence probably wants to see it
        here too, and ignoring that means every new situation starts cold.
        """
        touch_id = getattr(touch, "id", "")
        pipeline = getattr(suggestion, "pipeline", "")

        here_up = here_down = 0
        anywhere_up = anywhere_down = 0
        for reaction in self._reactions:
            if reaction.pipeline != pipeline:
                continue
            if reaction.touch_id == touch_id:
                if reaction.accepted:
                    here_up += 1
                else:
                    here_down += 1
            elif reaction.accepted:
                anywhere_up += 1
            else:
                anywhere_down += 1

        score = (
            1.0
            + 0.25 * here_up - 0.25 * here_down
            + 0.08 * anywhere_up - 0.08 * anywhere_down
        )
        return max(MIN_WEIGHT, min(MAX_WEIGHT, score))

    def retired(self, touch: Any, suggestion: Any) -> bool:
        """Whether this has been refused often enough to stop offering it.

        Scoped to the situation, not global: a path dismissed while reviewing
        findings may be exactly right when reviewing a gap, and retiring it
        everywhere would punish a reviewer for one context's preference.
        """
        touch_id = getattr(touch, "id", "")
        pipeline = getattr(suggestion, "pipeline", "")
        refusals = sum(
            1 for reaction in self._reactions
            if reaction.pipeline == pipeline
            and reaction.touch_id == touch_id
            and not reaction.accepted
        )
        return refusals >= RETIRE_AFTER

    def informed(self, touch: Any) -> bool:
        """Whether anything has been learned about this situation yet."""
        touch_id = getattr(touch, "id", "")
        return any(item.touch_id == touch_id for item in self._reactions)

    def explain(self, touch: Any, suggestion: Any) -> str:
        """Why this sits where it sits. The learned part, made interrogable."""
        weight = self.weight(touch, suggestion)
        touch_id = getattr(touch, "id", "")
        pipeline = getattr(suggestion, "pipeline", "")

        here = [
            item for item in self._reactions
            if item.pipeline == pipeline and item.touch_id == touch_id
        ]
        accepted = sum(1 for item in here if item.accepted)
        dismissed = len(here) - accepted

        if not here:
            return (
                f"gain {getattr(suggestion, 'gain', 0):.3f}; nothing has been "
                f"accepted or dismissed here yet, so the order is the computed one"
            )
        if self.retired(touch, suggestion):
            return (
                f"retired: dismissed {dismissed} times in this situation, so it "
                f"is no longer offered"
            )
        return (
            f"gain {getattr(suggestion, 'gain', 0):.3f} × weight {weight:.2f} "
            f"— accepted {accepted}, dismissed {dismissed} here before"
        )

    # -- the session, and what can be claimed from it --------------------

    def session(self, *, since: int = 0, actor: str = "") -> tuple[Reaction, ...]:
        """Accepted paths, in the order they were taken. The routine candidate."""
        return tuple(
            item for item in self._reactions
            if item.accepted
            and item.at >= since
            and (not actor or item.actor == actor)
        )

    def accepted_pipelines(self, **options: Any) -> tuple[str, ...]:
        """Distinct accepted pipelines, oldest first, for claiming a routine."""
        return tuple(dict.fromkeys(
            item.pipeline for item in self.session(**options)
        ))

    # -- persistence -----------------------------------------------------

    def _append(self, reaction: Reaction) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(reaction.to_dict()) + "\n")

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                self._reactions.append(Reaction.from_dict(json.loads(stripped)))
            except (ValueError, KeyError):
                # A corrupted line loses that reaction, not the whole history.
                # Refusing to start because one row is malformed would make a
                # convenience feature into an outage.
                continue

    def to_dict(self) -> dict[str, Any]:
        accepted = sum(1 for item in self._reactions if item.accepted)
        return {
            "reactions": len(self._reactions),
            "accepted": accepted,
            "dismissed": len(self._reactions) - accepted,
            "pipelines": len({item.pipeline for item in self._reactions}),
        }

    def __len__(self) -> int:
        return len(self._reactions)

    def __iter__(self) -> Iterator[Reaction]:
        return iter(self._reactions)
