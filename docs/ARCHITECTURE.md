# Architecture

This document explains *why* the boundaries sit where they do. For what the
system does, read `README.md`; for how each piece works, read the module
docstrings — they carry the detail this document deliberately leaves out.

---

## The one idea

A conventional pipeline is written against a schema someone declared. Gratimos
inverts that: the schema is *observed*, the code is *derived*, and both are
expected to move. Everything else in the design follows from taking that
seriously.

If shapes move, then:

- generated code must be **merged**, not overwritten, or every regeneration
  destroys hand-written work;
- shape changes must be **recorded and reversible**, or drift is
  indistinguishable from a decision;
- decisions about what to do next must be **explicable**, or nobody can debug
  the system at 3am;
- and the whole thing must be **rollback-able**, or a bad inference is permanent.

Those four requirements produce, respectively: the AST merge, the migration
ledger, the policy engine, and the ContextFlow.

---

## ContextFlow: one spine, many writers

Every subsystem writes to one append-only log. Not for tidiness — because the
alternative is each subsystem keeping its own history and no way to answer "what
caused this?" across them.

```
capture → shape → route → spill → codegen → merge → transform → migration
                            ↑                                        ↓
                          policy ←──────── agent ────────────────────┘
```

**Versions are a hash chain.** Each append links the previous version, the
event id, and the event's content digest. A version string is therefore a
cryptographic claim about the entire prefix that produced it — which is what
makes the timetravel guard possible.

**Events are immutable; correction is append-only.** A rollback does not delete;
it truncates the live prefix, replays what survives, and appends a `ROLLBACK`
event describing the rewind. The trace shows that history was rewound rather
than looking like it never happened.

**`ROLLBACK` is inert during replay.** It carries the undone paths for audit, so
materializing them would resurrect exactly what the rollback removed. This is
the sort of detail that is obvious in hindsight and expensive to discover in
production; it is enforced by a test.

### The timetravel guard

The failure this exists to prevent is specific. An agent reads the world, spends
time thinking, and by the time it writes, the world has moved — often because
*it* rolled the world back. Committing that stale work silently reintroduces
code that was already retracted.

So every event declares the `base_version` it was computed against, and:

- if the base is the current head → land it;
- if the base is still on the live chain and nothing the event touches has
  changed since → land it (a legal rebase);
- if the base is no longer on the chain, or the paths moved → `TimetravelConflict`.

Path-level granularity is what makes this usable rather than merely safe. Two
agents working on different entities never block each other; two agents working
on the same entity from divergent bases always conflict.

---

## Shapes: evidence, not contracts

A `DataShape` records what has been *seen*: how many values, how many nulls,
example renderings, format hints. It is explicitly not a schema you must satisfy.

**The type lattice** is what makes revision safe. Merging two observations never
loses information — it widens to the smallest type admitting both, with `JSON`
as the top where structure genuinely disagrees. A field seen as `int` in one
batch and `float` in the next becomes `float`, not a conflict.

**Diffs classify by consequence, not by shape of the change.** `WIDENED` and
`ADDED` are safe; `NARROWED`, `REMOVED`, and `RETYPED` are breaking. That single
distinction is what the codegen and migration policymakers both key on.

**Casting is a policy, not a coercion.** `"12"` → `12` is a decision with three
defensible answers (strict refuses, lenient parses, coerce substitutes), so the
caller picks the mode and every bend is reported rather than assumed. A lenient
cast that silently turns `"abc"` into `0` destroys evidence; a strict cast that
refuses `"12"` where an int is wanted destroys throughput. Both are available;
neither is the default silently.

---

## Probes: confidence, not extensions

Targets lie. A `.txt` full of JSON, a `.dat` that is really SQLite, a `.csv`
with no header. So probes score rather than claim, the highest score wins, and
the runner-up is recorded on the capture. When the wrong probe wins, the trace
says who came second and by how much — the difference between a five-minute fix
and an afternoon.

Two probes deserve a note:

**XLSX is parsed natively.** An `.xlsx` is a zip of XML parts, so `zipfile` plus
`xml.etree` handles the common "someone dropped a spreadsheet in the folder"
path with no dependency at all. `openpyxl` is used when present, because it
handles the long tail (number formats, dates, merged regions) far better than
200 lines of XML ever will. The native reader deliberately returns raw numbers
rather than guessing dates: that needs the style table, and guessing wrong
silently corrupts data.

**Shell scripts are read, not run.** A folder scan that executes every `.sh` it
finds is not a feature, it is a remote code execution primitive pointed at
whatever the agent was aimed at. The default extracts the interpreter, declared
variables, functions, and invoked commands — genuinely useful context, and
inert. Execution requires `allow_exec=True` *and* an explicit path allow-list.

---

## Hubs: where memory pressure becomes a decision

"Hold it in memory if memory allows, otherwise stage it cheaply" only works if
*allows* is a number somebody chose, rather than a number discovered at the
moment of the `MemoryError`.

The `MemoryBudget` is that number. It admits, refuses, and nominates eviction
candidates by least-recent use. It is a ledger the kernel keeps honestly — not a
heap profiler, and it does not pretend to be.

`consider()` and `admit()` are deliberately separate calls: the caller stages
the payload first and only then records residency, so a failed spill never
leaves the budget believing something is resident when it is not.

**Spill accepts JSON and raw bytes only.** Pickle would spill anything — and
would also mean rehydration executes arbitrary constructors on data the kernel
merely *observed*, which is the input we should trust least. A payload that will
not encode is reported as unspillable rather than quietly made dangerous.

**Channels have a fallback delivery mode**, which is what makes a "residual"
lane a genuine *nobody wanted this* signal instead of a lane every payload also
happens to travel. That set is the most valuable thing a policymaker can look
at: it is exactly what the kernel cannot yet model.

---

## Codegen: regeneration as a three-way merge

The three sides are the previously generated source (**base** — recorded, not
guessed), what is on disk now (**ours**), and what the generator wants to emit
(**theirs**).

| ours vs base | theirs vs base | outcome |
|---|---|---|
| unchanged | changed | take generated |
| changed | unchanged | keep the local edit |
| changed | changed, identically | either — they agree |
| changed | changed, differently | **conflict** |
| absent | added | take generated |
| present | removed | drop, unless locally edited |

Comparison is on `ast.dump`, so reformatting, comment rewrites, and import
reordering are not mistaken for edits. `# gratimos:keep` short-circuits the whole
table.

Two details that are easy to get wrong and are pinned by tests:

**Conflict markers are bare.** Commented-out markers leave a file that still
parses, where Python silently takes whichever definition comes last — a conflict
that imports is a conflict that ships. Bare markers guarantee a `SyntaxError`
until a human resolves them.

**The no-op check happens before emission.** The emitted source embeds the
revision number, so generating first and diffing after would make every
regeneration differ from itself and inflate history forever. The guard is on the
shape digest, checked before anything is emitted.

**Protobuf field numbers come from a recorded allocation**, never from field
order. A proto field number is a permanent contract; removing a field must not
renumber its neighbours. Retired numbers become `reserved` entries so a later
regeneration cannot reuse one for different data.

---

## Transformations: the one place operators write executed code

The threat model is stated explicitly in `transforms/policy.py`, and it matters
that it is stated: this is a **blast-radius limiter for semi-trusted code**, not
an adversarial sandbox. Python in-process isolation is not a security boundary,
and pretending otherwise would be worse than having no sandbox at all.

Four layers, in order:

1. a static AST gate, before the file is ever executed;
2. an import allow-list enforced again at runtime, catching indirect imports;
3. OS resource limits and a wall clock in a separate process;
4. JSON-only data across the boundary, so nothing executes on the way back.

Limits are installed *before* the transformation module is imported, so a module
that misbehaves at import time is already bounded.

For genuinely untrusted code the answer is a container, a VM, or seccomp — and
pointing Gratimos at *that* as a remote executor.

---

## Policy: decisions with names

A policymaker is not a heuristic buried in a method. It is a named rule with a
condition, an action, and a stated reason, and every verdict carries the rule
that produced it plus the facts it saw.

That shape matters because these decisions get questioned. When the system
decides to stage an entity to cold storage, regenerate a module, or block a
migration, somebody eventually asks *why*, and "the code did that" is not an
answer.

It also makes the kernel overridable without a fork: register a rule with a
lower priority number and it decides first. The default set is
`install_default_policies`, and every rule in it can be displaced.

---

## Migrations: drift with an identity

Shapes evolve because the data evolved, which means the kernel is constantly
proposing schema changes to itself. Left unrecorded, that is indistinguishable
from drift.

The ledger gives every change an identity, a parent, an upgrade path, and —
wherever possible — a downgrade path. Revision ids are content-addressed, so the
same change on the same parent produces the same id on any machine.

The chain is Alembic-shaped on purpose (`revision` / `down_revision` /
`upgrade` / `downgrade`), so a change proposed here can be handed to Alembic
without translation. Rendering does not require Alembic to be installed — the
output is text, and text does not need the library that will later read it.

**Irreversible migrations say so.** Dropping a field is structurally
recoverable and materially not; the generated `downgrade()` raises
`NotImplementedError` with the specific fields named, rather than silently
recreating empty columns.

---

## A2A: one interface, several kinds of peer

The protocol layer implements the wire contract — agent cards, JSON-RPC, typed
parts, the task lifecycle, SSE streaming — and the adapters put other systems
behind it.

**The Claude adapter** renders a `DataPart` with its shape alongside its records,
so the model sees structure rather than a wall of JSON, and can be granted
kernel tools (`list_entities`, `describe_entity`, `sample_entity`) so it answers
by looking at the data instead of being told about it.

**The UiPath adapter** is the more interesting bridge, because UiPath agents
reach the world two ways. One publishes an agent card and is already a peer.
Everything else is an Orchestrator job, and `UiPathTransport` implements the A2A
transport contract on top of that: `message/send` starts a job, `tasks/get` maps
its state, `tasks/cancel` stops it. The state table is the substance — a UiPath
job has no `input-required` and an A2A task has no `Suspended`, so the two
lifecycles are reconciled in one place rather than scattered through the code.

Both directions are recorded on the ContextFlow as `AGENT` events, which is the
point of routing agent traffic through the kernel rather than around it: when a
peer's answer reshapes a model, the trace shows the message that did it.

---

## The loop

```
sense → absorb → decide → generate → migrate → transform → reflect
```

Each stage is gated by depth, records what it did, and the whole cycle runs
inside a rollback scope — so a cycle that fails halfway leaves the context where
it started rather than half-built.

Two convergence properties make "run until stable" a real stop condition rather
than an aspiration:

**Absorption is content-addressed.** A payload whose digest matches the last
cycle's is not re-absorbed, so unchanged files do not churn the hub.

**Transformations are memoized on `(transform source, input payload)`.** A
transformation is a function of its input; re-running it on a payload it has
already consumed produces the same output and would keep every cycle looking
"productive" forever.

With both in place, a cycle that changes nothing means the kernel has finished
modelling what it can see — which is what *governing the context* looks like
from the outside.

---

## Deliberate omissions

Things that are absent on purpose, so their absence is not mistaken for an
oversight:

- **No async.** The kernel is I/O-light and CPU-bound on parsing and AST work.
  Async would add colour to every function for no measured gain. The A2A
  transports are the natural place to add it if network fan-out ever dominates.
- **No ORM.** Shapes are observations; an ORM wants declarations. The migration
  ledger renders to Alembic precisely so the declarative half lives where such
  things belong.
- **No plugin auto-discovery via entry points.** Probes, channels, connectors,
  and rules are registered explicitly. A system that executes code it found is
  the failure mode this design spends the most effort avoiding.
- **No `gs://` or `az://` adapter bundled.** Both schemes are declared in the
  connector registry so their absence is *visible* — `gratimos connectors` shows
  them as unavailable with the package that would enable them — rather than
  failing with an `ImportError` mid-run.
