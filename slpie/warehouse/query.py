"""Grouping — the one thing a star schema exists to make cheap.

`measures.py` answers "how many findings"; this answers "how many findings *by
severity*", which is the question anybody actually asks. Both go through the
same `Measure`, so a breakdown and a total can never disagree about what a
finding is — the number in the bar chart and the number in the headline are one
definition applied twice rather than two definitions that happen to agree today.

── Sorting is a column, not a convention ────────────────────────────────

A breakdown by severity sorted alphabetically reads `critical, high, info, low,
medium`, which puts the worst thing in the estate third and looks like a bug in
the data. `dim_severity` carries `rank` precisely so a chart can sort by
seriousness, and `breakdown()` joins it: the dimension row for each group value
rides along, so a caller ordering by `rank` is reading the warehouse's own
declared order rather than inventing one at the last moment.

── Why the rows come back denormalised ──────────────────────────────────

A grid over `fact_finding` showing `subject` as a hash helps nobody. `rows()`
resolves each dimension-pointing column to that dimension's readable field, so
a row carries both — `subject` for identity and `subject_name` for the reader.
Neither is dropped: the id is what a link needs and the name is what a person
needs, and a warehouse that returned only one of them would force every consumer
to fetch the other.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .measures import Measure
from .model import Star

Rows = Sequence[Mapping[str, Any]]

#: What a dimension is *called*, per dimension. The column a reader recognises
#: when a fact points at that table with an id.
LABELS = {
    "dim_node": "name",
    "dim_severity": "severity",
    "dim_evidence": "evidence_kind",
    "dim_time": "day",
}

#: How a fact column is drawn in the browser's grid. Keyed by column name first
#: and column type second, because `confidence` is a real that renders as a
#: percentage while `rank` is a real that renders as itself.
FORMATS = {
    "severity": "severity",
    "confidence": "confidence",
    "base_confidence": "confidence",
    "confidence_impact": "confidence",
}

#: The columns worth linking, and where to. A dashboard row that cannot be
#: opened is a dead end, and the graph screen is where every element question
#: continues.
LINKS = {
    "node_id": "#/node/:node_id",
    "subject": "#/node/:subject",
    "source": "#/node/:source",
    "target": "#/node/:target",
}


def _dimension_for(star: Star, field: str):
    """The dimension behind a fact column, if the star carries one.

    Two ways in, because facts point at dimensions two ways. A denormalised
    column carries the dimension's own key as its value (`severity` on
    `fact_finding`), so the dimension is found by matching key names. A foreign
    key names its table outright (`subject` points at `dim_node`), so the column
    says which one. Grouping by a field with neither is perfectly legitimate —
    `kind` has no table — and yields rows with no attributes attached.
    """
    column = star.fact.column(field)
    if column is not None and column.dimension:
        found = star.dimension(column.dimension)
        if found is not None:
            return found
    for item in star.dimensions:
        if item.keys and item.keys[0] == field:
            return item
    return None


def breakdown(
    star: Star,
    field: str,
    measure: Measure,
    *,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """One row per distinct value of `field`, with the measure computed over it.

    `label` and `value` are the shape the browser's bar list takes, so a
    breakdown renders with no adaptation. The dimension's own columns ride
    alongside under their own names, which is what lets a caller sort by `rank`.
    """
    groups: dict[Any, list[Mapping[str, Any]]] = {}
    for row in star.fact.rows:
        groups.setdefault(row.get(field), []).append(row)

    dimension = _dimension_for(star, field)
    key = dimension.keys[0] if dimension and dimension.keys else field
    attributes = {
        row.get(key): row for row in (dimension.rows if dimension else ())
    }

    out: list[dict[str, Any]] = []
    for value, rows in groups.items():
        label = "unknown" if value in (None, "") else str(value)
        entry: dict[str, Any] = {
            "label": label,
            "value": measure.of(rows),
            "rows": len(rows),
        }
        # The dimension's attributes, minus its own key — which is already
        # `label` and would arrive under a second name for no gain.
        for name, attribute in (attributes.get(value) or {}).items():
            if name not in (field, key):
                entry[name] = attribute
        out.append(entry)

    out.sort(key=lambda entry: (-entry["value"], entry["label"]))
    return out[:limit] if limit > 0 else out


def columns(star: Star, *, limit: int = 0) -> list[dict[str, Any]]:
    """The fact's columns as the browser's grid spec.

    Emitted as plain dictionaries in the shape `ui/contract.Column.to_dict()`
    produces, rather than as `Column` objects imported from there: `contract`
    reaches the verb registry, which reaches this package, and a cycle is too
    high a price for a shared dataclass. The two shapes are pinned by test.
    """
    out: list[dict[str, Any]] = []
    for column in star.fact.columns:
        # A column that points at a dimension is shown by its *name*, with the
        # id kept beside it for the dense register. A grid whose subject column
        # reads `f92e259c841f97b1…` is a table nobody can use — the id is what
        # the link needs and the name is what the reader needs, and dropping
        # either one costs something.
        resolved = column.dimension and LABELS.get(column.dimension)
        if resolved and resolved != column.name:
            out.append({
                "key": f"{column.name}_name",
                "label": column.name.replace("_", " "),
                "align": "",
                "density": "",
                "format": "",
                "link": LINKS.get(column.name, ""),
            })
        out.append({
            "key": column.name,
            "label": (f"{column.name} id" if resolved else
                      column.name.replace("_", " ")),
            "align": "right" if column.type in ("real", "integer") else "",
            # The identity columns are what a reader scans past, not what they
            # scan for, so the dense register shows them and the calm one does
            # not. Same data, one attribute.
            "density": "dense" if (column.key or resolved) else "",
            "format": FORMATS.get(column.name,
                                  "mono" if (column.key or resolved) else ""),
            "link": "" if resolved else LINKS.get(column.name, ""),
        })
    return out[:limit] if limit > 0 else out


def rows(
    star: Star,
    *,
    sort: str = "",
    descending: bool = True,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Fact rows, resolved against their dimensions and ordered.

    `sort` names a fact column; a column the fact does not carry leaves the
    order alone rather than raising, because a template asking for a sort the
    star cannot provide should still render — unsorted and complete beats
    refused, and the panel says how many rows it drew.
    """
    resolved: list[dict[str, Any]] = []
    lookups = {
        item.name: ({row.get(item.keys[0]): row for row in item.rows}
                    if item.keys else {})
        for item in star.dimensions
    }

    for row in star.fact.rows:
        entry = dict(row)
        for column in star.fact.columns:
            if not column.dimension:
                continue
            label = LABELS.get(column.dimension)
            source = lookups.get(column.dimension, {}).get(row.get(column.name))
            if label and source and label != column.name:
                entry[f"{column.name}_name"] = source.get(label)
        resolved.append(entry)

    if sort and star.fact.column(sort) is not None:
        # The order a column sorts in may not be the order its values sort in.
        # `severity` is text, and its seriousness lives on `dim_severity.rank`
        # — so a "worst first" table sorts by the rank the warehouse declares
        # rather than by the spelling of the word.
        dimension = _dimension_for(star, sort)
        ranks: dict[Any, Any] = {}
        if dimension is not None and dimension.column("rank") is not None:
            key = dimension.keys[0] if dimension.keys else sort
            ranks = {item.get(key): item.get("rank") for item in dimension.rows}

        def order(entry: Mapping[str, Any]) -> Any:
            value = entry.get(sort)
            if ranks:
                return ranks.get(value, -1)
            return value if isinstance(value, (int, float)) else str(value)

        # Unmeasured rows are held out and appended, rather than given a
        # sentinel: a sentinel is small in one direction and large in the
        # other, so `descending` would march the least-known rows to the top of
        # the most-urgent list. An absent measurement is not a low one, and it
        # sorts last whichever way the column is read.
        measured = [row for row in resolved if row.get(sort) is not None]
        absent = [row for row in resolved if row.get(sort) is None]
        measured.sort(key=order, reverse=descending)
        resolved = measured + absent

    return resolved[:limit] if limit > 0 else resolved
