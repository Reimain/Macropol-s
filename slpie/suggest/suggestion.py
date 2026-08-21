"""A suggestion — a path, not a definition.

The distinction is the whole point of this module. A definition tells a reviewer
what a word means; it does not tell them what to do, and a reviewer who does not
know what to do next stops reviewing. Pointing at a dictionary is the failure mode
this design exists to avoid.

So a `Suggestion` carries three things a glossary entry does not:

* **`pipeline`** — a composition they can run *right now*. Selected from the type
  graph, so it is valid by construction: a suggestion can never be something the
  platform cannot do.
* **`reveals`** — what they will know afterwards that they do not know now. Not
  "a lockfile pins versions" but "which of your two lockfiles won, and where".
* **`key`** — a short deterministic token. This is what gets typed, and after a
  few sessions it is what gets typed *instead of* composing four stages. Short
  keys are the ergonomics of a config section name: one token, one meaning.

`because` carries evidence rather than prose wherever it can, so the guidance
terminates in a file and a line like every other claim in the platform. A
suggestion whose justification is a sentence somebody wrote is a suggestion the
reviewer has to take on faith.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..domain.evidence import Evidence
from ..domain.identity import digest


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One next move worth making, with the reason and the payoff stated."""

    key: str                              # short, typeable, deterministic
    pipeline: str                         # always valid against the registry
    because: str                          # why this is offered now
    reveals: str                          # what you will know afterwards
    evidence: tuple[Evidence, ...] = ()
    gain: float = 0.0                     # expected information gain
    confidence: float = 0.0
    cost: str = "cheap"                   # cheap · moderate · expensive
    needs_environment: bool = False
    origin: str = "graph"                 # which generator produced it

    @property
    def id(self) -> str:
        return digest({"pipeline": self.pipeline, "reveals": self.reveals})

    @property
    def grounded(self) -> bool:
        """Whether the guidance can be checked rather than believed."""
        return bool(self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "key": self.key, "pipeline": self.pipeline,
            "because": self.because, "reveals": self.reveals,
            "gain": round(self.gain, 4), "confidence": round(self.confidence, 4),
            "cost": self.cost, "grounded": self.grounded,
            "needs_environment": self.needs_environment, "origin": self.origin,
            "sources": [item.location.reference for item in self.evidence],
        }

    def render(self, *, width: int = 74) -> str:
        """One suggestion, for a terminal."""
        lines = [f"  [{self.key}] {self.pipeline}"]
        lines.extend(f"      {line}" for line in _wrap(self.reveals, width - 6))
        if self.because:
            lines.extend(f"      · {line}" for line in _wrap(self.because, width - 8))
        if self.evidence:
            sources = ", ".join(
                item.location.reference for item in self.evidence[:3]
            )
            lines.append(f"      · from {sources}")
        if self.needs_environment:
            lines.append("      · needs an environment")
        return "\n".join(lines)

    def __str__(self) -> str:
        return f"[{self.key}] {self.pipeline}"


@dataclass(frozen=True, slots=True)
class Suggestions:
    """What was offered for one touch, and why in this order."""

    touch: Any
    items: tuple[Suggestion, ...] = ()
    reason: str = ""                      # why this ordering
    retired: tuple[str, ...] = ()         # dismissed too often to offer again

    @property
    def empty(self) -> bool:
        return not self.items

    def by_key(self, key: str) -> Suggestion | None:
        for item in self.items:
            if item.key == key:
                return item
        return None

    def keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "touch": self.touch.to_dict() if hasattr(self.touch, "to_dict") else {},
            "suggestions": [item.to_dict() for item in self.items],
            "reason": self.reason,
            "retired": list(self.retired),
        }

    def render(self, *, width: int = 74) -> str:
        """What the CLI prints after a command. Terse when nothing is wrong."""
        if self.empty:
            return "  nothing further suggests itself here."

        header = (
            f"  {len(self.items)} suggestion(s) — type the key to run one"
        )
        body = "\n\n".join(item.render(width=width) for item in self.items)
        footer = f"\n\n  why this order: {self.reason}" if self.reason else ""
        return f"{header}\n\n{body}{footer}"

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(str(text).split()), width=max(20, width)) or [""]


def mint_key(pipeline: str, taken: Iterable[str] = ()) -> str:
    """A short deterministic key for a pipeline.

    Deterministic so that the same suggestion has the same key across sessions —
    muscle memory is the entire value of a short key, and a key that moved between
    runs would be worse than no key at all. Built from the verbs' initials, which
    makes it mnemonic (`discover | link | findings` → `dlf`) rather than opaque.
    """
    reserved = set(taken)
    verbs = [
        fragment.strip().split()[0]
        for fragment in pipeline.split("|")
        if fragment.strip()
    ]
    if not verbs:
        return "x"

    candidate = "".join(verb[0] for verb in verbs).lower()[:4]
    if candidate and candidate not in reserved:
        return candidate

    # Lengthen before numbering: `dl` → `dli` reads better than `dl2`, and stays
    # mnemonic. Numbering is the last resort rather than the first.
    for length in range(2, 5):
        longer = "".join(verb[:length] for verb in verbs).lower()[:6]
        if longer and longer not in reserved:
            return longer

    index = 2
    while f"{candidate}{index}" in reserved:
        index += 1
    return f"{candidate}{index}"
