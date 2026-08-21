"""Three ways out, from one schema. None of them re-states a column.

`csv`, `json` and `sql` — and a fourth, Parquet, in ring 1 behind the same
`Table` it reads. Each is short because the schema already said everything: the
header comes from `Table.header()`, the types come from `Column.sql_type()`, and
an exporter that decided either for itself would be the fourth statement of a
schema that already has one.

── Why SQL goes into the store the platform already has ─────────────────

The graph lives in SQLite or Postgres. Materialising the warehouse *there*
means any BI tool that speaks SQL connects to a database that already exists,
with no new service, no new credential and no ring-1 dependency. A separate
analytics database would be a second thing to deploy, back up and keep in step,
and the §22 rule is that scale comes from implementing a published protocol
rather than adding a component.

── Rebuilt, never migrated ──────────────────────────────────────────────

`load()` drops and recreates. The graph is the source of truth and these tables
are a projection of it — exactly the relationship `core/projections.py` has to
the ledger, where the answer to a schema change is *rebuild from sequence zero*.
An incremental warehouse would need its own change-tracking, which is a second
correctness problem for a second copy of the data.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import Table, ddl, insert


def to_csv(table: Table) -> str:
    """One table as CSV, header first.

    `\\r\\n` is what `csv` writes and what the RFC says, and it is left alone:
    a warehouse extract is read by tools, not by `diff`, and quietly rewriting
    line endings is how a file stops round-tripping.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(table.header())
    for row in table.rows:
        writer.writerow(_flat(value) for value in table.values(row))
    return buffer.getvalue()


def to_json(table: Table) -> str:
    """One table as JSON: the schema, then the rows.

    Not a bare array. A consumer reading an extract cold needs to know what the
    columns *mean*, and shipping the documented schema beside the data is the
    difference between a file and a dataset.
    """
    return json.dumps(
        {
            "table": table.name,
            "doc": table.doc,
            "grain": getattr(table, "grain", ""),
            "columns": [column.to_dict() for column in table.columns],
            "rows": [dict(row) for row in table.rows],
        },
        indent=2, sort_keys=False, default=str,
    )


def to_sql(table: Table, *, dialect: str = "sqlite") -> str:
    """DDL plus literal inserts, for an extract somebody will pipe into a client.

    Literals here, parameters in `load()`. The difference is deliberate: this
    output is a *file* a human runs, so it has to be self-contained; `load()`
    talks to a driver, where a literal would be an injection waiting for a node
    name with a quote in it.
    """
    lines = [f"-- {table.name}: {table.doc}", ddl(table, dialect=dialect), ""]
    if not table.rows:
        lines.append(f"-- no rows")
        return "\n".join(lines)

    columns = ", ".join(table.header())
    for row in table.rows:
        values = ", ".join(_literal(value) for value in table.values(row))
        lines.append(f"INSERT INTO {table.name} ({columns}) VALUES ({values});")
    return "\n".join(lines) + "\n"


def load(connection: Any, tables: Iterable[Table], *, dialect: str = "sqlite") -> dict[str, int]:
    """Materialise tables into a live database. Returns rows written per table.

    Parameterised, always. These rows carry node names, file paths and rule ids
    — user-controlled strings with quotes in them — and the one place a
    warehouse could grow an injection is the loader that writes them.
    """
    written: dict[str, int] = {}
    cursor = connection.cursor()
    for table in tables:
        # Dropped and recreated: this is a projection, and a stale row from a
        # previous build is worse than a missing one because it looks current.
        cursor.execute(f"DROP TABLE IF EXISTS {table.name}")
        cursor.execute(ddl(table, dialect=dialect))
        if table.rows:
            cursor.executemany(
                insert(table, dialect=dialect),
                [table.values(row) for row in table.rows],
            )
        written[table.name] = len(table.rows)
    connection.commit()
    return written


def write(tables: Iterable[Table], destination: str | Path, *,
          fmt: str = "csv", dialect: str = "sqlite") -> tuple[str, ...]:
    """Write an extract per table under `destination`."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)

    renderers = {
        "csv": (to_csv, "csv"),
        "json": (to_json, "json"),
        "sql": (lambda table: to_sql(table, dialect=dialect), "sql"),
    }
    if fmt not in renderers:
        raise KeyError(f"no exporter for {fmt!r}; this build has {', '.join(sorted(renderers))}")

    render, suffix = renderers[fmt]
    written = []
    for table in tables:
        path = root / f"{table.name}.{suffix}"
        path.write_text(render(table), encoding="utf-8")
        written.append(str(path))
    return tuple(written)


def dictionary(tables: Iterable[Table]) -> str:
    """The data dictionary, as Markdown, from the schema itself.

    Generated for the reason `INSTALL.md` is: a hand-written column list is
    wrong within two releases, and a warehouse whose columns are undocumented
    is one where every analyst asks the same question of the same person.
    """
    lines = ["# The warehouse", "",
             "<!-- Generated by `slpie warehouse --dictionary`. Do not edit:",
             "     the columns come from the schema, so this is wrong only if",
             "     the schema is. -->", ""]
    for table in tables:
        lines += [f"## `{table.name}`", "", table.doc, ""]
        if grain := getattr(table, "grain", ""):
            lines += [f"**One row is:** {grain}", ""]
        lines += ["| Column | Type | Meaning |", "|---|---|---|"]
        for column in table.columns:
            note = column.doc or ""
            if column.dimension:
                note = (note + " " if note else "") + f"Joins `{column.dimension}`."
            lines.append(f"| `{column.name}` | {column.type} | {note.strip()} |")
        lines.append("")
    return "\n".join(lines)


def _flat(value: Any) -> Any:
    """A cell CSV can hold. Booleans become 1/0 rather than `True`/`False`.

    Because that is what every SQL target stores and what a spreadsheet sums.
    `None` stays empty, which is the one way CSV can say "not recorded".
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"
