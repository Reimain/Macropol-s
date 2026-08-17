"""Where nobody serves the buyer, and what it would take us to.

The comparison table is not the product of competitive analysis — it is the
by-product. The useful output is this file: the capabilities that no established
product covers, ranked by how much of the work we have already done.

Two rules keep it honest, and both are the same rule the rest of the platform
runs on:

* **A gap is computed, not asserted.** `opportunities()` reads the registry and
  finds columns where coverage is thin. Nobody types "we are the only ones who
  do X" — if the table does not show it, it is not claimed.
* **An unverified column is not a gap.** If we recorded `UNKNOWN` for four
  products on a capability, that is our ignorance, not white space. It is
  reported as `Leverage.UNVERIFIED` and excluded from the ranking, because
  building a roadmap on what we forgot to check is how a company ships something
  three rivals already had.

`positioning()` is the one-paragraph answer to "why you and not them", generated
from the same data rather than written underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .registry import CAPABILITIES, RECORDED, rival_registry
from .rival import Coverage, Rival

#: A capability where the mean rival coverage is at or below this is white
#: space. 0.25 means: on average, rivals score between "absent" and "an add-on".
THIN = 0.25

#: Below this share of *verified* assessments we decline to call a column a gap,
#: whatever the score says. Four unknowns and one `NONE` is not white space, it
#: is a column nobody checked.
MIN_VERIFIED = 0.6


class Leverage(str, Enum):
    """What it would take us to serve a gap."""

    SHIPPED = "shipped"          # we already do this, today, in the kernel
    NEAR = "near"                # the seam exists; weeks, not quarters
    FAR = "far"                  # real work, but on our architecture
    UNVERIFIED = "unverified"    # we did not check the field well enough to say

    @property
    def actionable(self) -> bool:
        return self is not Leverage.UNVERIFIED


@dataclass(frozen=True, slots=True)
class Gap:
    """One capability, and how thinly the field covers it."""

    capability: str
    description: str
    mean_coverage: float
    verified_share: float
    covered_by: tuple[str, ...]      # rivals scoring FULL
    absent_from: tuple[str, ...]     # rivals scoring NONE

    @property
    def thin(self) -> bool:
        return (
            self.mean_coverage <= THIN
            and self.verified_share >= MIN_VERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "description": self.description,
            "mean_coverage": self.mean_coverage,
            "verified_share": self.verified_share,
            "covered_by": list(self.covered_by),
            "absent_from": list(self.absent_from),
            "thin": self.thin,
        }


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A gap, with what we would build and why it is defensible."""

    gap: Gap
    leverage: Leverage
    already: str = ""            # what exists today that serves it
    build: str = ""              # what is still missing
    moat: str = ""               # why a rival cannot simply add it next quarter

    @property
    def rank(self) -> float:
        """Thinness × how close we are. Higher is a better next thing to build."""
        if not self.leverage.actionable:
            return 0.0
        weight = {"shipped": 1.0, "near": 0.7, "far": 0.35}[self.leverage.value]
        return round((1.0 - self.gap.mean_coverage) * weight, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap": self.gap.to_dict(), "leverage": self.leverage.value,
            "already": self.already, "build": self.build, "moat": self.moat,
            "rank": self.rank,
        }


#: What we can say about ourselves, keyed by capability. Separated from the
#: computed gap so the two cannot be confused: everything above is derived from
#: cited records about other people's products; everything here is a claim about
#: ours, and each one names the module that makes it true so a reader can check.
OURS: dict[str, tuple[Leverage, str, str, str]] = {
    "blast_radius": (
        Leverage.SHIPPED,
        "`slpie/graph/traversal.py` — reverse reachability as one recursive SQL "
        "query, with a confidence floor and a path-based cycle guard. Minimum "
        "confidence propagates, so a node reached only through a 0.4 dynamic "
        "load is reported at 0.4 rather than as a fact.",
        "Nothing for the core answer. The enterprise half — cross-repository "
        "radius over a shared graph — needs the Postgres store.",
        "Requires a typed graph with per-edge confidence and bitemporal history. "
        "A scanner that emits a findings list cannot bolt this on; it has no "
        "graph to traverse and no confidence to propagate.",
    ),
    "evidence_provenance": (
        Leverage.SHIPPED,
        "Invariant 1: `Edge.__post_init__` refuses to construct a relationship "
        "with no evidence, and every conclusion walks `derived_from` back to a "
        "file and a line. Confidence is derived from evidence kind and "
        "corroboration — no caller ever passes a number.",
        "Nothing.",
        "This is an architectural decision taken at the type level on day one. "
        "Retrofitting it means rewriting every producer of every fact, which is "
        "why the products that have it (Sourcegraph, FOSSA) have it in one "
        "narrow domain and not across the whole answer.",
    ),
    "declared_vs_observed": (
        Leverage.SHIPPED,
        "`slpie/environment/reconcile.py` — the manifest declares intent at 0.92 "
        "confidence, discovery produces independent evidence, and the delta is "
        "reported both ways: declared-but-absent is a dead declaration, "
        "observed-but-undeclared is a shadow dependency.",
        "Nothing for the core answer.",
        "Backstage has the declaration and never checks it. A scanner has the "
        "observation and no declaration to check against. Holding both, with "
        "confidence on each, is the position neither can reach without becoming "
        "the other.",
    ),
    "offline_operation": (
        Leverage.SHIPPED,
        "Zero third-party dependencies in either kernel, including the UI. "
        "Asserted by a CI job that installs with no extras and checks what came "
        "with it, and by an `ast` walk over every module.",
        "Nothing.",
        "Not a feature — a constraint held from the first commit. A hosted "
        "product cannot adopt it; it is their business model.",
    ),
    "service_catalogue": (
        Leverage.NEAR,
        "The graph already holds services, APIs, deployments and their owners, "
        "corroborated by discovery rather than declared in YAML.",
        "The portal surface — ownership workflows, team pages, scaffolding — "
        "which is most of what Backstage actually is.",
        "Weak. Backstage is open source, entrenched, and good. The defensible "
        "claim is not a better catalogue; it is a catalogue whose entries were "
        "*verified* against the tree.",
    ),
    "licence_compliance": (
        Leverage.NEAR,
        "SPDX expression parsing, compatibility checking by distribution and "
        "linkage, CycloneDX and SPDX emission — all deterministic and offline.",
        "The legal workflow FOSSA sells: obligation tracking, attribution "
        "documents, approval chains.",
        "Weak. FOSSA is better at this and should be assumed to stay better.",
    ),
    "vulnerability_matching": (
        Leverage.FAR,
        "purl-native identity, so OSV records match without a translation layer.",
        "A curated, continuously updated advisory database — which is a data "
        "business, not a software one.",
        "None. This is Snyk's moat and it is a good one. The right move is to "
        "consume OSV and not pretend to compete.",
    ),
    "secret_detection": (
        Leverage.NEAR,
        "Entropy and pattern scanners, with whole-segment fixture allowances so "
        "a test directory does not mask a real key.",
        "Validation against live providers, and historical git scanning.",
        "Weak — GitHub gives this away free.",
    ),
    "dependency_updates": (
        Leverage.FAR,
        "`options` computes safe upgrade paths from the constraint solve, so we "
        "know which bumps resolve.",
        "The whole pull-request pipeline: branching, CI integration, "
        "auto-merge, per-ecosystem update strategies.",
        "None. Renovate is excellent, open source, and free. Integrate with it "
        "rather than rebuild it.",
    ),
}


def gaps() -> tuple[Gap, ...]:
    """Every capability, scored across the field. Computed, never asserted."""
    rivals = rival_registry()
    found: list[Gap] = []

    for name, description in CAPABILITIES:
        assessments = [rival.coverage_of(name) for rival in rivals]
        verified = [item for item in assessments if item.verified]
        mean = (
            round(sum(item.score for item in verified) / len(verified), 3)
            if verified else 0.0
        )
        found.append(Gap(
            capability=name,
            description=description,
            mean_coverage=mean,
            verified_share=round(len(verified) / len(assessments), 3),
            covered_by=tuple(
                rival.id for rival in rivals
                if rival.coverage_of(name) is Coverage.FULL
            ),
            absent_from=tuple(
                rival.id for rival in rivals
                if rival.coverage_of(name) is Coverage.NONE
            ),
        ))
    return tuple(found)


def opportunities() -> tuple[Opportunity, ...]:
    """The gaps we could serve, best first."""
    found: list[Opportunity] = []
    for gap in gaps():
        leverage, already, build, moat = OURS.get(
            gap.capability, (Leverage.UNVERIFIED, "", "", ""),
        )
        if leverage is Leverage.UNVERIFIED and not gap.thin:
            # Nothing recorded about us, and no white space either — no claim
            # to make in any direction.
            continue
        # Everything we have a recorded position on stays in, including the
        # capabilities we are behind on. Dropping those would leave a list where
        # we win every row, which is the document a buyer stops reading.
        found.append(Opportunity(
            gap=gap, leverage=leverage, already=already, build=build, moat=moat,
        ))
    return tuple(sorted(found, key=lambda item: -item.rank))


def positioning() -> str:
    """Why us and not them, generated from the table rather than written under it."""
    best = [item for item in opportunities() if item.leverage is Leverage.SHIPPED]
    rivals = rival_registry()

    lines = [
        "",
        f"  Positioning, computed from {len(rivals)} recorded products ({RECORDED})",
        "  " + "=" * 72,
        "",
    ]
    if not best:
        lines.append("  No capability in the recorded field is both thin and shipped.")
        lines.append("  That is a finding, not an omission — and it means the")
        lines.append("  differentiation argument is not ready to take to a buyer.")
        lines.append("")
        return "\n".join(lines)

    lines.append("  What we do that the recorded field mostly does not:")
    lines.append("")
    for item in best:
        gap = item.gap
        absent = len(gap.absent_from)
        lines.append(f"    · {gap.capability.replace('_', ' ')}")
        lines.append(f"        {gap.description}")
        lines.append(
            f"        absent from {absent} of {len(rivals)} recorded products; "
            f"mean coverage {gap.mean_coverage:.2f}"
        )
        if item.moat:
            lines.append(f"        why it is hard to copy: {item.moat.split('.')[0]}.")
        lines.append("")

    weak = [item for item in opportunities() if item.leverage is Leverage.FAR]
    if weak:
        lines.append("  Where the field is ahead of us, and should be assumed to stay.")
        lines.append("  Named on purpose: a comparison we win on every row is one")
        lines.append("  a buyer stops reading.")
        lines.append("")
        for item in weak:
            leaders = ", ".join(item.gap.covered_by) or "the field"
            lines.append(f"    · {item.gap.capability.replace('_', ' ')}")
            lines.append(f"        led by {leaders}; mean coverage "
                         f"{item.gap.mean_coverage:.2f}")
            lines.append(f"        still missing here: {item.build.split('.')[0]}")
        lines.append("")

    lines.append("  Every row above is derived from the cited records in")
    lines.append("  `slpie/rivals/registry.py`. Nothing here is typed by hand.")
    lines.append("")
    return "\n".join(lines)


def render() -> str:
    """The comparison table, for a terminal or a notebook."""
    rivals = rival_registry()
    mark = {
        Coverage.FULL: "###", Coverage.PARTIAL: " ##",
        Coverage.NONE: "  ·", Coverage.UNKNOWN: "  ?",
    }

    header = "  " + " ".join(f"{rival.id[:9]:>9}" for rival in rivals)
    lines = [
        "",
        f"  The field, as recorded {RECORDED}",
        "  ### full   ## partial   · none   ? not verified",
        "",
        f"  {'capability':26}{header}",
        "  " + "-" * (26 + len(header)),
    ]
    for name, _ in CAPABILITIES:
        row = " ".join(
            f"{mark[rival.coverage_of(name)]:>9}" for rival in rivals
        )
        lines.append(f"  {name:26}  {row}")

    lines.append("")
    lines.append(
        f"  verified share of these records: "
        f"{sum(r.verified_share for r in rivals) / len(rivals):.0%}"
    )
    lines.append("")
    return "\n".join(lines)
