"""The ledger: chain integrity, tamper detection, and concurrent writers.

The concurrency test launches real subprocesses rather than threads. Threads
share a GIL and a process, so a thread-safe sequence assignment can still lose
the race between two `slpie` invocations — which is the case that actually
happens when a watcher and an operator run at the same time.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap

import pytest

from slpie.core import AttachElement, CommandBus, DomainEvent, EventKind, emit
from slpie.errors import ChainBroken, LedgerError
from slpie.ledger import (
    GENESIS,
    InMemoryLedger,
    LedgerRecord,
    SqliteLedger,
    canonical,
    open_ledger,
    verify_chain,
)


def an_event(subject="payments", kind=EventKind.HEARTBEAT_RECORDED, **payload):
    return emit(kind, subject, payload)


@pytest.fixture(params=["memory", "sqlite"])
def ledger(request, tmp_path):
    """Both implementations, run against the identical suite.

    The in-memory store is not a relaxed version of the durable one — if a rule
    holds for SQLite it must hold here, or every test that uses the fast store
    is a weaker statement than it appears.
    """
    if request.param == "memory":
        return InMemoryLedger()
    return SqliteLedger(tmp_path / "ledger.db")


# --- append and read -----------------------------------------------------


def test_appending_assigns_consecutive_sequences_from_one(ledger):
    records = ledger.append(an_event("a"), an_event("b"), an_event("c"))

    assert [r.sequence for r in records] == [1, 2, 3]
    assert ledger.version == 3


def test_the_first_record_chains_to_genesis(ledger):
    record = ledger.append(an_event("a"))[0]
    assert record.previous_hash == GENESIS


def test_each_record_chains_to_the_one_before_it(ledger):
    records = ledger.append(an_event("a"), an_event("b"), an_event("c"))

    for previous, current in zip(records, records[1:]):
        assert current.previous_hash == previous.hash


def test_appending_the_same_event_twice_is_idempotent(ledger):
    """At-least-once delivery from a watcher is normal, not a fault."""
    event = an_event("a")
    first = ledger.append(event)[0]
    second = ledger.append(event)[0]

    assert first.sequence == second.sequence
    assert ledger.version == 1


def test_reading_since_a_sequence_returns_only_what_followed(ledger):
    ledger.append(an_event("a"), an_event("b"), an_event("c"))

    assert [r.subject for r in ledger.read(since=1)] == ["b", "c"]
    assert [r.subject for r in ledger.read(limit=2)] == ["a", "b"]


def test_the_head_is_the_whole_history_in_one_value(ledger):
    assert ledger.head() is None
    assert ledger.digest == GENESIS

    ledger.append(an_event("a"))
    assert ledger.head().sequence == 1
    assert ledger.digest == ledger.head().hash


def test_history_for_one_subject_comes_back_in_order(ledger):
    ledger.append(an_event("a"), an_event("b"), an_event("a", fingerprint="x"))

    assert [r.sequence for r in ledger.subject_history("a")] == [1, 3]


def test_an_event_can_be_found_by_its_id(ledger):
    event = an_event("a")
    ledger.append(event)

    assert ledger.by_event_id(event.event_id).sequence == 1
    assert ledger.by_event_id("nonexistent") is None


def test_reading_a_sequence_that_does_not_exist_is_an_error(ledger):
    ledger.append(an_event("a"))

    assert ledger.at(1).subject == "a"
    with pytest.raises(LedgerError):
        ledger.at(99)


def test_filtering_by_kind_returns_only_those_events(ledger):
    ledger.append(
        an_event("a", kind=EventKind.NODE_ASSERTED),
        an_event("b", kind=EventKind.HEARTBEAT_RECORDED),
        an_event("c", kind=EventKind.NODE_ASSERTED),
    )
    selected = ledger.events(kinds=[EventKind.NODE_ASSERTED])

    assert [e.subject for e in selected] == ["a", "c"]


def test_appending_nothing_is_not_an_error(ledger):
    assert ledger.append() == ()
    assert ledger.version == 0


# --- integrity -----------------------------------------------------------


def test_an_untouched_chain_verifies(ledger):
    ledger.append(*(an_event(f"e{n}") for n in range(20)))
    ledger.verify()


def test_an_empty_ledger_verifies(ledger):
    ledger.verify()


def test_two_ledgers_with_the_same_history_have_the_same_digest(tmp_path):
    events = [
        DomainEvent(kind=EventKind.NODE_ASSERTED, subject=f"n{n}", occurred_at=1000 + n)
        for n in range(5)
    ]
    left = InMemoryLedger()
    right = SqliteLedger(tmp_path / "right.db")
    left.append(*events)
    right.append(*events)

    # Identical inputs, identical chain — across implementations, not just runs.
    assert left.digest == right.digest


def test_editing_a_record_in_place_breaks_the_chain(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = SqliteLedger(path)
    ledger.append(an_event("a"), an_event("b"), an_event("c"))

    connection = sqlite3.connect(path)
    connection.execute("UPDATE ledger SET subject = 'forged' WHERE sequence = 2")
    connection.commit()
    connection.close()

    with pytest.raises(ChainBroken) as error:
        SqliteLedger(path).verify()
    assert error.value.sequence == 2


def test_removing_a_record_breaks_the_chain(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = SqliteLedger(path)
    ledger.append(an_event("a"), an_event("b"), an_event("c"))

    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM ledger WHERE sequence = 2")
    connection.commit()
    connection.close()

    with pytest.raises(ChainBroken):
        SqliteLedger(path).verify()


def test_rewriting_a_payload_breaks_the_chain_even_with_the_hash_left_alone(tmp_path):
    path = tmp_path / "ledger.db"
    SqliteLedger(path).append(an_event("a", fingerprint="original"))

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE ledger SET payload = ? WHERE sequence = 1",
        (canonical({"fingerprint": "forged"}),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ChainBroken):
        SqliteLedger(path).verify()


def test_a_reordered_in_memory_chain_is_detected():
    ledger = InMemoryLedger()
    ledger.append(an_event("a"), an_event("b"), an_event("c"))
    records = list(ledger.read())

    with pytest.raises(ChainBroken):
        verify_chain([records[0], records[2], records[1]])


def test_a_record_knows_whether_its_own_content_still_matches_its_hash():
    from dataclasses import replace

    ledger = InMemoryLedger()
    record = ledger.append(an_event("a"))[0]

    assert record.intact
    assert not replace(record, event=an_event("forged")).intact


def test_sealing_is_deterministic():
    event = DomainEvent(kind=EventKind.NODE_ASSERTED, subject="n1", occurred_at=1000)
    left = LedgerRecord.seal(event, sequence=1)
    right = LedgerRecord.seal(event, sequence=1)

    assert left.hash == right.hash
    # A different position is a different record, even for the same event.
    assert LedgerRecord.seal(event, sequence=2).hash != left.hash


# --- durability and concurrency -----------------------------------------


def test_a_ledger_survives_being_reopened(tmp_path):
    path = tmp_path / "ledger.db"
    first = SqliteLedger(path)
    first.append(an_event("a"), an_event("b"))
    digest = first.digest
    first.close()

    reopened = SqliteLedger(path)
    assert reopened.version == 2
    assert reopened.digest == digest
    reopened.verify()


def test_a_failed_batch_leaves_nothing_behind(tmp_path):
    """Half a lockfile in the graph is worse than none of it."""
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.append(an_event("a"))

    class Unserialisable:
        def __repr__(self):
            raise RuntimeError("boom")

    broken = emit(EventKind.NODE_ASSERTED, "n1", {"value": Unserialisable()})
    with pytest.raises(Exception):
        ledger.append(an_event("b"), broken, an_event("c"))

    assert ledger.version == 1
    ledger.verify()


@pytest.mark.slow
def test_two_processes_appending_at_once_do_not_collide(tmp_path):
    path = tmp_path / "ledger.db"
    SqliteLedger(path).append(an_event("seed"))

    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_repository_root())!r})
        from slpie.ledger import SqliteLedger
        from slpie.core import DomainEvent, EventKind

        ledger = SqliteLedger({str(path)!r})
        tag = sys.argv[1]
        for index in range(40):
            ledger.append(DomainEvent(
                kind=EventKind.HEARTBEAT_RECORDED, subject=f"{{tag}}-{{index}}",
            ))
    """))

    processes = [
        subprocess.Popen([sys.executable, str(worker), tag]) for tag in ("a", "b", "c")
    ]
    assert [process.wait() for process in processes] == [0, 0, 0]

    ledger = SqliteLedger(path)
    assert ledger.version == 121
    # No duplicate sequence, no gap, and the chain still hashes through.
    assert [r.sequence for r in ledger.read()] == list(range(1, 122))
    ledger.verify()


def _repository_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


# --- metadata and construction ------------------------------------------


def test_metadata_round_trips(tmp_path):
    ledger = SqliteLedger(tmp_path / "ledger.db")

    assert ledger.meta("environment", "unset") == "unset"
    ledger.set_meta("environment", "acme")
    ledger.set_meta("environment", "acme-production")
    assert ledger.meta("environment") == "acme-production"


def test_correlated_events_come_back_together(tmp_path):
    ledger = SqliteLedger(tmp_path / "ledger.db")
    commands = CommandBus(ledger)
    commands.dispatch(AttachElement(element="payments", correlation_id="scan-1"))
    commands.dispatch(AttachElement(element="orders", correlation_id="scan-1"))
    commands.dispatch(AttachElement(element="shipping", correlation_id="scan-2"))

    assert len(ledger.correlated("scan-1")) == 2


def test_open_ledger_gives_a_durable_store_for_a_path_and_a_memory_one_without(tmp_path):
    assert isinstance(open_ledger(tmp_path / "ledger.db"), SqliteLedger)
    assert isinstance(open_ledger(None), InMemoryLedger)
