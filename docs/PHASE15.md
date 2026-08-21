# Phase 15 — Postgres persistence (ring 1)

Execution plan. Everything here was checked against the tree, not recalled.

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
