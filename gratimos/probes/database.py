"""Database probes and streaming capture.

SQLite gets a first-class probe because it arrives as a *file* — an agent
pointed at a folder will meet one. Everything else arrives as a *connection*,
so :func:`stream_query` handles the general DB-API case: it pulls in chunks,
shapes each chunk, and yields wrappers, so a table larger than memory becomes a
sequence of bounded payloads instead of one fatal allocation.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterator, Mapping, Protocol, Sequence

from ..ids import slug
from ..meta import Wrapped, infer_shape
from .base import BaseProbe, Capture, Target

_SQLITE_MAGIC = b"SQLite format 3\x00"

#: Tables SQLite maintains for itself; never interesting as data.
_INTERNAL_PREFIXES = ("sqlite_",)


class Cursor(Protocol):  # pragma: no cover - structural typing only
    description: Any

    def execute(self, sql: str, parameters: Sequence[Any] = ...) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...


class Connection(Protocol):  # pragma: no cover - structural typing only
    def cursor(self) -> Cursor: ...


class SqliteProbe(BaseProbe):
    """One payload per user table, sampled."""

    name = "sqlite"
    suffixes = (".sqlite", ".sqlite3", ".db")
    default_limit = 2000

    def sniff(self, target: Target) -> float:
        if target.head.startswith(_SQLITE_MAGIC):
            return 0.99
        return 0.6 if target.suffix in self.suffixes else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        if target.path is None:
            capture.warnings.append("sqlite probe needs a local path")
            return capture
        capture.bytes_read = target.size

        # Read-only URI so probing can never mutate what it is inspecting.
        uri = f"file:{target.path}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            capture.warnings.append(f"cannot open database: {exc}")
            return capture

        connection.row_factory = sqlite3.Row
        try:
            for table in self._tables(connection, capture):
                rows = self._sample(connection, table, self._limit(limit), capture)
                if not rows:
                    capture.warnings.append(f"table {table!r} is empty")
                    continue
                entity = f"{target.entity}_{slug(table)}"
                capture.payloads.append(self._wrap(rows, target, name=entity, table=table))
        finally:
            connection.close()
        return capture

    def _tables(self, connection: sqlite3.Connection, capture: Capture) -> list[str]:
        try:
            cursor = connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            )
        except sqlite3.Error as exc:
            capture.warnings.append(f"cannot list tables: {exc}")
            return []
        return [
            row[0] for row in cursor.fetchall()
            if not str(row[0]).startswith(_INTERNAL_PREFIXES)
        ]

    def _sample(
        self, connection: sqlite3.Connection, table: str, limit: int, capture: Capture
    ) -> list[dict[str, Any]]:
        # Table names cannot be bound as parameters; quote-escape instead.
        quoted = '"' + str(table).replace('"', '""') + '"'
        try:
            cursor = connection.execute(f"SELECT * FROM {quoted} LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            capture.warnings.append(f"cannot read {table!r}: {exc}")
            return []


def stream_query(
    connection: Connection,
    sql: str,
    *,
    parameters: Sequence[Any] = (),
    chunk_size: int = 1000,
    name: str = "query",
    source: str = "db://connection",
    max_chunks: int = 0,
) -> Iterator[Wrapped]:
    """Stream a query as a sequence of shaped, bounded payloads.

    Works with any DB-API 2.0 connection — sqlite3, psycopg, pyodbc, the UiPath
    data service client — because it only relies on ``cursor.description`` and
    ``fetchmany``.
    """
    cursor = connection.cursor()
    cursor.execute(sql, parameters) if parameters else cursor.execute(sql)
    columns = [str(col[0]) for col in (cursor.description or ())]
    index = 0
    while True:
        batch = cursor.fetchmany(chunk_size)
        if not batch:
            return
        records = [
            row if isinstance(row, Mapping) else dict(zip(columns, row))
            for row in batch
        ]
        yield Wrapped.of(records, name=f"{name}_{index:04d}" if max_chunks != 1 else name,
                         source=source, probe="db-stream", chunk=str(index))
        index += 1
        if max_chunks and index >= max_chunks:
            return


def describe_connection(connection: Connection, tables: Sequence[str], *, source: str = "db://connection") -> dict[str, Any]:
    """Infer a shape per table without materializing the tables."""
    out: dict[str, Any] = {}
    for table in tables:
        quoted = '"' + str(table).replace('"', '""') + '"'
        try:
            chunk = next(stream_query(connection, f"SELECT * FROM {quoted} LIMIT 200", name=table, source=source))
        except (StopIteration, Exception):  # noqa: BLE001 - drivers raise their own errors
            continue
        out[table] = chunk.shape.to_dict()
    return out


def shape_of_query(connection: Connection, sql: str, *, sample: int = 200, name: str = "query") -> Any:
    """Shape a query result from a bounded sample."""
    cursor = connection.cursor()
    cursor.execute(sql)
    columns = [str(col[0]) for col in (cursor.description or ())]
    rows = [dict(zip(columns, row)) for row in cursor.fetchmany(sample)]
    return infer_shape(rows, name=name, source="db://query")
