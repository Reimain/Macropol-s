"""Measures — the aggregations, named once so two screens cannot disagree.

"How many findings" sounds like it has one answer. It does not: suppressed
findings, findings on retired nodes, and the same rule firing on two subjects
are three decisions, and a dashboard that made them one way while a report made
them another is how two numbers about the same estate end up in the same
meeting.

So a measure is a *declared* thing with its own definition in words, and every
consumer asks for it by name. That is the §24 argument — one registry, many
projections — applied to arithmetic.

── Every measure says whether it can be summed ──────────────────────────

`additive` is the field that prevents the classic warehouse mistake. A count of
findings adds up across severities; a *confidence* does not, and summing it
produces a number with no meaning that looks entirely plausible on a chart. A
measure that cannot be added says so, and the template engine refuses to put it
in a total row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

Rows = Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Measure:
    """One named number, its definition, and whether it may be added."""

    name: str
    doc: str
    #: The fact table it reads. A measure is meaningless without its grain.
    fact: str
    compute: Callable[[Rows], float]
    additive: bool = True
    #: How to render it: a count, a fraction of one, a duration in seconds.
    unit: str = "count"

    def of(self, rows: Rows) -> float:
        return self.compute(rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "doc": self.doc, "fact": self.fact,
            "additive": self.additive, "unit": self.unit,
        }


def _count(rows: Rows) -> float:
    return float(len(rows))


def _distinct(field: str) -> Callable[[Rows], float]:
    def compute(rows: Rows) -> float:
        return float(len({row.get(field) for row in rows if row.get(field) is not None}))
    return compute


def _mean(field: str) -> Callable[[Rows], float]:
    def compute(rows: Rows) -> float:
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        return sum(values) / len(values) if values else 0.0
    return compute


def _minimum(field: str) -> Callable[[Rows], float]:
    def compute(rows: Rows) -> float:
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        return min(values) if values else 0.0
    return compute


def _share(field: str, wanted: Any) -> Callable[[Rows], float]:
    def compute(rows: Rows) -> float:
        if not rows:
            return 0.0
        return sum(1 for row in rows if row.get(field) == wanted) / len(rows)
    return compute


def _where(field: str, wanted: Any) -> Callable[[Rows], float]:
    def compute(rows: Rows) -> float:
        return float(sum(1 for row in rows if row.get(field) == wanted))
    return compute


#: Every measure this platform publishes. A dashboard, a report and an API
#: answer read from here, so "how many findings" has exactly one definition.
MEASURES: tuple[Measure, ...] = (
    Measure(
        name="findings", fact="fact_finding", compute=_count,
        doc="Open findings. Suppressed ones are not in the fact table at all — "
            "a suppression is a decision with a reason on the record, and "
            "counting it here would make that decision invisible.",
    ),
    Measure(
        name="blocking", fact="fact_finding",
        compute=_where("blocks_release", True),
        doc="Findings that block a release. The number a release manager acts "
            "on, which is not the same as the number of findings.",
    ),
    Measure(
        name="subjects_affected", fact="fact_finding",
        compute=_distinct("subject"),
        additive=False,
        doc="How many distinct things have something against them. Not "
            "additive: the same subject appears under several severities, and "
            "adding the per-severity figures double-counts it.",
    ),
    Measure(
        name="relationships", fact="fact_edge", compute=_count,
        doc="Recorded relationships between elements — every live edge in the "
            "selection, whatever evidence it rests on. Read beside "
            "`inferred_share`, which says how many were read rather than guessed.",
    ),
    Measure(
        name="mean_confidence", fact="fact_edge", compute=_mean("confidence"),
        additive=False, unit="fraction",
        doc="Average confidence across relationships. Not additive, and worth "
            "reading beside `inferred_share` rather than alone: a high mean "
            "with a long weak tail is a different estate from a uniform one.",
    ),
    Measure(
        name="weakest_link", fact="fact_edge", compute=_minimum("confidence"),
        additive=False, unit="fraction",
        doc="The lowest confidence on any relationship in the selection. An "
            "answer that traverses this set is bounded by it — the same rule "
            "the flight rail applies to a single path, over a whole slice.",
    ),
    Measure(
        name="inferred_share", fact="fact_edge",
        compute=_share("read", False), additive=False, unit="fraction",
        doc="The fraction of relationships inferred rather than read from a "
            "file. The single number that says how much of a picture is "
            "evidence and how much is reasoning.",
    ),
    Measure(
        name="elements", fact="fact_element", compute=_count,
        doc="Elements the platform currently believes exist. Declared-only "
            "elements are included and counted again by `unobserved`, "
            "because something declared and never seen still exists as a claim.",
    ),
    Measure(
        name="undeclared", fact="fact_element",
        compute=_where("declared", False),
        doc="Observed and never declared — shadow dependencies and "
            "undocumented egress. The reconciliation delta, as a number.",
    ),
    Measure(
        name="unobserved", fact="fact_element",
        compute=_where("observed", False),
        doc="Declared and never observed. Either it is gone, or the platform "
            "was not given the access to see it, and those are not the same.",
    ),
)


def measure(name: str) -> Measure | None:
    return next((item for item in MEASURES if item.name == name), None)


def for_fact(fact: str) -> tuple[Measure, ...]:
    return tuple(item for item in MEASURES if item.fact == fact)


def summarise(fact: str, rows: Rows) -> dict[str, Any]:
    """Every measure defined on this fact, computed over these rows.

    Returned with `additive` alongside each value rather than as bare numbers,
    so a caller building a total row can tell which columns it may add — the
    distinction is the whole reason the flag exists.
    """
    return {
        item.name: {
            "value": item.of(rows), "unit": item.unit,
            "additive": item.additive, "doc": item.doc,
        }
        for item in for_fact(fact)
    }
