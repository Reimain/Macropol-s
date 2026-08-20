# Vendored rendering engines

Nothing is vendored. That is the current state, not an oversight, and this file
exists so the state is declared rather than inferred from an empty directory.

## Why the directory ships empty

The tier shipped with the protocol (`../contract.js`) and the native renderer
(`../canvas2d.js`) first, so the seam is proven by its own use rather than
asserted — the same argument invariant 6 makes about plugins, applied to
rendering.

A third-party engine drops in afterwards, as a plugin, **for a measured reason**
— a frame rate the native path could not reach on a real scene — rather than
speculatively. Vendoring 600KB before knowing whether it is needed is how a
dependency arrives without anyone having decided to take it.

## The boundary, stated

Invariant 4 does not gain an exception here. It gains an edge:

- the air-gapped console is precisely the one running `canvas2d`, and that claim
  stays literally true because of one test — **the app boots, renders and passes
  the whole browser tier with this directory deleted**;
- every engine declares `native`. The default declares `true`. Anything landing
  here declares `false`, and the console reports it as *not air-gapped native*;
- nothing here is precached by the service worker. The offline shell is the
  native path, and precaching a vendored engine would make the offline console
  depend on something that is not in this repository;
- no module imports from this directory statically. `contract.js` reaches for an
  engine by name at runtime and falls back to `canvas2d` with a stated reason,
  because a static import of an absent module takes the whole screen down.

## What an entry here must record

One section per engine, before it is committed:

| Field | Why |
|---|---|
| upstream URL | where it came from, so the claim is checkable |
| version | the exact release, not a range |
| licence | checked before the bytes land, as `corpus/DATASHEET.md` does |
| sha256 | of the file as committed |
| why it is here | the measurement that made the native path insufficient |
| what was removed | wrappers ship minimal; say what was cut |
