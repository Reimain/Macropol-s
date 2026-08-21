"""One product on the market, and what can actually be verified about it.

The type refuses to hold an unfalsifiable claim. A `Rival` with no homepage, a
`Capability` assessment with no source, or a summary nobody wrote will not
construct — the same guard `gratimos/reference/protocols.py` puts on a protocol
entry, for the same reason: a landscape survey ages into confident wrongness
faster than any code does, and the only defence is making every line checkable.

`Coverage.UNKNOWN` is the important value here. It means *we did not verify
this*, and it counts against the confidence of our own comparison rather than
against the product. A table with no unknowns in it has usually been filled in
from memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import SlpieError


class RivalError(SlpieError):
    """A competitive record is unusable — missing a citation, or unfalsifiable."""


class Segment(str, Enum):
    """Which buyer a product is sold to. Products blur; budgets do not."""

    SCA = "sca"                       # software composition analysis
    SAST = "sast"                     # static application security testing
    SUPPLY_CHAIN = "supply_chain"     # provenance, attestation, typosquats
    DEP_UPDATES = "dep_updates"       # automated dependency bumps
    CATALOGUE = "catalogue"           # service catalogues, developer portals
    CODE_SEARCH = "code_search"       # search and large-scale refactoring
    EA = "enterprise_architecture"    # EA suites, TOGAF tooling
    DATA_PLATFORM = "data_platform"   # notebooks, warehouses, data science
    LICENSING = "licensing"           # licence compliance and SBOM


class Coverage(str, Enum):
    """How well a product serves one capability, as far as we could verify."""

    FULL = "full"           # documented, and it is the product's core job
    PARTIAL = "partial"     # documented, but limited or an add-on
    NONE = "none"           # documented as out of scope, or plainly absent
    UNKNOWN = "unknown"     # we could not verify it — counts against *us*

    @property
    def score(self) -> float:
        return {"full": 1.0, "partial": 0.5, "none": 0.0, "unknown": 0.0}[self.value]

    @property
    def verified(self) -> bool:
        """Whether this assessment says anything at all about the product."""
        return self is not Coverage.UNKNOWN


@dataclass(frozen=True, slots=True)
class Evidence:
    """Where a claim was checked, and when.

    `checked` is not decoration. A capability verified eighteen months ago is a
    guess, and the honest thing to do with a guess is label it — which is what
    lets `Rival.stale` refuse to let one sit in a comparison unremarked.
    """

    source: str                       # a URL somebody can open
    checked: str                      # YYYY-MM
    quote: str = ""                   # what the source actually said

    def __post_init__(self) -> None:
        if not self.source.startswith("http"):
            raise RivalError(
                f"evidence cites {self.source!r}, which is not a URL anybody can "
                f"open; a competitive claim nobody can check is marketing"
            )
        if len(self.checked) != 7 or self.checked[4] != "-":
            raise RivalError(
                f"evidence checked {self.checked!r}; expected YYYY-MM, because a "
                f"claim with no date cannot be told from a stale one"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "checked": self.checked, "quote": self.quote}


@dataclass(frozen=True, slots=True)
class Capability:
    """One product's coverage of one capability, with where it was checked."""

    capability: str
    coverage: Coverage
    evidence: Evidence | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.coverage.verified and self.evidence is None:
            raise RivalError(
                f"claiming {self.capability!r} is {self.coverage.value} for a "
                f"product requires a source. Use Coverage.UNKNOWN to say we did "
                f"not check — that is an honest answer; an uncited claim is not"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "coverage": self.coverage.value,
            "note": self.note,
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


@dataclass(frozen=True, slots=True)
class Rival:
    """One product, as recorded."""

    id: str
    name: str
    vendor: str
    segments: tuple[Segment, ...]
    homepage: str
    summary: str
    capabilities: tuple[Capability, ...] = ()
    open_source: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.homepage.startswith("http"):
            raise RivalError(f"rival {self.id!r} cites no homepage")
        if not self.summary.strip():
            raise RivalError(
                f"rival {self.id!r} has no summary; an entry nobody described is "
                f"one nobody checked"
            )
        seen = [item.capability for item in self.capabilities]
        if len(seen) != len(set(seen)):
            raise RivalError(f"rival {self.id!r} assesses a capability twice")

    def coverage_of(self, capability: str) -> Coverage:
        for item in self.capabilities:
            if item.capability == capability:
                return item.coverage
        return Coverage.UNKNOWN

    def assessment_of(self, capability: str) -> Capability | None:
        return next(
            (item for item in self.capabilities if item.capability == capability),
            None,
        )

    @property
    def verified_share(self) -> float:
        """How much of this record we actually checked.

        Reported beside every comparison. A product we verified at 30% appearing
        to lose a feature race is not evidence of anything.
        """
        if not self.capabilities:
            return 0.0
        verified = sum(1 for item in self.capabilities if item.coverage.verified)
        return round(verified / len(self.capabilities), 3)

    def stale(self, *, before: str) -> tuple[str, ...]:
        """Capabilities whose evidence predates `before` (YYYY-MM)."""
        return tuple(
            item.capability
            for item in self.capabilities
            if item.evidence is not None and item.evidence.checked < before
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "vendor": self.vendor,
            "segments": [item.value for item in self.segments],
            "homepage": self.homepage, "summary": self.summary,
            "open_source": self.open_source, "note": self.note,
            "verified_share": self.verified_share,
            "capabilities": [item.to_dict() for item in self.capabilities],
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.vendor})"
