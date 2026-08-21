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
