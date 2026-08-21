# Phase 15 — Postgres persistence (ring 1)

**Built.** 17 conformance tests pass against a real Postgres 16; the default
suite still runs offline with zero third-party packages. What follows was the
plan; the last section records where it was wrong.

## Why it is small

The seam already exists. `slpie/graph/store.py` declares `GraphView` and
`GraphStore` as `Protocol`s, and `slpie/ledger/` has the same shape. Phase 15
is an *implementation* of published contracts, not a redesign — which is §22's
whole argument, and it is why the surface below fits on a page.

**Nothing blocks it.** Ring 1 today is `slpie_enterprise/{spawn,storage}` — 8
modules. `pip install -e '.[enterprise]'` resolves fastapi, celery, redis,
SQLAlchemy, alembic and psycopg cleanly now that the extra is declared. 15 is
unstarted, not stuck.

## The exact port surface

| What | Where | Count |
|---|---|---|
| recursive CTEs | `slpie/graph/sqlite_graph.py:45,67,90,114` | 4 |
| `instr(path, …)` | same file, `:56,78,102,124` | 4 |
| `MIN(a, b)` two-arg | same file, `:49,71,118` | 3 |
| `BEGIN IMMEDIATE` | `slpie/ledger/sqlite_ledger.py:116` | 1 |
| methods to implement | `sqlite_graph.py` | 50 |
| methods to implement | `sqlite_ledger.py` | 22 |

Postgres has recursive CTEs, so the four walks port structurally. Three
dialect differences and one concurrency primitive is the entire delta:

```
instr(r.path, '>' || e.src || '>') = 0   →   position('>' || e.src || '>' in r.path) = 0
MIN(r.min_conf, e.confidence)            →   LEAST(r.min_conf, e.confidence)
CREATE INDEX … WHERE valid_to IS NULL    →   same syntax; Postgres has partial indexes
BEGIN IMMEDIATE                          →   a sequence plus SELECT … FOR UPDATE
```

`MIN(depth)` and `MAX(min_conf)` in the projection are **aggregates** and port
unchanged; only the two-argument scalar form needs `LEAST`. Getting those two
confused is the obvious way to break the traversal silently, so the dialect
layer names them separately.

## Decisions taken up front

| Question | Decision |
|---|---|
| Dialect strategy | A **small `dialect` module**, not an ORM. Three substitutions do not justify SQLAlchemy Core in the query path; SQLAlchemy stays for Alembic's benefit only |
| Test strategy | **Parametrise the existing fixtures.** `tests/test_slpie_graph.py:62,77` and the ledger's equivalents gain a store parameter. No new assertions — if Postgres and SQLite disagree, an *existing* test fails |
| Where Postgres runs in CI | A `services: postgres:16` container in a **separate job**. The kernel job keeps installing zero third-party packages, which is invariant 4 |
| Where it runs here | `pytest -m postgres` skips loudly with no `DATABASE_URL`. Never a silent skip |
| Sequence assignment | Postgres sequence + `SELECT … FOR UPDATE` on the tail row. The hash chain is unchanged |
| Cross-process events | `LISTEN`/`NOTIFY` carrying `EventBus` frames — this is what lets a Celery worker's observations reach a web server's SSE stream in phase 16 |
| Migrations | Alembic, already a Gratimos dependency under the `sql` extra |

## Files

```
slpie_enterprise/persistence/
  __init__.py
  dialect.py        the three substitutions, named and tested individually
  engine.py         connection/pool lifecycle, DATABASE_URL parsing
  schema.sql        the DDL, mirroring slpie/graph/schema.py
  postgres_graph.py GraphStore + Traversal, the four CTEs ported
  postgres_ledger.py LedgerStore, sequence + FOR UPDATE, chain unchanged
  notify.py         LISTEN/NOTIFY as an EventBus transport
  migrations/       alembic
tests/
  conftest.py       a `store` fixture parametrised over sqlite and postgres
  test_slpie_persistence.py   dialect units + the two-writer concurrency case
.github/workflows/enterprise.yml
```

## Sequence, with the gate that proves each step

| Step | Delivers | Gate |
|---|---|---|
| **1** | `dialect.py` and its unit tests | each substitution checked in isolation; the aggregate/scalar `MIN` distinction has its own case |
| **2** | `engine.py`, `schema.sql`, Alembic baseline | `alembic upgrade head` builds the schema; `downgrade base` removes it |
| **3** | `postgres_graph.py` — writes and reads, no traversal | the existing node/edge/evidence tests pass against it, parametrised |
| **4** | the four CTEs | **blast radius, cycles and reachability return identical results to SQLite over the same graph** — same fixture, both stores, compared row for row |
| **5** | `postgres_ledger.py` | the existing chain-integrity and tamper tests pass; the **two-process writer test** passes against Postgres |
| **6** | `notify.py` | an event appended in one process reaches a subscriber in another |
| **7** | CI job + `enterprise.yml` | the Postgres job runs the parametrised suite; the kernel job still installs zero third-party packages |

**Step 4 is the one that matters.** Two stores that disagree about a blast
radius is a platform that answers differently depending on where it is
deployed, and that is the failure §22's conformance rule exists to catch. It is
also the step where the `instr`/`position` and `MIN`/`LEAST` substitutions are
actually exercised rather than merely unit-tested.

## What is explicitly not in phase 15

- **Read replicas and `STALE_REPLICA`.** §23's region model. Needs 15 first.
- **FastAPI, Celery, cloud object stores, framework discoverers.** Phase 16.
- **Any change to `slpie/`.** Ring 0 does not learn Postgres exists. The
  existing `test_slpie_boundaries.py` and `test_enterprise_boundaries.py` walks
  are what enforce it, unmodified.

## Risks, and the cheap answer to each

| Risk | Answer |
|---|---|
| The CTEs port but behave differently on ties | Step 4 compares row for row, not counts. Ordering is already total in both |
| `psycopg` connection handling under Celery workers | Not phase 15's problem — `engine.py` exposes a pool and phase 16 owns the fork discipline |
| Alembic drifting from `schema.py` | One test: build from `schema.py`, build from Alembic head, compare the introspected schema |
| No Postgres in this environment | The suite skips loudly; CI is where the gate actually runs. Do not simulate a database |


---

## What the plan got wrong

Kept rather than edited away, because the corrections are the useful part.

**Three dialect substitutions were four.** `anchors` was not planned and was
found by running the query. SQLite is untyped; Postgres types a recursive CTE
from its seed row, so `SELECT :root, 0, '…', 1.0` seeds `numeric` against a
`double precision` walk and the whole query is refused:

```
recursive query "reach" column 4 has type numeric in the non-recursive term
but type double precision overall
```

A loud failure rather than a wrong answer — the good kind, and only because it
was executed. Nothing about reading the SQL would have found it.

**`NOTIFY` takes no parameters at all**, not for the payload and not for the
channel, so building it means interpolating both into SQL. `pg_notify(channel,
payload)` is an ordinary function and binds both. The send path now reaches SQL
with no text substitution whatsoever; `LISTEN` remains the only place a name is
interpolated, and it is validated.

**`verify()` returns `None` and raises.** The first version here returned
`(ok, reason)`, which reads better at a call site and is a *different protocol*
— a caller written against the published one would treat the tuple as truthy
and call every chain intact, including a broken one. Implementing a published
protocol means implementing that protocol, including the parts you would have
shaped differently.

**Alembic needs a SQLAlchemy connection**, which is the whole reason SQLAlchemy
is in the extra. A raw psycopg connection fails with `'Connection' object has
no attribute 'dialect'`. It touches nothing else: the stores use psycopg
directly and the traversal is never rewritten into an expression language.

**A shared row mapper was not in the plan and should have been.** Both stores
build the same tables, so a node row is a node row whichever engine returned
it. `slpie/graph/rows.py` is that mapping, once, and the SQLite store now
delegates to it. Two copies would have been faster to write and would
eventually have disagreed about what a retired node looks like.

**The empty ledger needed a second lock.** `FOR UPDATE` locks a row that
exists; the first append has no tail, so two processes racing to write sequence
1 would both find nothing and both claim it. `pg_advisory_xact_lock` covers
exactly that window and releases with the transaction.

## What the schema parity test caught on its first run

`graph_meta` was missing from the Postgres DDL. That is the test doing its job
before a human could have noticed, and it is why the comparison is column for
column rather than table by table.

## Running it here

Postgres 16 is installed in this environment, so the conformance gate runs
locally rather than only in CI:

```bash
export PGDATA=/var/lib/pgdata-slpie
su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $PGDATA -U slpie --auth=trust"
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -o '-p 55432 -k /tmp' -l /tmp/pg.log start"
psql -h /tmp -p 55432 -U slpie -d postgres -c "CREATE DATABASE slpie_test;"

export SLPIE_DATABASE_URL="postgresql://slpie@/slpie_test?host=/tmp&port=55432"
python -m pytest -q -m postgres
```

## Still not in phase 15

Unchanged from the plan: read replicas and `STALE_REPLICA` (§23), FastAPI,
Celery, cloud object stores and framework discoverers (phase 16). And ring 0
still does not know this package exists — asserted by import walk, not by grep,
because a ring-0 module *naming* the ring-1 adapter its protocol is for is the
seam being legible rather than a violation.
