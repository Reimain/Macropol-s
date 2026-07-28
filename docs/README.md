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

Python 3.11+ (for stdlib `tomllib`, which SLPIE reads `pyproject.toml` and
`Cargo.toml` with). The kernel itself imports nothing outside the standard
library — including the `.xlsx` reader, which parses OOXML directly, and the
package-registry crawler, which is built on `urllib`. Optional packages widen
coverage; they are never required to start.

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

## Not writing the code at all

The cheapest module is the one that already exists. Gratimos will go and look
for it, prove it fits, and remember the answer.

```python
from gratimos import ReuseAssessor, need_from_text
from gratimos.crawl import Crawler, Fetcher, PyPISource, SourceRegistry, official_policy
from gratimos.ontology.need import Constraint, ConstraintKind

fetcher = Fetcher(official_policy())
crawler = Crawler(SourceRegistry(PyPISource(fetcher)))
need = need_from_text(
    "I need to retry failed HTTP requests with exponential backoff",
    constraints=[Constraint(ConstraintKind.ECOSYSTEM, "pypi")],
)

print(ReuseAssessor(crawler=crawler).assess(need).reasoning)
```

```
need: I need to retry failed HTTP requests with exponential backoff
crawled pypi for retry-with-backoff, backoff, exponential backoff, retry, … → 2 artifacts
refused 1:
  - pkg:pypi/backoff@2.2.1: blocked — licence: AGPL-3.0-only triggers on
    network_service: network use obliges you to offer the complete corresponding
    source of the whole work
admitted 1, best first:
  1. pkg:pypi/retry@0.9.2 → 0.71 = fit 0.67×0.30 — covers 67% of the required
     concepts, 2 of them exactly; maintenance 1.00×0.20 — last release 89 days
     ago; licence 0.95×0.10 — permissive obligations to absorb; …
gaps:
  ! pkg:pypi/retry@0.9.2 matches on naming alone (0.34); nothing has confirmed
    it does what the need asks
```

Four things that are load-bearing rather than decorative:

**The refusal is part of the answer.** A shortlist that silently omits the
obvious library looks like the tool never found it. Every excluded candidate
comes back with its blocker and a remediation.

**The score reconstructs itself.** Every component carries its weight, its raw
measurement and a sentence. Popularity is the weakest input on purpose — it is
the easiest signal to get and the least related to whether a package does what
you need.

**Needs are keyed by meaning, not phrasing.** "I need to retry failed HTTP
requests with exponential backoff" and "HTTP client retry, exponential backoff"
hash to one signature, because the signature is computed over canonical concepts
from the ontology rather than over the words.

**The agent gets turned off.** Attach a validator and it is consulted once per
distinct need; every later phrasing is answered from memory. Two things stop
that becoming a machine for repeating stale answers: verdicts decay on
volatility half-lives and the escalation floor applies to the *decayed*
confidence, and a sampled fraction of hits is re-validated anyway — without it
the base can only ever confirm itself.

```python
loop = DistillationLoop(KnowledgeBase(), claude, engine=engine)
assessor = ReuseAssessor(crawler=crawler, loop=loop)

assessor.assess(need)                  # agent consulted
assessor.assess(reworded_need).turn.route   # Route.RECALLED — not consulted
```

The crawler is polite by construction: an allow-list of hosts that publish a
documented API, one request per host per second, `robots.txt` per origin,
conditional requests so a second crawl is a sequence of 304s, and `Retry-After`
obeyed literally and capped. It runs entirely offline against recorded
responses — which is how its own tests exercise the real rate limiter rather
than a stand-in for it.

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
| `gratimos.ontology` | The concept lattice; needs keyed by meaning, not phrasing |
| `gratimos.reason` | Forward chaining to propagate, backward chaining to answer |
| `gratimos.crawl` | Polite, cached, offline-capable registry discovery |
| `gratimos.reuse` | The licence gate that excludes, the ranking that explains |
| `gratimos.distill` | Calling the validating agent only when memory cannot answer |

`docs/ARCHITECTURE.md` explains why each boundary sits where it does.

## Tests

```bash
pip install -e '.[dev]' && pytest
```

No network, no fixtures that mock the thing under test — the XLSX tests parse a
real workbook, the SQLite tests open a real database, the sandbox tests actually
get killed by the kernel's CPU limiter, and the crawler tests drive the real
fetcher over a recorded transport so the rate limiter, the robots gate and the
retry loop all genuinely run.
