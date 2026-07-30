"""Routines — claimed at the end of a session, then typed as one short key.

This is the loop that makes the platform worth returning to. A reviewer composes
four stages, accepts a few suggestions, and at the end of the session **claims**
what they did as a routine with a two-letter key. Next session they type `rc`.

Three decisions, each of which could have gone the lazier way:

**Claiming is explicit.** Auto-saving every accepted sequence accumulates a list
nobody trusts and everybody eventually clears. An explicit claim is a statement
that *this one* was worth keeping, and that is what makes the list short enough to
stay useful.

**A routine is validated at claim time.** The short key is a promise; a key that
sometimes fails costs more than no key, because now every key has to be checked
before it is trusted. So a sequence that would not run cannot be claimed, and the
refusal names the type error.

**Keys are deterministic and unique.** `rc` resolves to exactly one composition,
forever. The whole value is muscle memory, and a key that meant different things
on different days would be worse than typing the pipeline out.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..compose.pipeline import Composition
from ..compose.registry import VerbRegistry, registry as default_registry
from ..errors import SlpieError

#: A key: short, lowercase, alphanumeric. Short because it is typed constantly;
#: constrained because it shares a namespace with the verbs and must not shadow
#: one.
KEY = re.compile(r"^[a-z][a-z0-9-]{0,11}$")


class RoutineError(SlpieError):
    """A routine could not be claimed, or a key could not be resolved."""


@dataclass(frozen=True, slots=True)
class Routine:
    """A named composition, invoked by a short key."""

    key: str
    name: str
    pipeline: str
    claimed_at: int = 0
    actor: str = "user"
    note: str = ""
    uses: int = 0

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(
            fragment.strip().split()[0]
            for fragment in self.pipeline.split("|")
            if fragment.strip()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "pipeline": self.pipeline,
            "claimed_at": self.claimed_at, "actor": self.actor,
            "note": self.note, "uses": self.uses,
            "stages": list(self.stages),
        }

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> "Routine":
        return cls(
            key=body["key"], name=body.get("name", body["key"]),
            pipeline=body["pipeline"],
            claimed_at=int(body.get("claimed_at", 0)),
            actor=body.get("actor", "user"),
            note=body.get("note", ""), uses=int(body.get("uses", 0)),
        )

    def render(self) -> str:
        lines = [
            f"  {self.key:<6} {self.name}",
            f"         {self.pipeline}",
        ]
        if self.note:
            lines.append(f"         {self.note}")
        if self.uses:
            lines.append(f"         used {self.uses} time(s)")
        return "\n".join(lines)

    def __str__(self) -> str:
        return f"{self.key} → {self.pipeline}"


class Routines:
    """The claimed routines, keyed. Persisted as plain JSON."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        verbs: VerbRegistry | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.verbs = verbs if verbs is not None else default_registry()
        self._routines: dict[str, Routine] = {}
        if self.path and self.path.exists():
            self._load()

    # -- claiming --------------------------------------------------------

    def claim(
        self,
        name: str,
        pipeline: str,
        *,
        key: str = "",
        actor: str = "user",
        note: str = "",
    ) -> Routine:
        """Claim a composition as a routine. Refuses anything that would not run."""
        text = (pipeline or "").strip()
        if not text:
            raise RoutineError("there is nothing to claim: the pipeline is empty")

        validation = Composition.read(text, verbs=self.verbs).validate()
        if not validation.ok:
            raise RoutineError(
                f"this cannot be claimed because it would not run: "
                f"{validation.explain()}. A short key is a promise, and a key "
                f"that sometimes fails costs more than no key"
            )

        chosen = (key or self.mint(name, text)).lower()
        if not KEY.match(chosen):
            raise RoutineError(
                f"{chosen!r} is not a usable key: lowercase, starting with a "
                f"letter, up to 12 characters"
            )
        if chosen in self.verbs:
            raise RoutineError(
                f"{chosen!r} is already a verb, and a key that shadowed one would "
                f"make `slpie {chosen}` mean two things"
            )
        existing = self._routines.get(chosen)
        if existing is not None and existing.pipeline != text:
            raise RoutineError(
                f"{chosen!r} already resolves to `{existing.pipeline}`. A key that "
                f"meant different things on different days would be worse than "
                f"typing the pipeline out; choose another with --key"
            )

        routine = Routine(
            key=chosen, name=name or chosen, pipeline=text,
            claimed_at=int(time.time()), actor=actor, note=note,
        )
        self._routines[chosen] = routine
        self._save()
        return routine

    def claim_session(
        self,
        name: str,
        pipelines: Sequence[str],
        **options: Any,
    ) -> Routine:
        """Claim the longest accepted pipeline from a session.

        The *longest* rather than a concatenation, because accepted paths are
        alternatives explored, not stages of one thing — joining them with `|`
        would produce a composition nobody ran and that probably does not
        type-check. The longest one is the most refined thing the reviewer
        actually reached.
        """
        runnable = [
            text for text in pipelines
            if text and Composition.read(text, verbs=self.verbs).validate().ok
        ]
        if not runnable:
            raise RoutineError(
                "nothing accepted this session can be claimed: no accepted path "
                "was a runnable composition"
            )
        best = max(runnable, key=lambda text: (len(text.split("|")), len(text)))
        return self.claim(name, best, **options)

    def mint(self, name: str, pipeline: str) -> str:
        """A short, mnemonic, unused key.

        From the *name* first — a reviewer who calls it "release check" will reach
        for `rc` — falling back to the verbs' initials when the name gives nothing.
        """
        words = [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]
        candidates = []
        if words:
            candidates.append("".join(word[0] for word in words)[:4])
            candidates.append(words[0][:4])
        stages = [
            fragment.strip().split()[0].lower()
            for fragment in pipeline.split("|") if fragment.strip()
        ]
        if stages:
            candidates.append("".join(stage[0] for stage in stages)[:4])

        for candidate in candidates:
            if candidate and KEY.match(candidate) and self._free(candidate):
                return candidate

        base = (candidates[0] if candidates else "r")[:3] or "r"
        index = 2
        while not self._free(f"{base}{index}"):
            index += 1
        return f"{base}{index}"

    def _free(self, key: str) -> bool:
        return key not in self._routines and key not in self.verbs

    # -- using -----------------------------------------------------------

    def get(self, key: str) -> Routine | None:
        return self._routines.get(key.lower())

    def resolve(self, key: str) -> str:
        """A key to its composition. Raises rather than guessing."""
        routine = self.get(key)
        if routine is None:
            raise RoutineError(
                f"no routine is keyed {key!r}"
                + (f"; claimed: {', '.join(sorted(self._routines))}"
                   if self._routines else
                   ". Claim one with `slpie routine claim <name> '<pipeline>'`")
            )
        return routine.pipeline

    def used(self, key: str) -> Routine | None:
        """Record a use. What makes `slpie routine` show what actually pays off."""
        routine = self.get(key)
        if routine is None:
            return None
        counted = Routine(
            key=routine.key, name=routine.name, pipeline=routine.pipeline,
            claimed_at=routine.claimed_at, actor=routine.actor,
            note=routine.note, uses=routine.uses + 1,
        )
        self._routines[counted.key] = counted
        self._save()
        return counted

    def forget(self, key: str) -> bool:
        removed = self._routines.pop(key.lower(), None) is not None
        if removed:
            self._save()
        return removed

    # -- listing ---------------------------------------------------------

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._routines))

    def all(self) -> tuple[Routine, ...]:
        """Most used first — the ones that earned their key."""
        return tuple(sorted(
            self._routines.values(),
            key=lambda item: (-item.uses, item.key),
        ))

    def render(self) -> str:
        if not self._routines:
            return (
                "  no routines claimed yet.\n\n"
                "  Run something worth repeating, then:\n"
                "    slpie routine claim release-check 'discover . | link | findings'"
            )
        lines = ["  claimed routines — type the key to run one", ""]
        lines.extend(routine.render() for routine in self.all())
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"routines": [item.to_dict() for item in self.all()]}

    # -- persistence -----------------------------------------------------

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8",
        )

    def _load(self) -> None:
        assert self.path is not None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return
        for entry in body.get("routines", []):
            try:
                routine = Routine.from_dict(entry)
            except KeyError:
                continue
            self._routines[routine.key] = routine

    def __contains__(self, key: object) -> bool:
        return str(key).lower() in self._routines

    def __len__(self) -> int:
        return len(self._routines)

    def __iter__(self) -> Iterator[Routine]:
        return iter(self.all())
