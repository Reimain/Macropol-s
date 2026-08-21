"""Connections, and the one thing a pool must not get wrong.

Thin on purpose. This is not an abstraction over Postgres — it is the small
amount of lifecycle every caller would otherwise repeat: parse a URL, hand out a
connection, build the schema once.

**`dict_row` is not a preference.** `slpie/graph/rows.py` maps a row to a domain
object by column name, and it is the same mapper both stores use. A tuple cursor
would work everywhere except there, and the failure would be a `TypeError` deep
inside a read rather than anything a connection test would catch.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

from contextlib import contextmanager

SCHEMA = Path(__file__).resolve().parent / "schema.sql"

#: Where a caller says which database to use. Named once so the CI job, the
#: test fixture and an operator all spell it the same way.
URL = "SLPIE_DATABASE_URL"


class PersistenceUnavailable(RuntimeError):
    """Postgres was asked for and is not there.

    A distinct type rather than a bare `RuntimeError`, so a caller can route on
    it — the taxonomy rule both `errors.py` docstrings open with.
    """


def configured() -> str:
    return os.environ.get(URL, "").strip()


def connect(url: str = "", *, autocommit: bool = False) -> Any:
    """One connection, rows as dicts.

    Raises `PersistenceUnavailable` rather than returning `None`, because a
    caller that forgets to check a `None` gets an `AttributeError` from
    somewhere unrelated, and this is the one place that can still say what
    actually went wrong.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:  # pragma: no cover - exercised by absence
        raise PersistenceUnavailable(
            "psycopg is not installed; `pip install -e '.[enterprise]'`"
        ) from error

    target = url or configured()
    if not target:
        raise PersistenceUnavailable(
            f"no database configured; set {URL} to a Postgres connection string"
        )
    return psycopg.connect(target, row_factory=dict_row, autocommit=autocommit)


class Database:
    """A connection and the schema it holds, with the lifecycle in one place."""

    def __init__(self, url: str = "", *, autocommit: bool = False) -> None:
        self.url = url or configured()
        self.connection = connect(self.url, autocommit=autocommit)

    def build(self) -> None:
        """Create the schema. Idempotent — every statement is `IF NOT EXISTS`."""
        with self.connection.cursor() as cursor:
            cursor.execute(SCHEMA.read_text(encoding="utf-8"))
        self.connection.commit()

    def drop(self) -> None:
        """Remove it. Used by the test fixture between cases, never in anger."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DROP TABLE IF EXISTS ledger_meta, ledger, snapshot, enrichment, "
                "edge_evidence, node_evidence, evidence, edge, node CASCADE"
            )
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """A cursor inside a transaction, committed or rolled back as a unit."""
        with self.connection.cursor() as cursor:
            try:
                yield cursor
            except Exception:
                self.connection.rollback()
                raise
            self.connection.commit()

    def close(self) -> None:
        if self.connection and not self.connection.closed:
            self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.close()
