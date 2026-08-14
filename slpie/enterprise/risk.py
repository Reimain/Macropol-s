"""The risk register — findings aggregated onto the things that carry them.

A findings list is a work queue: it is ordered by severity and read top-down
until somebody runs out of afternoon. A risk register is a different artifact for
a different reader — it answers "which parts of this estate are dangerous", which
a list of two hundred individual findings does not, because the component with
one critical and the component with forty mediums look the same in a list.

So the register **aggregates by subject** and reports both dimensions:

* **severity** — the worst single thing wrong with it.
* **exposure** — how much depends on it, from the graph's own edges.

The product is what makes a register useful. A critical vulnerability in a leaf
nobody imports is a smaller problem than a high one in the package forty modules
reach, and a register that ranked purely by severity would put them the wrong way
round. That is the one judgement this module makes, and it is stated rather than
hidden: `Risk.rank` is a documented formula over two numbers the graph already
derived, not a score somebody tuned.

**Nothing here invents a severity.** Every risk traces to the findings that
produced it, and every finding carries its own evidence — so a risk register cell
walks back to a file and a line exactly like everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..domain.finding import Finding
from ..domain.lifecycle import RiskClass, Severity
from .view import View, identifier, unique

#: How far to walk when measuring exposure. Beyond this everything reaches
#: everything and the number stops discriminating.
EXPOSURE_DEPTH = 4

#: Exposure is compressed before it multiplies severity. Without this a hub with
#: four hundred dependents would swamp severity entirely and the register would
#: rank purely by popularity.
def _compress(count: int) -> float:
    """Dependents → an exposure multiplier between 1.0 and roughly 3.5."""
    from math import log2

    return 1.0 + log2(1 + max(0, count)) / 3.0


@dataclass(frozen=True, slots=True)
class Risk:
    """One subject, everything wrong with it, and how far the damage reaches."""

    subject: str
    label: str
    findings: tuple[Finding, ...] = ()
    dependents: int = 0

    @property
    def severity(self) -> Severity:
        """The worst single thing wrong with this subject."""
        return max(
            (finding.severity for finding in self.findings),
            key=lambda item: item.rank,
            default=Severity.INFO,
        )

    @property
    def exposure(self) -> float:
        return round(_compress(self.dependents), 4)

    @property
    def rank(self) -> float:
        """severity rank × compressed exposure. The register's whole judgement.

        Stated as a formula rather than tuned as a score, so a reader who
        disagrees can see exactly what they disagree with. Severity dominates —
        a critical always outranks a high at equal exposure — and exposure
        breaks ties between equal severities, which is the ordering an operator
        triaging a release actually wants.
        """
        return round(self.severity.rank * self.exposure, 4)

    @property
    def risk_class(self) -> RiskClass:
        """The aggregate class, derived from the worst finding. Never assigned."""
        return self.severity.to_risk()

    @property
    def blocking(self) -> int:
        return sum(1 for finding in self.findings if finding.blocks_release)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "label": self.label,
            "severity": self.severity.value,
            "risk_class": self.risk_class.value,
            "findings": len(self.findings),
            "blocking": self.blocking,
            "dependents": self.dependents,
            "exposure": self.exposure,
            "rank": self.rank,
            "kinds": sorted({finding.kind.value for finding in self.findings}),
        }

    def __str__(self) -> str:
        return (
            f"{self.label}: {self.severity.value}, {len(self.findings)} finding(s), "
            f"{self.dependents} dependent(s)"
        )


def _dependents(graph: Any, node_id: str, *, depth: int = EXPOSURE_DEPTH) -> int:
    """How many distinct things reach this one, bounded.

    Uses the graph's own traversal where one is available, so exposure agrees
    with `impact` rather than being a second reachability implementation. A
    graph that cannot traverse yields 0, which reads as "unmeasured" — and
    because exposure only ever *multiplies*, an unmeasured subject falls back to
    ranking purely by severity rather than silently sorting to the bottom.
    """
    radius = getattr(graph, "blast_radius", None)
    if radius is None:
        return 0
    try:
        return len(radius(node_id, max_depth=depth))
    except Exception:  # noqa: BLE001 - a store's traversal is not this module's problem
        return 0


def register(
    findings: Iterable[Finding],
    *,
    graph: Any = None,
    labels: Mapping[str, str] | None = None,
) -> tuple[Risk, ...]:
    """Findings → risks, worst first.

    `labels` maps a subject id to something a human recognises. Supplied rather
    than looked up so this function works over a findings list alone, which is
    what lets `govern | risk` run with no graph at all.
    """
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.subject, []).append(finding)

    named = dict(labels or {})
    risks = [
        Risk(
            subject=subject,
            label=named.get(subject, "") or _shorten(subject),
            findings=tuple(items),
            dependents=_dependents(graph, subject) if graph is not None else 0,
        )
        for subject, items in grouped.items()
    ]
    # Rank descending, then subject, so equal risks order stably across runs.
    return tuple(sorted(risks, key=lambda risk: (-risk.rank, risk.subject)))


def _shorten(subject: str) -> str:
    """A subject id → something readable when no label was supplied.

    A blake2b node id has no readable form, so it is truncated and marked rather
    than printed in full — forty hex characters in a register column tells a
    reader nothing and costs them the width.
    """
    if subject.startswith(("pkg:", "urn:", "file:")):
        return subject
    if len(subject) >= 32 and all(c in "0123456789abcdef" for c in subject.lower()):
        return f"node:{subject[:12]}"
    return subject


def risk_view(
    findings: Iterable[Finding],
    *,
    graph: Any = None,
    labels: Mapping[str, str] | None = None,
) -> View:
    """The register as an emittable view."""
    risks = register(findings, graph=graph, labels=labels)
    rows = unique([
        {
            "id": identifier(risk.label, fallback="RISK"),
            "label": risk.label,
            "kind": risk.risk_class.value,
            **{
                key: value for key, value in risk.to_dict().items()
                if key not in ("label", "subject")
            },
        }
        for risk in risks
    ])
    return View(
        name="risk",
        doc=(
            "Risk register: every subject carrying a finding, ranked by the "
            "worst thing wrong with it multiplied by how much depends on it"
        ),
        elements=rows,
        diagram="graph LR",
    )


def heat_map(risks: Sequence[Risk]) -> str:
    """The register as a severity × exposure grid.

    A table rather than a chart, because this has to render in a terminal, in a
    markdown file and in a PR comment — and a chart that only renders in one of
    those is a chart people stop looking at.
    """
    bands = (
        ("isolated", 0, 1),
        ("few", 1, 4),
        ("many", 4, 20),
        ("pervasive", 20, 10 ** 9),
    )
    severities = (
        Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
        Severity.LOW, Severity.INFO,
    )

    counts: dict[tuple[str, str], int] = {}
    for risk in risks:
        band = next(
            name for name, low, high in bands
            if low <= risk.dependents < high
        )
        counts[(risk.severity.value, band)] = counts.get(
            (risk.severity.value, band), 0
        ) + 1

    width = max(len(name) for name, _low, _high in bands) + 2
    header = "  " + "severity".ljust(10) + "".join(
        name.rjust(width) for name, _low, _high in bands
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    for severity in severities:
        row = "  " + severity.value.ljust(10)
        for name, _low, _high in bands:
            count = counts.get((severity.value, name), 0)
            row += (str(count) if count else "·").rjust(width)
        lines.append(row)
    return "\n".join(lines)


def report(risks: Sequence[Risk], *, limit: int = 20) -> str:
    """The register as markdown, for `architecture/risk_report.md`."""
    lines = [
        "# Risk register",
        "",
        f"{len(risks)} subject(s) carry at least one finding.",
        "",
        "## Heat map",
        "",
        "```",
        heat_map(risks),
        "```",
        "",
        "## Ranked",
        "",
        "| rank | subject | severity | findings | blocking | dependents |",
        "| ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for risk in risks[:limit]:
        lines.append(
            f"| {risk.rank} | {risk.label} | {risk.severity.value} | "
            f"{len(risk.findings)} | {risk.blocking} | {risk.dependents} |"
        )
    if len(risks) > limit:
        lines.append("")
        lines.append(f"_{len(risks) - limit} further subject(s) not shown._")
    return "\n".join(lines) + "\n"
