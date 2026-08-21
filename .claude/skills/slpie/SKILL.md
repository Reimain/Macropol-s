---
name: slpie
description: Orientation for the Gratimos + SLPIE codebase — the invariants, the ring rule, the operating policy, and how to query the generated map of every verb, route, screen, module and plan section. Load this before working anywhere in this repository, and whenever a question starts "where is" or "what depends on".
---

# SLPIE — how this product is put together

Two packages in one repository. **Gratimos** is a data-shaping kernel; **SLPIE**
is an architecture intelligence platform built over it. SLPIE owns everything
except code generation, which it calls Gratimos for — through exactly one import,
asserted by test.

The generated half of this skill is **`INDEX.md`** and **`index.json`**. Both
carry every verb, route, screen, component, module, test file and plan section,
with what connects to what. Both are produced by `slpie context --skill` and are
never edited by hand.

**Read `INDEX.md`. Do not read `index.json`** — it is ~440KB and exists to be
queried by a program, not loaded into a context window. Use `slpie context query`
for a single answer, or `jq` against the file when you need something the CLI
does not render.

## Ask the map before you grep

```bash
slpie context                        # counts, coverage, the digest
slpie context query verb:findings    # where it is declared, what runs it
slpie context query screen:graph     # what it reads, what hangs off it
slpie context query lockfile         # search, when you do not know the id
slpie context --json                 # every facet, for a machine
slpie context --digest               # one comparable value for CI
```

Facet ids are `kind:name` — `verb:`, `route:`, `screen:`, `component:`,
`module:`, `package:`, `test:`, `section:`, `kind:`. A query prints outbound
links with `→` and inbound with `←`, because "what does this screen read" and
"who reads this route" are the same question from opposite ends.

## The invariants, and where each is asserted

These are not style preferences. Each one is enforced by a test that fails.

1. **No relationship without evidence** — `Edge.__post_init__`.
2. **Confidence is derived, never assigned.** No caller passes a number. The
   ladder is `slpie/domain/evidence.py`; reflection/dynamic/heuristic-only
   evidence caps at 0.60, a single lockfile pin short-circuits to 1.00.
3. **Append-only.** Corrections supersede; retirement sets `valid_to`. Nothing
   is deleted, ever — a rollback is a supersession.
4. **Zero third-party dependencies in the kernel, including the UI.** Stdlib
   server, no CDN, no webfont, no build step, `script-src 'self'`.
   `test_the_kernel_has_no_third_party_dependencies`.
5. **Every answer carries a reasoning path and its gaps.** Never a bare value.
6. **Everything is a plugin.** Built-ins register through the identical path a
   third party uses.
7. **Simulated and live differ only in binding.** No branch on target above
   `binding/`.
8. **Exactly one SLPIE module imports Gratimos** — `slpie/artifacts/codegen.py`.
   `tests/test_slpie_boundaries.py`.
9. **The kernel never learns a framework exists.** FastAPI, Celery, cloud SDKs
   and React live in ring 1 and ring 2.

## The rings

```
ring 0   slpie/  gratimos/       stdlib only · offline · installs with no extras
ring 1   slpie_enterprise/       kubernetes · boto3 · optional extras
ring 2   clients/                React web · Tauri desktop · React Native
```

Ring 1 imports ring 0's public API. **Ring 0 never imports ring 1** — it does not
know it exists. `tests/test_enterprise_boundaries.py` asserts both directions.

The browser code applies the same rule one level down: `core/` imports nothing,
`data/` and `ui/` import `core/` only, `screens/` may import all three, nothing
imports upward.

## Composition is the operating philosophy

Capabilities are **verbs** in one registry (`slpie/compose/registry.py`). The
CLI, the HTTP API, the manual, the planner, the screens and every client are
*projections* of it. A verb added once appears in all of them; a verb added
without wiring fails `tests/test_slpie_compose.py`, which asserts the projection
is total.

Pipes are **typed**, and the pipe carries provenance rather than bytes — so
`scan | link | findings --severity high` ends with findings still carrying the
capability refusal from `scan`. Composing accumulates the explanation instead of
discarding it.

An invalid composition is refused **before anything runs**, with both kinds
named.

## Doctrine that has been paid for

Things learned the expensive way. Ignore them and the same bug returns.

- **Verify a guard fails red before trusting it green.** Four tests in this
  repository once passed over zero files after a rename. Every filesystem walk
  now goes through `tests/_walk.py`, which refuses to match nothing. When you
  add a check, break the thing it checks and watch it fail first.
- **A refusal is never rendered in red.** 403 is `.refusal` with the accent
  colour and the sentence verbatim; 500 is `.fault` in red. Rendering policy as
  a fault teaches people to file tickets about working controls.
- **Confidence is not goodness.** An ordinal blue ramp carries certainty; the
  reserved status palette carries severity. Never mix them.
- **Colour is never the only channel.** Every severity, verdict and target state
  also carries a glyph and a word.
- **Measure before believing.** The "maroon bug" in the light theme was subpixel
  antialiasing — the computed colour was `rgb(20,27,30)`. The palette failure was
  `deutan ΔE 1.4`, not a matter of taste.
- **Density and theme are independent token axes.** Palette tokens carry no
  sizes; geometry tokens carry no colours. No raw `px` outside the token files.
- **A screen with a `parent` is a view, not a destination.** It renders as a tab
  on its parent's page and never as a rail row — `Screen.is_destination`.
- **Generated artifacts are gated, not regenerated.** `data/client.js`,
  `openapi.json`, the three `slpie-client.ts` files and this skill's `index.json`
  all have parity tests. Regenerating once is not a fix.

## Operating policy

- **Never merge to `main` without explicit confirmation from the user.**
- Develop and push only to the assigned feature branch. Push with
  `git push -u origin <branch>`.
- Do not open a pull request unless asked.
- The test suite must pass with **zero third-party packages installed** for the
  kernel job. That is invariant 4, and it is a CI job, not an aspiration.

## Verifying a change — the ladder, not the full suite

**`pytest -q` costs 285 seconds.** Running it after every edit is the single
largest waste available in this repository: a session that runs it eight times
has spent forty minutes watching dots. Climb the ladder instead, and run the
full suite **once, before the commit**.

| Change | Run | Cost |
|---|---|---|
| one module | its own test file | < 2s |
| a verb, a route, a screen | `test_slpie_compose.py test_slpie_contract.py test_slpie_ui_assets.py` | ~100s |
| anything under `app/` | `test_slpie_ui_assets.py test_slpie_ui_engine.py test_slpie_clients.py` | ~7s |
| anything that renders | `pytest -q -m browser -k <name>` | ~10s |
| before committing | `pytest -q` then `pytest -q -m browser` | 285s + 102s |

`-k` on the browser tier is the difference between 10 seconds and 102. Use it
while iterating; drop it before the commit.

### Regenerate before the full suite, never after

Four artifacts are committed and parity-gated, so a stale one fails the suite
and costs a second 285-second run. Do this *first*:

```bash
python -m tools.clients          # client.js, openapi.json, three .ts clients
python -m slpie.cli context --skill   # this skill's generated half
python -m tools.vendor --check   # the vendored third-party bytes
```

### The rest

```bash
slpie context --digest           # unchanged tree → unchanged digest
slpie audit --digest             # the architecture has not drifted
slpie ui --once                  # the front door opens
python acceptance.py             # the full end-to-end run, offline
```

## Operations that block, and what they actually cost

Measured in this environment, not estimated. Knowing which of these is slow is
what stops a session budgeting its time by accident.

| Operation | Cost | Note |
|---|---|---|
| `pytest -q` | 285s | the whole default suite |
| `pytest -q -m browser` | 102s | opt-in; needs `.[e2e]` |
| `pytest -q -m browser -k x` | ~10s | use this while iterating |
| `python -m tools.clients` | ~20s | rebuilds the verb registry |
| `slpie context --skill` | ~17s | cached on a tree digest (§32 step 0) |
| `npm install` in `clients/web` | ~11s | registry is reachable |
| `npm run build` | ~3s | tsc + vite |
| `npm view` / `npm pack` | works | the proxy allows the npm registry |

**What is *not* blocked, despite looking like it:**

- **The npm registry is reachable.** Node 22 and npm are installed. `clients/web`
  installs and builds here. The old claim in `clients/README.md` that this
  environment had no Node toolchain was stale and is corrected.
- **WebGL works headless.** Chromium at `/opt/pw-browsers` has SwiftShader, so
  the vendored Three engine is exercised for real by the browser tier.
- **`pip install -e '.[enterprise]'` works** — once the extra exists. It did not
  until now: §22 specified `enterprise` and `pyproject.toml` never declared it,
  so the command failed with *"does not provide the extra"*, which reads like an
  architectural blocker and was five lines of packaging.

**What genuinely cannot be done here:** reaching hosts the proxy refuses
(`grok.me` is one, refused at CONNECT with a 403). Record those as gaps rather
than working around them.

## Where the build actually is

Phases 1–13 are built. §§24–32 are the composition spine, the audit judge, the
capture firewall, the console and the graph — all inserted **before** the
enterprise rings, which is why 14–18 look untouched.

| Phase | State |
|---|---|
| 14 — seams | done: `TaskRunner`, `ResourceMeter`, `ObjectStore`, `contract.py` |
| 15 — Postgres persistence | done: `slpie_enterprise/persistence/`, conformant against a real database in `enterprise.yml` |
| 16 — FastAPI, Celery, frameworks | done: one route table over two servers, a runner that counts what it could not distribute, routes read with `ast` |
| 17 — clients | done for `web`: real data, shell capabilities, shared tokens, the ride, auth, a browser tier over the built bundle. `desktop` and `mobile` are scaffolds **and say so, tested** |
| 18 — deployment | not started |

**Phase 17's web shell never needed 16**: it talks to the stdlib server over the
same generated client, which is why it built before either ring-1 phase existed.

Two rules govern that shell and both are asserted rather than intended. It holds
**only screens the stdlib console declines** — `GET /api/shells`'s `cannot` map
*is* the backlog, and a screen both shells can draw fails
`tests/test_slpie_clients.py`. And it **shares rather than copies**: the scene
modules through `@scene`, the design tokens through `@styles`, both digest- or
import-checked. A copy renamed on the way in still fails.

Ring 1 is `slpie_enterprise/{api,frameworks,persistence,queue,spawn,storage}`.
