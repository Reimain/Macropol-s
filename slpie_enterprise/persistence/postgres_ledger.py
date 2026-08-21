"""The ledger, in Postgres. The chain is unchanged; only the lock moves.

`SqliteLedger` takes `BEGIN IMMEDIATE` before reading the tail, which is what
makes sequence assignment safe between processes: two writers cannot both see
the same last sequence and both claim the next one. Postgres has no such
statement, and the equivalent is narrower and therefore better — lock the row
the sequence is derived from, rather than the whole database.

**The hash chain does not change at all.** It never depended on *how* the lock
was taken, only that reading the tail and writing its successor were atomic
together. `record_hash` is imported from ring 0 and computed identically, so a
chain written here verifies with ring 0's verifier and vice versa.

**Why a row lock and not a sequence object.** A Postgres sequence is faster and
deliberately allows gaps after a rollback — that is the point of it. A hash
chain with a gap cannot be verified, because sequence *n+1* names *n* as its
predecessor and there is no *n*. So the sequence is derived from the tail, under
a lock, exactly as SQLite derives it.

The first append is the case that needs care: `FOR UPDATE` locks a row that
exists, and an empty ledger has no tail. An advisory lock covers that window.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from slpie.core.events import DomainEvent, EventKind
from slpie.errors import ChainBroken
from slpie.ledger.chain import GENESIS, canonical, record_hash
from slpie.ledger.record import LedgerRecord

from .dialect import LEDGER_LOCK, LOCK_EMPTY_SQL, LOCK_TAIL_SQL
from .engine import Database


class PostgresLedger:
    """An append-only event store in Postgres."""

    def __init__(self, database: Database | str = "") -> None:
        self.db = database if isinstance(database, Database) else Database(str(database))
        self.db.build()

    # -- writing ---------------------------------------------------------

    def append(self, *events: DomainEvent) -> tuple[LedgerRecord, ...]:
        """Seal events onto the end of the chain, atomically.

        The whole batch commits or none of it does. A partially written batch
        would leave the graph projection describing a state that never existed.
        """
        if not events:
            return ()

        written: list[LedgerRecord] = []
        connection = self.db.connection
        cursor = connection.cursor()
        try:
            # Two locks, one window each. The advisory lock is what makes the
            # *first* append safe; `FOR UPDATE` is what makes every later one
            # safe. Taking the advisory lock unconditionally is simpler than
            # deciding whether the ledger is empty before it is safe to look.
            cursor.execute(LOCK_EMPTY_SQL, {"key": LEDGER_LOCK})
            cursor.execute(LOCK_TAIL_SQL)
            tail = cursor.fetchone()
            sequence = tail["sequence"] if tail else 0
            previous = tail["hash"] if tail else GENESIS

            for event in events:
                cursor.execute(
                    "SELECT * FROM ledger WHERE event_id = %s", (event.event_id,)
                )
                existing = cursor.fetchone()
                if existing is not None:
                    # Dedupe on `event_id`, exactly as SQLite does. At-least-once
                    # delivery means the same event genuinely arrives twice, and
                    # appending it twice would break the chain's meaning rather
                    # than merely duplicating a row.
                    written.append(to_record(existing))
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
                cursor.execute(
                    "INSERT INTO ledger (sequence, event_id, kind, subject, payload,"
                    " actor, causation_id, correlation_id, occurred_at, written_at,"
                    " previous_hash, hash)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
        return tuple(written)

    # -- reading ---------------------------------------------------------

    def _all(self, sql: str, parameters: Any = None) -> list[dict[str, Any]]:
        with self.db.connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return list(cursor.fetchall())

    def read(self, *, since: int = 0, limit: int = 0) -> tuple[LedgerRecord, ...]:
        sql = "SELECT * FROM ledger WHERE sequence > %s ORDER BY sequence"
        values: list[Any] = [since]
        if limit:
            sql += " LIMIT %s"
            values.append(limit)
        return tuple(to_record(row) for row in self._all(sql, values))

    def record(self, sequence: int) -> LedgerRecord | None:
        found = self._all("SELECT * FROM ledger WHERE sequence = %s", (sequence,))
        return to_record(found[0]) if found else None

    def by_subject(self, subject: str) -> tuple[LedgerRecord, ...]:
        return tuple(to_record(row) for row in self._all(
            "SELECT * FROM ledger WHERE subject = %s ORDER BY sequence", (subject,),
        ))

    @property
    def version(self) -> int:
        found = self._all("SELECT COALESCE(MAX(sequence), 0) AS v FROM ledger")
        return int(found[0]["v"])

    def __len__(self) -> int:
        return self.version

    def verify(self) -> None:
        """Walk the whole chain, streaming, and raise at the first break.

        **Returns `None` and raises `ChainBroken`, exactly as ring 0 does.** The
        first version of this returned `(ok, reason)`, which reads more nicely
        at a call site and is a different contract — a caller written against
        the protocol would have treated the tuple as truthy and concluded every
        chain was intact, including a broken one. Implementing a published
        protocol means implementing *that* protocol, including the parts whose
        shape you would have chosen differently.

        Streamed rather than loaded because this runs over a production ledger
        of arbitrary size, and an integrity check that needs the history in
        memory is one nobody runs.
        """
        previous = GENESIS
        expected = 1
        with self.db.connection.cursor(name="verify") as cursor:
            cursor.execute("SELECT * FROM ledger ORDER BY sequence")
            for row in cursor:
                if row["sequence"] != expected:
                    raise ChainBroken(row["sequence"], str(expected), str(row["sequence"]))
                if row["previous_hash"] != previous:
                    raise ChainBroken(row["sequence"], previous, row["previous_hash"])
                payload = row["payload"]
                recomputed = record_hash(
                    sequence=row["sequence"],
                    previous_hash=row["previous_hash"],
                    event_id=row["event_id"],
                    kind=row["kind"],
                    subject=row["subject"],
                    payload=payload if isinstance(payload, dict) else json.loads(payload),
                    occurred_at=row["occurred_at"],
                    actor=row["actor"],
                )
                if recomputed != row["hash"]:
                    raise ChainBroken(row["sequence"], row["hash"], recomputed)
                previous = row["hash"]
                expected += 1

    def close(self) -> None:
        self.db.close()


def to_record(row: Any) -> LedgerRecord:
    """One row, as a `LedgerRecord`.

    Kept beside the store rather than in `slpie.graph.rows` because the ledger's
    row shape belongs to the ledger, and putting two unrelated mappings in one
    module because both are called "rows" is how a utility drawer starts.
    """
    payload = row["payload"]
    event = DomainEvent(
        kind=EventKind(row["kind"]),
        subject=row["subject"],
        payload=payload if isinstance(payload, dict) else json.loads(payload),
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
