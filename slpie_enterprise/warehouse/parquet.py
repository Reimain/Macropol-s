"""`Table` → Parquet, with the schema taken from the declaration.

Arrow is asked for the types rather than told to guess them. That matters more
than it sounds: Arrow infers from the first rows, so a column that is null for
the first thousand facts and a float afterwards infers as null and then fails —
or worse, silently becomes a string column that no downstream aggregation can
sum. The schema is already declared in `slpie/warehouse/model.py`, and using it
is the difference between an export that is right and one that is usually right.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from slpie.warehouse.model import Table

try:  # pragma: no cover - the whole point is that it may be absent
    import pyarrow as pa
    import pyarrow.parquet as pq

    AVAILABLE = True
except ImportError:  # pragma: no cover
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]
    AVAILABLE = False

#: The declared type, as Arrow spells it. `timestamp` is stored as an epoch
#: integer rather than a timestamp type on purpose: the warehouse's own column
#: is an integer, and converting here would put a timezone assumption into an
#: export that has none.
ARROW = {
    "text": "string", "integer": "int64", "real": "float64",
    "boolean": "bool", "timestamp": "int64",
}


def schema_of(table: Table) -> Any:
    """The Arrow schema for a table, from the declaration rather than the rows."""
    _require()
    return pa.schema([
        pa.field(column.name, getattr(pa, ARROW.get(column.type, "string"))(),
                 # Nullable, always. A value that was not recorded stays absent
                 # in this warehouse, and a non-nullable column would force the
                 # exporter to invent one.
                 nullable=True,
                 metadata={b"doc": column.doc.encode("utf-8")} if column.doc else None)
        for column in table.columns
    ])


def to_parquet(table: Table) -> bytes:
    """One table as Parquet bytes."""
    _require()
    import io

    buffer = io.BytesIO()
    pq.write_table(_arrow_table(table), buffer, compression="snappy")
    return buffer.getvalue()


def write_parquet(tables: Iterable[Table], destination: str | Path) -> tuple[str, ...]:
    """A `.parquet` per table under `destination`."""
    _require()
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)

    written = []
    for table in tables:
        path = root / f"{table.name}.parquet"
        pq.write_table(_arrow_table(table), path, compression="snappy")
        written.append(str(path))
    return tuple(written)


def _arrow_table(table: Table) -> Any:
    schema = schema_of(table)
    columns = {
        column.name: [row.get(column.name) for row in table.rows]
        for column in table.columns
    }
    return pa.Table.from_pydict(columns, schema=schema)


def _require() -> None:
    if not AVAILABLE:  # pragma: no cover - depends on the install
        raise ImportError(
            "Parquet export needs pyarrow. Install `slpie[enterprise]`, or use "
            "`warehouse-export --format csv`, which is stdlib and produces the "
            "same rows."
        )
