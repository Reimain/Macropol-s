# Clients — ring 2

Three shells over **one generated API client**. The client is not written here: it
is emitted from the verb registry by `slpie contract --typescript`, so a
capability added on the server becomes a compile error in every shell that has
not handled it, rather than a runtime 404 somebody reports from production.

```
clients/
  web/       the enterprise console — React + TypeScript
  desktop/   Tauri. The portal: many environments at once, each its own kernel
  mobile/    React Native. Read-and-approve rather than full administration
```

## Status, stated plainly — and asserted

<!-- Parsed by tests/test_slpie_clients.py. Edit the table and the tree, or the
     suite fails: a status claimed here that the tree does not support is the
     one kind of documentation error nobody notices until a customer clones it. -->

| shell | state | what that means |
|---|---|---|
| `web` | **builds** | `npm run build` runs `tsc --noEmit && vite build` and produces `dist/`. The browser tier drives that bundle |
| `desktop` | **scaffold** | a Tauri config and the generated client. No Rust toolchain here, so nothing has been compiled |
| `mobile` | **scaffold** | a React Native package and the generated client. No simulator here, so nothing has been run |

**A scaffold is a shell with no build command, and that is checkable rather than
descriptive.** `test_the_readme_status_matches_the_tree` reads this table and
holds it against the directories: a shell claiming *builds* must have a build
script and an entry point, and a shell claiming *scaffold* must have neither. So
the day somebody adds a Tauri build the README goes red rather than stale.

The reason to state it this way at all: a green tick for something that was
never compiled is worse than an honest scaffold. It is the same rule the
platform applies to its own answers — `INDETERMINATE` never passes as upheld
(§25), a missing binary is a reported gap rather than a silent fallback (§27),
and a shell that has not been built says so.

**`web` builds. `desktop` and `mobile` are still scaffolds.**

This paragraph used to say that the environment had no Node toolchain and that
building here would be a claim nobody had verified. That is no longer true —
Node 22 is present — and the first thing running the build did was find a
defect the whole test suite had missed: the generated TypeScript client did not
compile, and never had. It emitted `max-bytes?: number` into a type literal,
which is not a property name TypeScript can parse.

Nothing caught it because every test around the emitter asserted the output was
**total** (every verb present) and **deterministic** (byte-identical across
runs) and none asserted it was **valid**. A generator can be perfectly
reproducible and reproducibly wrong. `tests/test_slpie_clients.py` now checks
the property names in pure Python, and the opt-in job actually compiles.

## `web` — and the rule that keeps two shells one product

The stdlib console is deliberately minimal, and it has a real ceiling. Blocks
as data render tables, grids, metrics and the composed inspector. What they
cannot express is a screen whose behaviour *is* the interaction — panes the
reader drags to size, a scrubbable axis over the ledger, a route you re-aim by
dragging. That is code, and pretending otherwise turns the manifest into a bad
framework.

So `web` exists, and it holds exactly those screens. What it does **not** do is
hold a second copy of anything:

```
clients/web/src/scene/  →  @scene/*  →  slpie/ui/app/engine/*
```

The alias points at ring 0's renderer tier, and the shells share the *scene* —
projection, deterministic layout, region colouring, glyph geometry,
aggregation — verbatim. Not a port kept in step by discipline: the same files.
`test_no_scene_module_is_copied_into_a_client` compares content digests, so a
copy made under a new name still fails.

It shares the scene and **not the console**: `core/`, `ui/` and `screens/` are
the stdlib shell's own DOM helpers, components and router, and reaching into
them would couple a built shell to a rendering strategy rather than to a model.
That is asserted too.

TypeScript checks this package against the ring-0 JavaScript directly
(`allowJs`), so there is no `.d.ts` to go stale — a declaration file would be a
build artifact in a directory that deliberately has no build step.

## Which screens land here, and why

Not a tier. A screen declares what it **needs** (`Screen.requires`) and a shell
declares what it **gives** (`contract.SHELLS`), so the console can name the
missing capability instead of saying "not available here", and a third shell
joins by adding a row. `GET /api/shells` reports both sides.

Today: 33 of 34 screens render in the stdlib console. `flight` does not, and
the console says so — it needs `split-pane`, `timeline` and `drag`.

## Building it

```bash
cd clients/web
npm install
npm run build        # tsc --noEmit && vite build
npm run dev
```

Three code-splits into its own chunk and is fetched on demand, because the seam
resolves it through a dynamic import rather than a static one — the same
mechanism that lets the stdlib console run without it at all.

## The stdlib UI is not one of these

`slpie/ui/` stays exactly as it is — responsive, installable, and dependent on
nothing. Inside an air-gapped network it is the only one that runs, and it is the
one the tests drive. These three are additive.

## Regenerating the client

```bash
slpie contract --typescript > clients/web/src/slpie-client.ts
cp clients/web/src/slpie-client.ts clients/mobile/src/
cp clients/web/src/slpie-client.ts clients/desktop/src/
slpie contract --openapi > clients/openapi.json
```

Committing the generated file rather than generating at build time is deliberate:
a reviewer can see in the diff that a route changed, which is the whole point of
having a contract.
