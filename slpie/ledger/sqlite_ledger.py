"""The durable ledger: SQLite in WAL mode, safe for concurrent writers.

Sequence assignment is the one place in the platform where two processes can
genuinely race. A watcher noticing a file change and an operator running a scan
must not both claim sequence 41 — and "read the max, then insert" loses that
race however carefully it is written.

So the sequence comes from the table itself, inside an `IMMEDIATE` transaction:
the writer takes the write lock before reading the tail, so the value it reads
is still true when it writes. A second process blocks until the first commits,
then reads the new tail. The chain stays consistent because it cannot be
computed from stale data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..core.events import DomainEvent, EventKind
from ..errors import ChainBroken, LedgerError
from .chain import GENESIS, canonical, record_hash
from .record import LedgerRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    sequence      INTEGER PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL,
    payload       TEXT NOT NULL,
    actor         TEXT NOT NULL,
    causation_id  TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    occurred_at   INTEGER NOT NULL,
    written_at    INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    hash          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_subject ON ledger(subject);
CREATE INDEX IF NOT EXISTS ledger_kind    ON ledger(kind);
CREATE INDEX IF NOT EXISTS ledger_causation ON ledger(causation_id);
CREATE INDEX IF NOT EXISTS ledger_correlation ON ledger(correlation_id);

CREATE TABLE IF NOT EXISTS ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteLedger:
    """An append-only event store on disk.

    Connections are per-thread: SQLite objects are not shareable across threads,
    and the UI server, the watcher and the CLI all touch the same ledger.
    """

    def __init__(self, path: str | Path, *, timeout: float = 30.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._local = threading.local()
        self._lock = threading.RLock()
        self._initialise()

    # -- connection ------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = sqlite3.connect(
                self.path,
                timeout=self._timeout,
                isolation_level=None,          # explicit transactions only
                check_same_thread=False,
            )
            existing.row_factory = sqlite3.Row
            existing.execute("PRAGMA journal_mode=WAL")
            existing.execute("PRAGMA synchronous=NORMAL")
            existing.execute("PRAGMA foreign_keys=ON")
            existing.execute("PRAGMA busy_timeout=%d" % int(self._timeout * 1000))
            self._local.connection = existing
        return existing

    def _initialise(self) -> None:
        with self._lock:
            self.connection.executescript(SCHEMA)

    def close(self) -> None:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None

    # -- writing ---------------------------------------------------------

    def append(self, *events: DomainEvent) -> tuple[LedgerRecord, ...]:
        """Seal events onto the end of the chain, atomically.

        The whole batch commits or none of it does. A partially written batch
        would leave the graph projection describing a state that never existed.
        """
        if not events:
            return ()

        connection = self.connection
        written: list[LedgerRecord] = []
        # BEGIN IMMEDIATE takes the write lock *before* we read the tail, which
        # is what makes sequence assignment safe between processes.
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT sequence, hash FROM ledger ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = row["sequence"] if row else 0
            previous = row["hash"] if row else GENESIS

            for event in events:
                existing = connection.execute(
                    "SELECT * FROM ledger WHERE event_id = ?", (event.event_id,)
                ).fetchone()
                if existing is not None:
                    written.append(_row_to_record(existing))
                    continue

                sequence += 1
                written_at = time.time_ns()
                placed = event.sequenced(sequence)
                digest = record_hash(
                    sequence=sequence,
                    previous_hash=previous,
                    event_id=placed.event_id,
                    kind=placed.kind.value,
                    subject=placed.subject,
                    payload=placed.payload,
                    occurred_at=placed.occurred_at,
                    actor=placed.actor,
                )
                connection.execute(
                    "INSERT INTO ledger (sequence, event_id, kind, subject, payload, actor,"
                    " causation_id, correlation_id, occurred_at, written_at, previous_hash, hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sequence, placed.event_id, placed.kind.value, placed.subject,
                        canonical(dict(placed.payload)), placed.actor,
                        placed.causation_id, placed.correlation_id,
                        placed.occurred_at, written_at, previous, digest,
                    ),
                )
                written.append(LedgerRecord(
                    sequence=sequence, event=placed, previous_hash=previous,
                    hash=digest, written_at=written_at,
                ))
                previous = digest
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return tuple(written)

    # -- reading ---------------------------------------------------------

    def read(self, *, since: int = 0, limit: int = 0) -> tuple[LedgerRecord, ...]:
        query = "SELECT * FROM ledger WHERE sequence > ? ORDER BY sequence"
        parameters: list[Any] = [since]
        if limit:
            query += " LIMIT ?"
            parameters.append(limit)
        return tuple(
            _row_to_record(row) for row in self.connection.execute(query, parameters)
        )

    def events(self, *, since: int = 0, kinds: Iterable[EventKind] = ()) -> tuple[DomainEvent, ...]:
        wanted = [k.value for k in kinds]
        if not wanted:
            return tuple(record.event for record in self.read(since=since))
        placeholders = ",".join("?" * len(wanted))
        rows = self.connection.execute(
            f"SELECT * FROM ledger WHERE sequence > ? AND kind IN ({placeholders})"
            " ORDER BY sequence",
            [since, *wanted],
        )
        return tuple(_row_to_record(row).event for row in rows)

    def head(self) -> LedgerRecord | None:
        row = self.connection.execute(
            "SELECT * FROM ledger ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return _row_to_record(row) if row else None

    def at(self, sequence: int) -> LedgerRecord:
        row = self.connection.execute(
            "SELECT * FROM ledger WHERE sequence = ?", (sequence,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"no record at sequence {sequence}")
        return _row_to_record(row)

    def by_event_id(self, event_id: str) -> LedgerRecord | None:
        row = self.connection.execute(
            "SELECT * FROM ledger WHERE event_id = ?", (event_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def subject_history(self, subject: str) -> tuple[LedgerRecord, ...]:
        rows = self.connection.execute(
            "SELECT * FROM ledger WHERE subject = ? ORDER BY sequence", (subject,)
        )
        return tuple(_row_to_record(row) for row in rows)

    def correlated(self, correlation_id: str) -> tuple[LedgerRecord, ...]:
        """Every event from one operation — what the UI groups a scan under."""
        rows = self.connection.execute(
            "SELECT * FROM ledger WHERE correlation_id = ? OR event_id = ? ORDER BY sequence",
            (correlation_id, correlation_id),
        )
        return tuple(_row_to_record(row) for row in rows)

    # -- integrity -------------------------------------------------------

    def verify(self) -> None:
        """Walk the whole chain, streaming, and raise at the first break.

        Streamed rather than loaded because this runs over a production ledger of
        arbitrary size, and an integrity check that needs the history in memory
        is one nobody runs.
        """
        previous = GENESIS
        expected = 1
        for row in self.connection.execute("SELECT * FROM ledger ORDER BY sequence"):
            if row["sequence"] != expected:
                raise ChainBroken(row["sequence"], str(expected), str(row["sequence"]))
            if row["previous_hash"] != previous:
                raise ChainBroken(row["sequence"], previous, row["previous_hash"])
            recomputed = record_hash(
                sequence=row["sequence"],
                previous_hash=row["previous_hash"],
                event_id=row["event_id"],
                kind=row["kind"],
                subject=row["subject"],
                payload=json.loads(row["payload"]),
                occurred_at=row["occurred_at"],
                actor=row["actor"],
            )
            if recomputed != row["hash"]:
                raise ChainBroken(row["sequence"], recomputed, row["hash"])
            previous = row["hash"]
            expected += 1

    @property
    def digest(self) -> str:
        head = self.head()
        return head.hash if head else GENESIS

    @property
    def version(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()
        return int(row["n"])

    # -- metadata --------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO ledger_meta (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def meta(self, key: str, default: str = "") -> str:
        row = self.connection.execute(
            "SELECT value FROM ledger_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def __len__(self) -> int:
        return self.version

    def __iter__(self) -> Iterator[LedgerRecord]:
        return iter(self.read())

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<SqliteLedger {self.path} version={self.version}>"


def _row_to_record(row: sqlite3.Row) -> LedgerRecord:
    event = DomainEvent(
        kind=EventKind(row["kind"]),
        subject=row["subject"],
        payload=json.loads(row["payload"]),
        actor=row["actor"],
        causation_id=row["causation_id"],
        correlation_id=row["correlation_id"],
        occurred_at=row["occurred_at"],
        sequence=row["sequence"],
    )
    return LedgerRecord(
        sequence=row["sequence"], event=event,
        previous_hash=row["previous_hash"], hash=row["hash"],
        written_at=row["written_at"],
    )


def open_ledger(path: str | Path | None) -> SqliteLedger | Any:
    """A durable ledger for a path, or an in-memory one when there is none.

    The simulator and the tests take the in-memory branch; nothing above this
    line knows which it got.
    """
    if path is None:
        from .store import InMemoryLedger

        return InMemoryLedger()
    return SqliteLedger(path)
