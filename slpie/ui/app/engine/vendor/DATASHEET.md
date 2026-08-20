# Vendored rendering engines

One engine is vendored. Everything about it is declared here and in
`tools/vendored.json`, and the declaration is checkable:
`python -m tools.vendor --check` verifies the committed bytes against their
recorded digests with no network, which is what CI runs.

## The boundary, stated

Invariant 4 does not gain an exception here. It gains an edge:

- the air-gapped console is precisely the one running `../canvas2d.js`, and that
  claim stays literally true because of one test — **the app boots, renders and
  passes the whole browser tier with this directory deleted**;
- every engine declares `native`. The default declares `true`. Anything here
  declares `false`, and the console reports it as *not air-gapped native*;
- nothing here is precached by the service worker. The offline shell is the
  native path, and precaching a vendored engine would make the offline console
  depend on something that is not in this repository;
- **no module outside this directory statically imports into it.**
  `../contract.js` reaches for an engine by name at runtime and falls back to
  `canvas2d` with a stated reason, because a static import of an absent module
  takes the whole screen down. A wrapper importing the library it wraps, or
  reaching up to the shared scene modules, is fine — both disappear together
  when the directory does;
- an engine may **decline the machine it landed on**. `three.js` answers a
  reason from `available()` when WebGL is absent, and `resolve()` falls back and
  says so. A missing capability is reported, never a black canvas.

## three — 0.185.1

| Field | |
|---|---|
| upstream URL | https://registry.npmjs.org/three/-/three-0.185.1.tgz |
| homepage | https://threejs.org |
| version | 0.185.1 |
| licence | MIT — `three.LICENSE.md`, verbatim |
| files | `three.module.min.js` (365,552 B), `three.core.min.js` (385,386 B) |
| sha256 | recorded per file in `tools/vendored.json` |

### What was removed

The concept this serves arrived as a Vite + React + react-three-fiber
application. **React and react-three-fiber were dropped on the way in**, and
that is a decision rather than an omission: r3f is a React binding whose value
is declarative scene composition inside a component tree, and this console has
no component tree. With no tree it is three peer dependencies and a build step
bought for nothing. Everything substantive in that bundle — instanced meshes, a
swept path tube, depth fog, a camera rig that blends between two roles — is
plain Three, and plain Three is two ESM files that need no bundler.

Also not taken: `three.webgpu.*`, `three.tsl.*`, the CJS build, the examples
tree, and the unminified builds. Two files, 751KB, and nothing that is not
reached.

### Why it is here — measured, and the measurement says something specific

`test_the_vendored_engine_draws_the_same_marks_as_the_native_one` runs both
engines over one scene and reports the frame rate. Headless Chromium with
SwiftShader, no GPU:

| scene | marks after aggregation | canvas2d | three |
|---|---|---|---|
| 900 nodes, 1,400 edges | 515 | 142 fps | 229 fps |
| 6,000 nodes, 9,000 edges | 106 | 60 fps | 68 fps |

**The honest reading: frame rate does not justify this.** Canvas 2D holds 60fps
at six thousand nodes, and the two engines converge as density rises because
the shared half — projection and aggregation — is what actually costs, and both
run the identical code. A dependency taken on these numbers alone would be a
dependency taken on nothing.

What does justify it is what Canvas 2D **cannot express**:

- marks as **solids** rather than filled outlines, so the flight view has
  something to fly past rather than a field of discs;
- depth as **real fog** the hardware applies, rather than an sRGB blend
  computed per mark;
- the route as a **swept tube** along the traversal. A path drawn as a surface
  is somewhere you are; a path drawn as a line is a diagram of somewhere else,
  and that difference is the whole point of the ride.

So it ships as an **option a reader selects**, not as a necessity. That is the
distinction the seam exists to keep, and the deleted-directory test is what
keeps it.

### The rule it is held to

`three.js` — the wrapper — runs the **identical** `aggregate()` over the
identical projection as the native renderer, and a test asserts the two tallies
match on marks, represented, severe, edges and tiers. WebGL could happily push
twenty thousand instances, and drawing them would make this engine show a
*different picture of the same query*, which is a worse failure than being slow.
Different surfaces must not be different answers (§24, acceptance 7), and that
holds for renderers.

## What an entry here must record

One section per engine, before it is committed:

| Field | Why |
|---|---|
| upstream URL | where it came from, so the claim is checkable |
| version | the exact release, not a range |
| licence | checked before the bytes land, as `corpus/DATASHEET.md` does |
| sha256 | of the file as committed, in `tools/vendored.json` |
| why it is here | the measurement, and what it actually said |
| what was removed | wrappers ship minimal; say what was cut |
