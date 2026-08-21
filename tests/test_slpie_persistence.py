"""Postgres persistence — the dialect, and the answers it must not change.

Two halves, and the second is the one that matters.

The dialect units are cheap and always run: they check the three substitutions
in isolation, including the trap that `MIN` is two different things in SQLite
and only one of them becomes `LEAST`.

The conformance half needs a real database. It runs the **same** graph through
both stores and compares row for row, because two stores that disagree about a
blast radius is a platform that answers differently depending on where it was
deployed — the exact failure §22's conformance rule exists to catch. Skipped
loudly without `SLPIE_DATABASE_URL`, never silently.
"""

from __future__ import annotations

import os

import pytest

from slpie.domain import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceKind,
    Node,
    NodeKind,
    Purl,
    SourceLocation,
)
from slpie.graph import SqliteGraph

LOCK = Evidence(
    kind=EvidenceKind.LOCKFILE_PIN,
    location=SourceLocation("file:///r/package-lock.json", line=10),
    extractor="npm",
    excerpt='"lodash": {"version": "4.17.20"}',
)
DYNAMIC = Evidence(
    kind=EvidenceKind.DYNAMIC_LOAD,
    location=SourceLocation("file:///r/plugins.py", line=3),
    extractor="python",
    excerpt="importlib.import_module(name)",
)


# --- the dialect, always run --------------------------------------------------


def test_instr_becomes_position_with_the_arguments_swapped():
    from slpie_enterprise.persistence.dialect import contains

    found = contains("WHERE instr(r.path, '>' || e.src || '>') = 0")
    assert found == "WHERE position('>' || e.src || '>' in r.path) = 0"


def test_only_the_scalar_min_becomes_least():
    """The trap this module exists to make impossible.

    SQLite's `MIN` is two things wearing one name. Translating the aggregate as
    well produces SQL Postgres accepts and answers differently — a blast radius
    that is silently wrong rather than an error.
    """
    from slpie_enterprise.persistence.dialect import least

    assert least("MIN(r.min_conf, e.confidence)") == "LEAST(r.min_conf, e.confidence)"
    # The aggregate, untouched.
    assert least("SELECT MIN(depth) AS distance") == "SELECT MIN(depth) AS distance"
    assert least("MAX(min_conf)") == "MAX(min_conf)"
    # Both in one statement, which is exactly how the real query is shaped.
    mixed = least("SELECT MIN(depth), MIN(a, b) FROM t")
    assert mixed == "SELECT MIN(depth), LEAST(a, b) FROM t"


def test_the_propagates_flag_becomes_a_boolean():
    from slpie_enterprise.persistence.dialect import booleans

    assert booleans("AND e.propagates = 1") == "AND e.propagates = TRUE"


def test_named_parameters_become_psycopg_placeholders():
    from slpie_enterprise.persistence.dialect import parameters

    assert parameters("WHERE depth < :max_depth") == "WHERE depth < %(max_depth)s"
    # `::jsonb` is a cast, not a parameter, and must survive.
    assert parameters("'{}'::jsonb") == "'{}'::jsonb"


def test_the_four_walks_translate_without_leaving_sqlite_behind():
    """Every walk, end to end, checked for residue.

    A substitution that silently did not fire leaves valid-looking SQL that
    Postgres refuses at execution — but only for the query nobody ran in the
    test that day, which is why this checks all four at import time.
    """
    from slpie_enterprise.persistence import postgres_graph as pg

    for name in ("BLAST_RADIUS", "REACHABLE", "CYCLES", "PATHS"):
        sql = getattr(pg, name)
        assert "instr(" not in sql, f"{name} still calls instr()"
        assert "propagates = 1" not in sql, f"{name} compares a boolean to 1"
        assert ":root" not in sql and ":max_depth" not in sql, f"{name} has SQLite params"
        assert "position(" in sql or name == "CYCLES" or "path" not in sql
    assert "LEAST(" in pg.BLAST_RADIUS
    # And the aggregates survived.
    assert "MIN(depth)" in pg.BLAST_RADIUS
    assert "MAX(min_conf)" in pg.BLAST_RADIUS


def test_the_two_schemas_declare_the_same_columns():
    """A column added to ring 0 and not here is a store that drops a field."""
    import re
    from pathlib import Path

    from slpie.graph.schema import SCHEMA

    here = Path(__file__).resolve().parent.parent
    postgres = (here / "slpie_enterprise" / "persistence" / "schema.sql").read_text()

    def columns(sql: str) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for table, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.DOTALL,
        ):
            names = set()
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--") or line.upper().startswith("PRIMARY"):
                    continue
                names.add(line.split()[0])
            found[table] = names
        return found

    ring0 = columns(SCHEMA)
    ring1 = columns(postgres)
    assert ring0, "the ring-0 schema parsed to nothing — did its shape change?"

    for table, names in ring0.items():
        assert table in ring1, f"the Postgres schema has no {table} table"
        missing = names - ring1[table]
        extra = ring1[table] - names
        assert not missing, f"{table} is missing {sorted(missing)} in Postgres"
        assert not extra, f"{table} has {sorted(extra)} only in Postgres"


# --- conformance: the same answers, from both stores --------------------------


def _database():
    url = os.environ.get("SLPIE_DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "no SLPIE_DATABASE_URL — the Postgres conformance half needs a real "
            "database. It runs in the `enterprise` CI job.",
        )
    pytest.importorskip("psycopg", reason="psycopg is not installed")
    return url


def package(name, version="1.0.0", evidence=LOCK):
    return Node(
        kind=NodeKind.PACKAGE,
        identity=Purl.create("npm", name, version=version),
        evidence=(evidence,),
    )


def depends(src, dst, evidence=LOCK, kind=EdgeKind.DEPENDS_ON):
    return Edge(kind=kind, src=src.id, dst=dst.id, evidence=(evidence,))


def _world():
    """A graph with a hub, a chain, a weak link and a cycle.

    Shaped so every one of the four walks has something to find: a blast radius
    with more than one distance, a path reached only through a 0.4 edge, and a
    genuine loop.
    """
    names = ["app", "api", "core", "utils", "lodash", "plugin", "loop-a", "loop-b"]
    nodes = {name: package(name) for name in names}
    edges = [
        depends(nodes["app"], nodes["api"]),
        depends(nodes["api"], nodes["core"]),
        depends(nodes["core"], nodes["utils"]),
        depends(nodes["utils"], nodes["lodash"]),
        depends(nodes["app"], nodes["lodash"]),
        depends(nodes["api"], nodes["plugin"], evidence=DYNAMIC),
        depends(nodes["plugin"], nodes["lodash"], evidence=DYNAMIC),
        depends(nodes["loop-a"], nodes["loop-b"]),
        depends(nodes["loop-b"], nodes["loop-a"]),
        depends(nodes["core"], nodes["loop-a"]),
    ]
    return nodes, edges


@pytest.fixture
def stores():
    url = _database()
    from slpie_enterprise.persistence.engine import Database
    from slpie_enterprise.persistence.postgres_graph import PostgresGraph

    sqlite = SqliteGraph()
    database = Database(url)
    database.drop()
    postgres = PostgresGraph(database)

    nodes, edges = _world()
    for store in (sqlite, postgres):
        for node in nodes.values():
            store.assert_node(node)
        for edge in edges:
            store.assert_edge(edge)

    yield sqlite, postgres, nodes
    sqlite.close()
    postgres.close()


@pytest.mark.postgres
def test_both_stores_hold_the_same_counts(stores):
    sqlite, postgres, _nodes = stores
    assert sqlite.counts() == postgres.counts()


@pytest.mark.postgres
def test_both_stores_read_back_the_same_node(stores):
    sqlite, postgres, nodes = stores
    for name, node in nodes.items():
        left = sqlite.node(node.id)
        right = postgres.node(node.id)
        assert left is not None and right is not None, name
        assert left.kind == right.kind
        assert str(left.identity) == str(right.identity)
        assert left.confidence == pytest.approx(right.confidence), name
        assert {e.id for e in left.evidence} == {e.id for e in right.evidence}


@pytest.mark.postgres
def test_the_blast_radius_agrees_row_for_row(stores):
    """The gate. Two stores that disagree here is a platform that answers
    differently depending on where it is deployed."""
    sqlite, postgres, nodes = stores
    for name in ("lodash", "utils", "core", "loop-a"):
        root = nodes[name].id
        left = sqlite.blast_radius(root, max_depth=8)
        right = postgres.blast_radius(root, max_depth=8)
        assert len(left) == len(right), f"{name}: {len(left)} vs {len(right)}"
        for one, two in zip(left, right):
            assert one[0] == two[0], name
            assert one[1] == two[1], f"{name}: distance to {one[0]}"
            assert one[2] == pytest.approx(two[2]), f"{name}: confidence to {one[0]}"


@pytest.mark.postgres
def test_the_confidence_floor_propagates_identically(stores):
    """A path reached only through a 0.4 dynamic load is a 0.4 path in both.

    This is the assertion the `MIN`/`LEAST` substitution actually exercises: if
    the aggregate had been translated too, the numbers here would differ and
    nothing else would.
    """
    sqlite, postgres, nodes = stores
    root = nodes["lodash"].id
    plugin = nodes["plugin"].id

    left = {row[0]: row[2] for row in sqlite.blast_radius(root, max_depth=8)}
    right = {row[0]: row[2] for row in postgres.blast_radius(root, max_depth=8)}
    assert left.keys() == right.keys()
    assert plugin in left, "the weak path was not reached at all"
    assert left[plugin] == pytest.approx(right[plugin])
    assert left[plugin] < 0.5, "the dynamic-load path should be reported as weak"


@pytest.mark.postgres
def test_reachability_and_cycles_agree(stores):
    sqlite, postgres, nodes = stores
    root = nodes["app"].id
    assert sqlite.reachable(root, max_depth=8) == postgres.reachable(root, max_depth=8)

    left = {frozenset(cycle) for cycle in sqlite.cycles(max_depth=6)}
    right = {frozenset(cycle) for cycle in postgres.cycles(max_depth=6)}
    assert left == right
    assert left, "the fixture contains a loop and neither store found it"


@pytest.mark.postgres
def test_a_confidence_floor_prunes_identically(stores):
    """The `min_confidence` filter is where a weak edge is meant to disappear."""
    sqlite, postgres, nodes = stores
    root = nodes["lodash"].id
    left = sqlite.blast_radius(root, max_depth=8, min_confidence=0.8)
    right = postgres.blast_radius(root, max_depth=8, min_confidence=0.8)
    assert left == right
    reached = {row[0] for row in left}
    assert nodes["plugin"].id not in reached, "the 0.4 edge survived a 0.8 floor"


@pytest.mark.postgres
def test_retiring_a_node_removes_it_from_both(stores):
    sqlite, postgres, nodes = stores
    target = nodes["plugin"].id
    assert sqlite.retire_node(target, valid_to=100) is True
    assert postgres.retire_node(target, valid_to=100) is True

    assert sqlite.counts()["nodes"] == postgres.counts()["nodes"]
    assert sqlite.counts()["retired_nodes"] == postgres.counts()["retired_nodes"]
    # And retiring it twice is not a second retirement in either.
    assert sqlite.retire_node(target, valid_to=101) is False
    assert postgres.retire_node(target, valid_to=101) is False


@pytest.mark.postgres
def test_search_finds_the_same_nodes(stores):
    sqlite, postgres, _nodes = stores
    left = {node.id for node in sqlite.search("lo")}
    right = {node.id for node in postgres.search("lo")}
    assert left == right
    assert left, "neither store matched anything, so this proves nothing"


# --- the ledger: the chain is the chain -------------------------------------


@pytest.fixture
def ledgers():
    url = _database()
    from slpie.ledger.sqlite_ledger import open_ledger
    from slpie_enterprise.persistence.engine import Database
    from slpie_enterprise.persistence.postgres_ledger import PostgresLedger

    database = Database(url)
    database.drop()
    yield open_ledger(None), PostgresLedger(database)
    database.close()


def _events(count=6):
    from slpie.core.events import DomainEvent, EventKind

    return [
        DomainEvent(
            kind=EventKind.NODE_ASSERTED,
            subject=f"urn:slpie:thing:{index}",
            payload={"index": index, "note": "written twice on purpose"},
            actor="conformance",
            occurred_at=1_700_000_000 + index,
        )
        for index in range(count)
    ]


@pytest.mark.postgres
def test_the_chain_is_identical_in_both_stores(ledgers):
    """The hash chain never depended on *how* the lock was taken.

    So a chain written to Postgres must be byte-identical to one written to
    SQLite from the same events — same sequences, same predecessors, same
    digests. If they differ, the digest is a function of the storage engine,
    which would make "the ledger is the source of truth" untrue.
    """
    sqlite, postgres = ledgers
    events = _events()

    left = sqlite.append(*events)
    right = postgres.append(*events)

    assert len(left) == len(right) == len(events)
    for one, two in zip(left, right):
        assert one.sequence == two.sequence
        assert one.previous_hash == two.previous_hash
        assert one.hash == two.hash, f"digest differs at sequence {one.sequence}"


@pytest.mark.postgres
def test_both_verifiers_agree_the_chain_is_intact(ledgers):
    """Same contract, not merely the same conclusion.

    Ring 0's `verify()` returns `None` and raises `ChainBroken`. A ring-1 store
    returning `(ok, reason)` would read more nicely and be a *different*
    protocol — a caller written against the published one would treat the tuple
    as truthy and call every chain intact, including a broken one.
    """
    sqlite, postgres = ledgers
    sqlite.append(*_events())
    postgres.append(*_events())

    assert sqlite.verify() is None
    assert postgres.verify() is None
    assert sqlite.version == postgres.version


@pytest.mark.postgres
def test_a_tampered_record_is_caught(ledgers):
    """The verifier earns its keep only if it catches an edit it did not make."""
    _sqlite, postgres = ledgers
    postgres.append(*_events())

    with postgres.db.transaction() as cursor:
        cursor.execute("UPDATE ledger SET subject = %s WHERE sequence = 3", ("forged",))

    from slpie.errors import ChainBroken

    with pytest.raises(ChainBroken) as raised:
        postgres.verify()
    assert "3" in str(raised.value)


@pytest.mark.postgres
def test_the_same_event_appended_twice_is_appended_once(ledgers):
    """At-least-once delivery means the same event genuinely arrives twice."""
    _sqlite, postgres = ledgers
    events = _events(3)

    first = postgres.append(*events)
    again = postgres.append(*events)

    assert postgres.version == 3
    assert [r.sequence for r in first] == [r.sequence for r in again]
    assert [r.hash for r in first] == [r.hash for r in again]


@pytest.mark.postgres
def test_two_writers_never_claim_the_same_sequence(ledgers):
    """The reason the lock exists, exercised across real connections.

    Both processes read the tail and derive the next sequence from it. Without
    the lock they see the same tail and both claim it; the insert then fails on
    the primary key, or worse, one of them wins and the other's event is lost
    with no error anywhere.
    """
    import threading

    _sqlite, postgres = ledgers
    url = os.environ["SLPIE_DATABASE_URL"]
    from slpie_enterprise.persistence.engine import Database
    from slpie_enterprise.persistence.postgres_ledger import PostgresLedger

    barrier = threading.Barrier(4)
    failures: list[BaseException] = []

    def writer(offset: int) -> None:
        ledger = PostgresLedger(Database(url))
        try:
            barrier.wait(timeout=20)
            ledger.append(*_events(5)[offset:offset + 1])
            ledger.append(*[
                event for event in _events(40)[10 + offset * 5:15 + offset * 5]
            ])
        except BaseException as error:      # noqa: BLE001 - reported, not swallowed
            failures.append(error)
        finally:
            ledger.close()

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert not failures, f"a concurrent writer failed: {failures}"

    postgres.verify()   # raises ChainBroken if the concurrent writes broke it

    records = postgres.read()
    sequences = [record.sequence for record in records]
    assert sequences == list(range(1, len(sequences) + 1)), (
        f"the sequence has a gap or a duplicate: {sequences}"
    )
    assert len(records) == 24, f"events were lost or duplicated: {len(records)}"


# --- across processes ---------------------------------------------------------


@pytest.mark.postgres
def test_an_append_in_one_connection_reaches_a_listener_in_another():
    """The piece phase 16 needs: a worker's observations reaching a web server.

    The payload is a *sequence*, never an event. `NOTIFY` truncates at 8000
    bytes and a subscriber that parsed a truncated event would act on half a
    fact — so the wire carries a number and the subscriber reads the ledger,
    which is §23's single-writer/replicated-reader shape between two processes
    rather than two regions.
    """
    url = _database()
    from slpie_enterprise.persistence import notify
    from slpie_enterprise.persistence.engine import Database, connect
    from slpie_enterprise.persistence.postgres_ledger import PostgresLedger

    database = Database(url)
    database.drop()
    ledger = PostgresLedger(database)

    listener = connect(url, autocommit=True)
    notify.listen(listener)

    with ledger.db.transaction() as cursor:
        notify.announce(cursor, 7)

    seen: list[int] = []
    notify.bridge(listener, seen.append, timeout=5.0)
    listener.close()
    ledger.close()

    assert seen == [7]


@pytest.mark.postgres
def test_a_listener_must_be_in_autocommit():
    """Inside a transaction the subscription is invisible until commit, and
    notifications queue behind it — a feed that silently delivers nothing."""
    url = _database()
    from slpie_enterprise.persistence import notify
    from slpie_enterprise.persistence.engine import connect

    connection = connect(url, autocommit=False)
    with pytest.raises(RuntimeError, match="autocommit"):
        notify.listen(connection)
    connection.close()


@pytest.mark.postgres
def test_a_channel_name_cannot_carry_sql():
    """`NOTIFY` will not take a parameter for the channel, so this is the one
    place a name reaches SQL as text."""
    from slpie_enterprise.persistence import notify

    with pytest.raises(ValueError, match="plain identifier"):
        notify._identifier("ledger; DROP TABLE ledger")


# --- migrations ---------------------------------------------------------------


@pytest.mark.postgres
def test_a_migrated_database_and_a_built_one_are_the_same_shape():
    """Alembic and `Database.build()` must not disagree.

    They read the same `schema.sql`, and this is what keeps that true: a
    migration hand-written to add a column, with the file left alone, would
    give a migrated database and a fresh one two different shapes — and only
    one of them would be the one the tests ran against.
    """
    import subprocess
    from pathlib import Path

    url = _database()
    from slpie_enterprise.persistence.engine import Database

    here = Path(__file__).resolve().parent.parent / "slpie_enterprise" / "persistence"

    def shape(connection) -> set[tuple[str, str, str]]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns WHERE table_schema = 'public' "
                "ORDER BY table_name, column_name"
            )
            return {
                (row["table_name"], row["column_name"], row["data_type"])
                for row in cursor.fetchall()
                # Alembic's own bookkeeping is not part of either schema, and
                # it survives `Database.drop()` because that drops the tables
                # the platform declares and nothing else — which is correct.
                if row["table_name"] != "alembic_version"
            }

    built = Database(url)
    built.drop()
    built.build()
    from_build = shape(built.connection)
    built.drop()
    built.close()

    environment = {**os.environ, "SLPIE_DATABASE_URL": url}
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        finished = subprocess.run(
            ["python", "-m", "alembic", *command],
            cwd=here, env=environment, capture_output=True, text=True, timeout=180,
        )
        assert finished.returncode == 0, f"alembic {command}: {finished.stderr}"

    migrated = Database(url)
    from_alembic = shape(migrated.connection)
    migrated.close()

    assert from_build == from_alembic, (
        "a migrated database and a freshly built one have different shapes:\n"
        f"  only when built:   {sorted(from_build - from_alembic)}\n"
        f"  only when migrated:{sorted(from_alembic - from_build)}"
    )
    assert len(from_build) > 50, "the schema introspected to almost nothing"


# --- packaging and boundaries, checked without a database ---------------------


def test_the_store_ships_the_sql_it_reads_at_runtime():
    """The DDL is read from disk, so it has to be in the wheel.

    Editable installs never catch a missing package-data glob, and the failure
    mode is a store that imports fine and cannot create its own schema — the
    same shape as the interface's nested-module packaging bug in §30.
    """
    import glob
    import tomllib
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    manifest = tomllib.loads((here / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = manifest["tool"]["setuptools"]["package-data"]["slpie_enterprise.persistence"]

    root = here / "slpie_enterprise" / "persistence"
    matched: set[Path] = set()
    for pattern in patterns:
        for hit in glob.glob(pattern, root_dir=str(root), recursive=True):
            matched.add((root / hit).resolve())

    needed = [
        root / "schema.sql",
        root / "alembic.ini",
        root / "migrations" / "env.py",
        root / "migrations" / "versions" / "0001_baseline.py",
    ]
    missing = [str(path.relative_to(here)) for path in needed if path.resolve() not in matched]
    assert not missing, f"these are read at runtime and match no glob: {missing}"


def test_ring_zero_does_not_know_this_package_exists():
    """The direction that would be a redesign rather than an implementation.

    §22's rule is that ring 1 imports ring 0's published API and ring 0 never
    imports ring 1. `test_enterprise_boundaries.py` walks both directions
    already; this names the specific thing phase 15 could have broken.
    """
    from pathlib import Path

    from _walk import imported_roots

    here = Path(__file__).resolve().parent.parent
    forbidden = {"psycopg", "slpie_enterprise", "alembic", "sqlalchemy"}

    # **Imports, not prose.** A first version grepped the text and flagged three
    # files, all of which merely *name* the ring-1 adapter their protocol is for
    # — `core/tasks.py` says "Celery", `graph/rows.py` says psycopg returns
    # jsonb as an object. That is the seam being legible, and a check that
    # punished writing it down would teach people to stop. Same rule the
    # browser tier's comment stripper follows: a warning about a hazard must
    # not be the hazard.
    offenders = []
    checked = 0
    for path in sorted((here / "slpie").rglob("*.py")):
        checked += 1
        reached = {root.lower() for root in imported_roots(path)} & forbidden
        if reached:
            offenders.append(f"{path.relative_to(here)} imports {sorted(reached)}")
    assert checked > 100, "the ring-0 walk found almost nothing — did it move?"
    assert not offenders, f"ring 0 imports the enterprise tier: {offenders}"


def test_the_walks_are_ring_zero_s_own_sql_rather_than_a_copy():
    """One definition of what a blast radius is.

    Copying the CTEs into ring 1 would have been faster and would have produced
    two definitions of reachability that drift. The store imports them.
    """
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    body = (here / "slpie_enterprise" / "persistence" / "postgres_graph.py").read_text()
    assert "from slpie.graph.sqlite_graph import" in body
    assert "WITH RECURSIVE" not in body, (
        "the Postgres store carries its own copy of a walk — a change to the "
        "traversal would now have to be made twice"
    )
