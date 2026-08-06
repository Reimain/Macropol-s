"""Context depth: how far the kernel is allowed to go.

"Put it in the environment and it builds itself" needs a stop condition, or the
only honest description of the system is *runaway*. Depth is that condition. It
is a single dial the operator turns, and every stage checks it before acting —
so the difference between "look but don't touch" and "govern this context" is
one argument, not a different code path.

Each level strictly contains the ones below it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from ..errors import OrchestrationError

MIB = 1 << 20


class Depth(IntEnum):
    """How deep into the context the kernel is permitted to reach."""

    SENSE = 0      # discover sources and infer shapes; touch nothing
    MODEL = 1      # + register shapes and propose migrations
    GENERATE = 2   # + emit and merge code
    TRANSFORM = 3  # + run sandboxed transformations
    GOVERN = 4     # + apply safe migrations, delegate to peers, iterate

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: "Depth | str | int") -> "Depth":
        if isinstance(value, Depth):
            return value
        if isinstance(value, int):
            return cls(max(0, min(int(value), int(cls.GOVERN))))
        text = str(value).strip().upper()
        if text.isdigit():
            return cls.parse(int(text))
        try:
            return cls[text]
        except KeyError as exc:
            raise OrchestrationError(
                f"unknown depth {value!r}; expected one of: "
                + ", ".join(d.label for d in cls)
            ) from exc

    def allows(self, stage: "Depth") -> bool:
        return self >= stage

    def describe(self) -> str:
        return {
            Depth.SENSE: "discover sources and infer shapes; make no changes",
            Depth.MODEL: "register shapes and propose migrations, but write no code",
            Depth.GENERATE: "generate and merge code for observed shapes",
            Depth.TRANSFORM: "run approved transformations over captured data",
            Depth.GOVERN: "apply safe migrations, delegate to peers, and iterate to a stable context",
        }[self]


@dataclass(frozen=True, slots=True)
class Budget:
    """The other half of the stop condition: how much, not how deep."""

    max_files: int = 500
    max_rows_per_payload: int = 5000
    max_cycles: int = 3
    max_seconds: float = 600.0
    memory_bytes: int = 256 * MIB
    per_payload_bytes: int = 64 * MIB
    #: Stop early once a cycle produces no new events.
    stop_when_stable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_files": self.max_files,
            "max_rows_per_payload": self.max_rows_per_payload,
            "max_cycles": self.max_cycles,
            "max_seconds": self.max_seconds,
            "memory_bytes": self.memory_bytes,
            "stop_when_stable": self.stop_when_stable,
        }


#: Sensible presets. `SURVEY` is the safe default for pointing at something new.
SURVEY = Budget(max_files=200, max_cycles=1, max_seconds=120.0)
STANDARD = Budget()
DEEP = Budget(max_files=5000, max_rows_per_payload=50_000, max_cycles=6, max_seconds=3600.0,
              memory_bytes=1024 * MIB)
