"""The demo as data — one script, projected to a terminal or to screens.

The first version of this was a hundred `say("...")` calls, which was the wrong
shape for exactly the reason the rest of the platform is not built that way: a
sequence of print statements can only ever be a terminal transcript. It cannot
drive the UI, cannot be stepped through on a phone, and cannot be checked against
what it claims to demonstrate.

So a demo is a **`Script` of `Beat`s**, and each beat is semantic rather than
textual. A beat says *what it is demonstrating*, *which surface demonstrates it*,
*which screen it corresponds to*, and *what call produces it*. The terminal
renderer turns that into text; the UI turns the same beats into a guided walk
through its own views; the test suite asserts each beat's claim actually holds.

That is the same "one source, many projections" rule the verb registry follows,
applied to the demo itself. A demo that could drift from the product it
demonstrates is worse than none, because it teaches something that is not true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence


class Surface(str, Enum):
    """Where a beat is demonstrated. The demo covers all of them on purpose."""

    CLI = "cli"
    SCREEN = "screen"
    API = "api"
    PIPE = "pipe"          # two processes, the flow crossing as JSON
    CONTRACT = "contract"  # the generated client's own check

    @property
    def label(self) -> str:
        return {
            Surface.CLI: "terminal",
            Surface.SCREEN: "screen",
            Surface.API: "HTTP",
            Surface.PIPE: "OS pipe",
            Surface.CONTRACT: "generated client",
        }[self]


@dataclass(frozen=True, slots=True)
class Beat:
    """One thing the demo demonstrates, stated so any surface can present it.

    `claim` is the load-bearing field. It is what this beat asserts to be true, in
    one sentence, and `tests/test_slpie_demo.py` checks it — so a beat cannot claim
    something the platform does not do.
    """

    key: str                                   # stable id, for deep links
    title: str
    claim: str
    surface: Surface = Surface.CLI
    screen: str = ""                           # the UI view this maps to
    call: str = ""                             # the composition or route shown
    narration: tuple[str, ...] = ()            # the *why*, in prose
    run: Callable[["Stage"], "Outcome"] | None = None

    @property
    def interactive(self) -> bool:
        """Whether a screen can present this rather than only a terminal."""
        return bool(self.screen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "title": self.title, "claim": self.claim,
            "surface": self.surface.value, "surface_label": self.surface.label,
            "screen": self.screen, "call": self.call,
            "narration": list(self.narration),
            "interactive": self.interactive,
        }

    def __str__(self) -> str:
        return f"{self.key}: {self.title}"


@dataclass(slots=True)
class Stage:
    """The mutable world a beat runs against. Passed, never global."""

    root: Any = None
    facts: dict[str, Any] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a beat produced. `lines` are shown; `holds` is the assertion."""

    holds: bool = True
    lines: tuple[str, ...] = ()
    detail: str = ""
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "holds": self.holds, "lines": list(self.lines),
            "detail": self.detail, "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class Script:
    """An ordered set of beats, and the claim the whole thing makes."""

    name: str
    thesis: str
    beats: tuple[Beat, ...] = ()

    def keys(self) -> tuple[str, ...]:
        return tuple(beat.key for beat in self.beats)

    def beat(self, key: str) -> Beat | None:
        for candidate in self.beats:
            if candidate.key == key:
                return candidate
        return None

    def for_surface(self, surface: Surface) -> tuple[Beat, ...]:
        return tuple(beat for beat in self.beats if beat.surface is surface)

    def screens(self) -> tuple[str, ...]:
        """The UI views this demo walks through, in order, without repeats."""
        return tuple(dict.fromkeys(
            beat.screen for beat in self.beats if beat.screen
        ))

    def to_dict(self) -> dict[str, Any]:
        """What the UI fetches to drive the same demo as a guided walk."""
        return {
            "name": self.name, "thesis": self.thesis,
            "beats": [beat.to_dict() for beat in self.beats],
            "screens": list(self.screens()),
            "surfaces": sorted({beat.surface.value for beat in self.beats}),
        }

    def __len__(self) -> int:
        return len(self.beats)

    def __iter__(self) -> Iterator[Beat]:
        return iter(self.beats)
