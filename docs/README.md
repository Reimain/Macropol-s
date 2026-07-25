# Gratimos

A self-building agent kernel. Point it at an environment — a folder, a database,
an API, a bucket — and it reads what is there, infers the shape of it, generates
code against those shapes, and records every step on a versioned spine that can
be rolled back.

```python
from gratimos import Depth, govern

report = govern("./data", depth=Depth.GENERATE)
print(report.summary())
```

```
depth=generate  cycles=2  0.31s
entities  : billing_invoices, customers, events, logo_image, notes, orders, quarterly
generated : billing_invoices, customers, events, orders, quarterly
stopped   : context is stable: a full cycle changed nothing
```

The generated modules are ordinary Python you can read, import, and edit:

```python
from gratimos_generated.orders import Orders, SHAPE

record = Orders.from_record({"order_id": "1001", "customer": "Ada",
                             "total": "99.50", "placed": "2026-01-14",
                             "priority": "true"})
# Orders(order_id=1001, customer='Ada', total=99.5,
#        placed=datetime.date(2026, 1, 14), priority=True)
```

Nobody wrote `Orders`. It came from a CSV.

## Install

```bash
pip install -e .            # kernel: zero dependencies
pip install -e '.[all]'     # optional backends: xlsx, sql, s3, delta, media
```

Python 3.10+. The kernel itself imports nothing outside the standard library —
including the `.xlsx` reader, which parses OOXML directly. Optional packages
widen coverage; they are never required to start.

## Command line

```bash
gratimos scan ./data                      # discover and shape, change nothing
gratimos govern ./data --depth govern     # the full loop
gratimos shapes ./data orders             # one entity's inferred shape
gratimos trace ./data/.gratimos -s        # what happened, by kind and actor
gratimos transforms new ./ws clean_orders # scaffold a transformation
gratimos connectors                       # which storage backends are reachable
gratimos serve ./data                     # expose the kernel as an A2A agent
```

## Depth

Depth is the stop condition. Each level contains the ones below it.

| Depth | What it does |
|---|---|
| `sense` | Discover sources and infer shapes. Changes nothing. |
| `model` | Register shapes, propose migrations. Writes no code. |
| `generate` | Emit and merge code for observed shapes. |
| `transform` | Run approved transformations over captured data. |
| `govern` | Apply safe migrations, consult peer agents, iterate until stable. |

Pair it with a `Budget` (files, rows, cycles, seconds, memory) — depth says how
far, budget says how much.

## What it reads

JSON, JSON Lines, CSV/TSV, XLSX (natively — no `openpyxl` needed), SQLite, YAML,
HTTP APIs, images (PNG/JPEG/GIF/BMP/WebP), video containers (MP4/MOV/AVI), shell
scripts, and plain text. Each probe reports a *confidence*, and the runner-up is
recorded — so when a `.txt` full of CSV gets read as text, the trace says why.

Databases too big to read arrive as a stream:

```python
from gratimos.probes import stream_query

for chunk in stream_query(connection, "SELECT * FROM events", chunk_size=5000):
    hub.publish(chunk)   # one bounded, shaped payload at a time
```

## What makes it safe

**Regeneration is a merge, not an overwrite.** Generated modules are merged at
the AST level against the previously generated source, so reformatting is not
mistaken for an edit, hand-written helpers survive, and a symbol marked
`# gratimos:keep` is never touched. When the generator and a human changed the
same symbol, that is a conflict — and it is raised, not guessed.

**Rollback that stops stale writes.** Every mutation lands on a hash-chained
event log. An agent that read the world, thought for a while, and comes back to
write is checked against what landed in the meantime: a write to paths that
moved raises `TimetravelConflict` instead of silently overwriting. Rewound
history is recorded going forward, so the trace shows the rewind rather than
hiding it.

**Transformations run gated and boxed.** Operator-supplied `.py` files are
statically checked before they are ever executed (no `socket`, no `subprocess`,
no `eval`, no `__subclasses__`), then run in a separate process under real
`RLIMIT_CPU`, `RLIMIT_AS`, and `RLIMIT_FSIZE`, exchanging JSON only. See
[the sandbox's own docstring](../gratimos/transforms/policy.py) for an honest
statement of what that does and does not defend against.

**Network access is guarded by default.** The API probe resolves the target and
refuses private, loopback, and link-local addresses unless explicitly allowed —
so a public hostname pointing at `169.254.169.254` does not get through.

**Decisions are explicable.** Every routing, storage, codegen, and migration
choice comes from a named rule with a stated reason, recorded on the trace:

```
regenerate (codegen.first-generation: no module exists for this entity yet)
stage_review (migration.data-loss: dropping a field discards observed data)
```

## Talking to other agents

Gratimos speaks [A2A](https://a2aproject.github.io/A2A/) in both directions —
agent cards, JSON-RPC, the task lifecycle, streaming.

```python
from gratimos.a2a import AgentRegistry
from gratimos.a2a.adapters.claude import claude_agent
from gratimos.a2a.adapters.uipath import UiPathConfig, UiPathConnection

registry = AgentRegistry(flow)
registry.host(claude_agent(hub=hub))                     # Claude, with kernel tools
registry.add(UiPathConnection(UiPathConfig.from_env())
             .connect("InvoicePosting"))                  # a UiPath process

registry.ask(payload.records(), skill="analyze")          # routed by capability
```

The UiPath adapter is the interesting one: a UiPath agent that publishes an
agent card is reached natively over HTTP, and everything else is reached by
starting an Orchestrator job and mapping its lifecycle onto A2A — including
`Suspended → input-required`, which is the one state both systems mean the same
thing by.

## Layout

| Package | What lives there |
|---|---|
| `gratimos.contextflow` | Causal clocks, immutable events, the versioned spine |
| `gratimos.trace` | Journal, checkpoints, rollback, the timetravel guard |
| `gratimos.meta` | Shapes, inference, casting, self-describing wrappers |
| `gratimos.probes` | Reading JSON, CSV, XLSX, SQLite, APIs, media, scripts |
| `gratimos.hubs` | Routing channels, memory budget, spill staging, registries |
| `gratimos.storage` | Path-contained local repository, S3/Delta/cloud connectors |
| `gratimos.codegen` | Emitters, protobuf, AST merge, generation history |
| `gratimos.transforms` | Static gate, capability policy, isolated executor |
| `gratimos.policy` | The rule engine and the four standing policymakers |
| `gratimos.migrations` | Reversible revision ledger, Alembic rendering |
| `gratimos.a2a` | Types, transports, server, client, registry, adapters |
| `gratimos.orchestrator` | Depth, budget, and the loop that spends them |

`docs/ARCHITECTURE.md` explains why each boundary sits where it does.

## Tests

```bash
pip install -e '.[dev]' && pytest
```

169 tests, no network, no fixtures that mock the thing under test — the XLSX
tests parse a real workbook, the SQLite tests open a real database, the sandbox
tests actually get killed by the kernel's CPU limiter.
