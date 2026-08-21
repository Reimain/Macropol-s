/* The enterprise console's build.
 *
 * ── The one decision in this file ────────────────────────────────────────
 *
 * `@scene` points at `slpie/ui/app/engine/` — the *same files* the stdlib
 * console draws with, not a copy of them. That is what makes two shells one
 * product rather than two products that resemble each other for a release and
 * then drift.
 *
 * The scene modules are plain ESM with no framework anywhere in them —
 * projection, deterministic layout, region colouring, glyph geometry and
 * aggregation — and a test asserts none of them is duplicated under
 * `clients/`. If somebody copies one here to make a quick change, the suite
 * says so, because the copy is the drift.
 *
 * `fs.allow` is what lets the dev server serve from outside this package root.
 * Vite forbids that by default and is right to; the exemption is one directory
 * and it is named.
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const scene = resolve(here, "../../slpie/ui/app/engine");

/* `@styles` is the second half of the same decision. The renderer reads
 * `--flight-surface`, `--flight-hue-*` and the confidence ramp off the computed
 * style of its own canvas, so a shell that did not ship ring 0's tokens would
 * silently draw the *fallback* palette baked into `canvas2d.js` — two consoles
 * showing the same graph in different colours, with nothing failing. Importing
 * the token files is what makes "one design system" true rather than intended,
 * and step 3's test forbids a colour literal here so it stays that way. */
const styles = resolve(here, "../../slpie/ui/app/styles");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@scene": scene, "@styles": styles },
  },
  server: {
    fs: { allow: [here, scene, styles] },
  },
  build: {
    outDir: "dist",
    // Three is 751KB and it is loaded on demand, not in the shell. Warning
    // about it every build teaches people to ignore build warnings.
    chunkSizeWarningLimit: 1200,
  },
});
