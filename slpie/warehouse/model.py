"""The schema as data — so the exporters, the DDL and the docs share one truth.

A table declared here becomes a CREATE TABLE, a CSV header, a Parquet schema and
a documented column list without any of those restating it. That is the same
move `compose/registry.py` makes for verbs and `ui/contract.py` makes for
routes, and it is here for the same reason: four hand-written copies of one
schema disagree by the second release.

── Types are a small closed set on purpose ──────────────────────────────

Five: `text`, `integer`, `real`, `boolean`, `timestamp`. Every target speaks all
five, which means an exporter never has to decide what to do with a type its
format cannot hold. A sixth would be one more thing three exporters must agree
about, and the schema does not need one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: What a column may be, and what each is called in the two SQL dialects this
#: platform stores in. Stated once here rather than in the exporter, so adding a
#: dialect is a column in this table rather than a branch in a writer.
TYPES: Mapping[str, tuple[str, str]] = {
    #  name          sqlite      postgres
    "text": ("TEXT", "TEXT"),
    "integer": ("INTEGER", "BIGINT"),
    "real": ("REAL", "DOUBLE PRECISION"),
    # SQLite has no boolean. Storing 0/1 in an INTEGER is what it does anyway,
    # and saying so here stops an exporter inventing a `BOOLEAN` column that
    # silently becomes NUMERIC with different comparison semantics.
    "boolean": ("INTEGER", "BOOLEAN"),
    "timestamp": ("INTEGER", "BIGINT"),
}


@dataclass(frozen=True, slots=True)
class Column:
    """One column, and what it means. The `doc` is not optional by accident.

    A warehouse whose columns are undocumented is one where every analyst asks
    the same question of the same person. The generated data dictionary reads
    this field, so a column added without a sentence is a column that documents
    itself as unexplained.
    """

    name: str
    type: str = "text"
    doc: str = ""
    key: bool = False           # part of the row's identity
    dimension: str = ""         # the table this column points at, if any

    def sql_type(self, dialect: str = "sqlite") -> str:
        pair = TYPES.get(self.type, TYPES["text"])
        return pair[1] if dialect == "postgres" else pair[0]

    def to_dict(self) -> dict[str, Any]:
        out = {"name": self.name, "type": self.type, "doc": self.doc}
        if self.key:
            out["key"] = True
        if self.dimension:
            out["dimension"] = self.dimension
        return out


@dataclass(frozen=True, slots=True)
class Table:
    """A named set of columns, and the rows someone put in it."""

    name: str
    doc: str
    columns: tuple[Column, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.key)

    def column(self, name: str) -> Column | None:
        return next((item for item in self.columns if item.name == name), None)

    def header(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def values(self, row: Mapping[str, Any]) -> tuple[Any, ...]:
        """One row in column order, with absent columns as None.

        Absent rather than defaulted: a fact that did not record a confidence
        and a fact whose confidence is zero are different statements, and
        filling one in as the other is how a warehouse starts lying quietly.
        """
        return tuple(row.get(column.name) for column in self.columns)

    def with_rows(self, rows: Iterable[Mapping[str, Any]]) -> "Table":
        """The same table, filled. Keeps its *type* — which is not automatic.

        An earlier version constructed a plain `Table`, so a `Fact` lost its
        grain the instant it was given rows: `to_json` shipped an empty grain
        and every consumer reading an extract cold lost the one sentence saying
        what a row is. `replace` keeps the subclass, which is the whole point.
        """
        from dataclasses import replace

        return replace(self, rows=tuple(rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "doc": self.doc,
            "columns": [column.to_dict() for column in self.columns],
            "rows": len(self.rows),
        }

    def __len__(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class Dimension(Table):
    """Short and wide: the things facts are grouped by.

    A dimension row is *one of a thing* — one node, one severity, one day — and
    its key is what a fact stores instead of repeating the whole row.
    """


@dataclass(frozen=True, slots=True)
class Fact(Table):
    """Long and thin: one row per event, measurement or relationship.

    `grain` states what one row *is*, in words, and it is the first thing anyone
    reading a fact table needs. A table whose grain nobody wrote down is one
    where somebody eventually sums a column that must not be summed.
    """

    grain: str = ""

    def to_dict(self) -> dict[str, Any]:
        # `Table.to_dict(self)`, not `super().to_dict()`. `slots=True` makes the
        # decorator build a *new* class, and the `__class__` cell a zero-argument
        # `super()` closes over still points at the original — so `super()` here
        # raises `TypeError: obj must be an instance or subtype of type`, and
        # every export that serialised a fact died on it.
        return {**Table.to_dict(self), "grain": self.grain}


@dataclass(frozen=True, slots=True)
class Star:
    """One fact table and the dimensions it points at — the queryable unit."""

    name: str
    doc: str
    fact: Fact
    dimensions: tuple[Dimension, ...] = ()

    @property
    def tables(self) -> tuple[Table, ...]:
        return (self.fact, *self.dimensions)

    def dimension(self, name: str) -> Dimension | None:
        return next((item for item in self.dimensions if item.name == name), None)

    def unresolved(self) -> tuple[str, ...]:
        """Fact columns pointing at a dimension this star does not carry.

        Reported rather than raised: a star exported on its own is a legitimate
        thing to want, and an unresolved key is a *fact about the export* the
        consumer should be told rather than a failure of the build.
        """
        present = {item.name for item in self.dimensions}
        return tuple(sorted(
            column.dimension for column in self.fact.columns
            if column.dimension and column.dimension not in present
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "doc": self.doc,
            "fact": self.fact.to_dict(),
            "dimensions": [item.to_dict() for item in self.dimensions],
            "unresolved": list(self.unresolved()),
        }


def ddl(table: Table, *, dialect: str = "sqlite") -> str:
    """`CREATE TABLE IF NOT EXISTS` for one table, in one dialect.

    `IF NOT EXISTS` because a warehouse is rebuilt rather than migrated: the
    graph is the source of truth and these tables are a projection of it, which
    is the same relationship `core/projections.py` has to the ledger.
    """
    lines = [f"CREATE TABLE IF NOT EXISTS {table.name} ("]
    body = [f"  {column.name} {column.sql_type(dialect)}" for column in table.columns]
    if table.keys:
        body.append(f"  PRIMARY KEY ({', '.join(table.keys)})")
    lines.append(",\n".join(body))
    lines.append(");")
    return "\n".join(lines)


def insert(table: Table, *, dialect: str = "sqlite") -> str:
    """A parameterised INSERT. Never a literal — these rows contain file paths.

    The placeholder differs by dialect because the drivers do, and getting it
    from the same table the DDL comes from is what stops a Postgres export
    silently emitting SQLite's `?`.
    """
    marker = "%s" if dialect == "postgres" else "?"
    columns = ", ".join(table.header())
    markers = ", ".join(marker for _ in table.columns)
    return f"INSERT INTO {table.name} ({columns}) VALUES ({markers})"
