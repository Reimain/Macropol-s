# Phase 17 — clients (ring 2)

Execution plan. Checked against the tree, not recalled.

## Where it already is

Phase 17 is **half built**, and not in the order §22 assumed. §22 says 17 needs
16; the web shell does not, because it talks to the stdlib server over the same
generated client. That turned out to be the load-bearing fact.

| Piece | State |
|---|---|
| `clients/web` | **builds** — Vite, React 18, `tsc --noEmit && vite build` green |
| the generated TS client | committed, parity-gated, covers 51 verbs |
| verbatim scene reuse | `@scene/*` → `slpie/ui/app/engine/*`, digest-checked |
| the `Flight` workbench | one screen, split-pane, live tally from the renderer |
| shell capabilities | `Screen.requires` / `contract.SHELLS`, `GET /api/shells` |
| `clients/desktop` | scaffold. No Tauri, no Rust toolchain here |
| `clients/mobile` | scaffold. No React Native, no simulator here |

So phase 17 is not "build three clients". It is **finish the one that builds,
and be honest about the two that cannot be built in this environment.**

## The decision that shapes everything else

`clients/web` currently holds one screen and synthetic data. Two ways forward:

| Option | What it means |
|---|---|
| **Port the console** | Re-implement the 34 screens in React. Two implementations of every screen, kept in step by discipline. Rejected — this is exactly the drift the capability model was built to avoid |
| **Only what ring 0 declines** | Build the screens whose `requires` the stdlib shell cannot meet, and nothing else. Today that is `flight`; tomorrow whatever else genuinely needs `drag`, `split-pane`, `timeline` or `virtual-scroll` |

**Decided: only what ring 0 declines.** The capability model already answers
"which screens belong here" — `GET /api/shells` returns `cannot` per shell, and
that list *is* the backlog. A screen that both shells can draw belongs in the
one that runs air-gapped.

This has a consequence worth stating: `clients/web` is not a replacement
console. It is a **companion** — a reader opens it for the four or five screens
that need a toolchain and works in the stdlib one the rest of the time. Anything
else means maintaining two products.

## Steps

| Step | Delivers | Gate |
|---|---|---|
| **1** | `useApi` over the generated client; the `Flight` workbench reads `GET /api/graph` and `GET /api/impact` instead of synthetic nodes | the screen renders a real estate; the synthetic generator is deleted, not left behind a flag |
| **2** | The shell reads `GET /api/shells` and renders **only** screens the stdlib one declines, with the same refusal card in reverse for anything it cannot draw either | a screen appearing in both shells fails a test |
| **3** | Tokens shared, not restated — `styles/tokens.css` imported rather than a second palette | no colour literal in `clients/web/src`, checked the way `components.css` already is |
| **4** | The ride wired: `condition.js`, `route.js`, `ride.js`, `narrate.js` driving the workbench, with the timeline scrubbing `at(route, seconds)` | the same hops in the same order as the stdlib flight mode, asserted across both |
| **5** | Auth: the generated client sends `Authorization`, the gateway does the rest | a 403 renders the refusal card with `Decision.explain()`, not a fault |
| **6** | Playwright over the built bundle | the four claims §30 makes about the stdlib shell, made about this one |
| **7** | `desktop` and `mobile` **declared, not faked** | `clients/README.md` states what is scaffold and what builds; a test asserts the README's claim matches the tree |

## What is deliberately not in phase 17

- **Porting the 34 stdlib screens.** See the decision above.
- **A second design system.** Tokens come from ring 0 or the two shells drift
  within a release.
- **Tauri and React Native builds.** No Rust toolchain and no mobile simulator
  here, and a green tick for something that was never compiled is worse than an
  honest scaffold. Step 7 makes the scaffold state a *tested* claim.
- **Server-side rendering, a router library, a state library.** The store,
  the router and the lexicon exist in ring 0 and are framework-free.

## Risks

| Risk | Answer |
|---|---|
| The two shells drift visually | Tokens are imported from ring 0, and step 3 forbids a colour literal |
| The two shells drift behaviourally | The scene modules are shared verbatim and digest-checked; step 4 asserts the ride produces the same hops in both |
| `clients/web` quietly becomes the real console | Step 2's test: a screen both shells can draw is a failure here |
| A stale generated client | Already gated — `tools/clients.py --check` in CI, and `tsc` now actually compiles it |
| Node in CI | Already added for the web build in `ui.yml` |

## The one thing to fix first

`clients/web/src/Flight.tsx` builds its own 900 synthetic nodes. That is a demo
that looks like an answer, which is the thing this product exists to stop.
Step 1 replaces it and **deletes** the generator rather than leaving it behind a
flag — a fallback to fabricated data is exactly how a screen ends up showing
something plausible when the API is down.

## What the plan got wrong

Written after building it, in the shape §15's post-mortem takes. Three of these
are defects the plan could not have predicted and one is a gap it created.

**The generated TypeScript client could not reach a single read route.** It
emitted a method per verb — 59 of them — and none for the 34 `GET` routes, so a
TypeScript consumer could call `impact` as a verb and could not read
`/api/graph` at all. Step 1 could not begin until `typescript()` grew
`_read_name()` and a method per non-parameterised read. Found by trying to use
it, which is the only way that class of gap is ever found.

**The generated client held `fetch` unbound, and had since it was written.**
`this.doFetch = options.fetch ?? fetch` stores a method of the global object on
an instance; calling it as `this.doFetch(...)` throws
`TypeError: Illegal invocation`. **The shell could not make one request in a
real browser** — and every structural test passed, because none of them ran
one. This is the same family as the `max-bytes` defect the last phase found: a
generator that is total, deterministic, and wrong. Caught on the first run of
step 6's tier, which is the whole argument for that tier existing.

**Severity is not on a node, so the screen has to join it.** The plan said "read
`GET /api/graph`" as though the payload carried everything the scene draws.
`Node.to_dict()` has no severity, and it should not: a finding is what
governance raised *against* a subject, not a property of it. So the screen reads
`/api/findings` too and joins by subject, keeping the **worst** severity — a
join that kept the last one read would make the same estate look different
depending on the order a query returned.

**A shared scene needs shared tokens, and step 3 was nearly cosmetic.** The plan
filed "tokens shared, not restated" under drift prevention. It is stronger than
that: `canvas2d.js` reads `--flight-surface`, `--flight-hue-*` and the
confidence ramp off the computed style of the canvas it is handed, and falls
back to constants baked into the module when they are absent. A shell restating
its own palette would have drawn the *fallback* colours while looking entirely
fine — one graph, two consoles, different hues, nothing failing. The `@styles`
alias is what makes "one design system" true rather than intended.

## What was built

| Step | Delivered | Where the gate lives |
|---|---|---|
| 1 | `api.ts` + `useApi`, real graph/findings/impact, the generator **deleted** | `test_no_client_fabricates_the_data_it_draws`, `test_the_screen_draws_what_the_api_returned_and_nothing_it_invented` |
| 2 | `App.tsx` reads `/api/shells`; `screens/` keyed by ring 0's own screen key | `test_the_built_shell_only_holds_screens_the_stdlib_one_declines` |
| 3 | `@styles` → ring 0's `tokens.css` and `density.css`; no literal in this shell | `test_the_built_shell_declares_no_colour_of_its_own`, `..._no_raw_size` |
| 4 | `condition` · `route` · `ride` · `narrate` driving the workbench, timeline scrubbing `at()` | `test_both_shells_drive_the_ride_from_the_same_modules`, four checks in the node suite |
| 5 | `token` on the generated client; `Refusal` renders stage and obligation | `test_a_refusal_is_never_rendered_as_a_fault`, `..._renders_the_way_out_rather_than_a_stack_trace` |
| 6 | Playwright over `clients/web/dist`, twelve checks, wired into `ui.yml` | `tests/test_slpie_clients_browser.py` |
| 7 | The README's status table parsed and held against the tree | `test_the_readme_status_matches_the_tree` |

**The one thing deliberately not done:** the road surface, the hop bars and the
solids beside it are not drawn in three dimensions. The ride moves the camera
along the real route and the rail counts the real hops, but §32 step 6's
geometry belongs in ring 0's renderer, where both shells would get it — adding
it here would put the scene in the one place the two shells do not share.
