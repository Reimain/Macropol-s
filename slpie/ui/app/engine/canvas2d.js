/* The native renderer. Canvas 2D, no dependency, nothing outside this repository.
 *
 * This is the engine that makes "air-gapped" literally true rather than
 * nominally true: the console boots, renders and passes its whole browser tier
 * with `engine/vendor/` deleted, because this is what draws when nothing else
 * is present.
 *
 * Canvas rather than SVG because density is the point. SVG's DOM cost becomes
 * the bottleneck somewhere around two thousand nodes — one element per mark,
 * each with style resolution and layout — and the estates this is for are
 * larger than that. `ui/graph.js` stays SVG and stays right for what it draws:
 * a few dozen nodes that must be focusable, selectable and readable by a screen
 * reader. Two renderers, two jobs, no competition between them.
 *
 * ── One measured lesson, encoded ─────────────────────────────────────────
 *
 * The prototype that preceded this called `getComputedStyle` inside the
 * per-node draw loop: twenty thousand style resolutions per frame. It measured
 * 16fps and very nearly produced the conclusion "we need a 3D engine". Hoisting
 * the palette to a resolve-once table took the same scene to 60. **That
 * conclusion would have been a 600KB dependency taken on the strength of a
 * bug**, which is the whole reason `contract.js` refuses to vendor anything
 * before there is a number that says it is needed.
 *
 * So: every colour this module uses is resolved once, at mount. Nothing inside
 * `draw()` reads the DOM.
 *
 * ── Depth is contrast, never hue ─────────────────────────────────────────
 *
 * Distant marks fade toward the surface colour. That is a value change, so it
 * composes with the confidence ramp and the severity palette instead of
 * competing with them. Hue in this product means something; a renderer that
 * spends it on distance makes the two channels that carry meaning unreadable.
 */

import { haze, project } from "./camera.js";

/** Marks smaller than this are not worth a stroke; they are drawn as a dot. */
const FINE = 2.4;

/** How far into the ground the furthest marks fade. 1 would erase them. */
const DEEPEST = 0.82;

function tokens(canvas) {
  const style = getComputedStyle(canvas);
  const read = (name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;
  return {
    surface: read("--flight-surface", read("--bg", "#0d1117")),
    ink: read("--flight-ink", read("--text", "#e6edf3")),
    line: read("--line", "#30363d"),
  };
}

/* Blend toward the surface in sRGB. Approximate, and deliberately so: a correct
 * perceptual blend costs a colour-space conversion per mark per frame, and the
 * thing being expressed is "further away", which does not need to be accurate to
 * a delta-E. */
function fade(colour, surface, share) {
  const from = rgb(colour);
  const to = rgb(surface);
  if (!from || !to) return colour;
  const mix = (index) => Math.round(from[index] + (to[index] - from[index]) * share);
  return `rgb(${mix(0)},${mix(1)},${mix(2)})`;
}

function rgb(colour) {
  const hex = String(colour).trim();
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex);
  if (short) return [1, 2, 3].map((index) => parseInt(short[index].repeat(2), 16));
  const long = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (long) return [1, 2, 3].map((index) => parseInt(long[index], 16));
  const parts = /^rgba?\(([^)]+)\)$/i.exec(hex);
  if (parts) return parts[1].split(",").slice(0, 3).map((part) => parseInt(part, 10));
  return null;
}

export const canvas2d = {
  name: "canvas2d",
  native: true,

  mount(canvas, scene) {
    this.canvas = canvas;
    this.scene = scene;
    this.context = canvas.getContext("2d");
    this.palette = tokens(canvas);
    // The renderer's own tally of what it actually put on the surface. Every
    // number the console reports about this scene comes from here rather than
    // from a formula over the node count — a figure nobody computed is not
    // telemetry, it is decoration.
    this.drawn = { marks: 0, edges: 0, clipped: 0 };
    return this;
  },

  draw(camera) {
    const { context, palette } = this;
    if (!context) return this.drawn;

    const ratio = this.canvas.ownerDocument.defaultView.devicePixelRatio || 1;
    const width = camera.width;
    const height = camera.height;
    if (this.canvas.width !== Math.round(width * ratio)) {
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = palette.surface;
    context.fillRect(0, 0, width, height);

    const tally = { marks: 0, edges: 0, clipped: 0 };
    const screen = new Map();
    let nearest = Infinity;
    let furthest = 0;

    for (const point of this.scene.placed.values()) {
      const at = project(point, camera);
      if (!at.visible) {
        tally.clipped += 1;
        continue;
      }
      screen.set(point.id, { point, at });
      if (at.depth < nearest) nearest = at.depth;
      if (at.depth > furthest) furthest = at.depth;
    }

    const span = { near: nearest, far: Math.max(furthest, nearest + 1) };

    // Edges first and unsorted: they sit behind every mark, and sorting them
    // buys nothing a reader can see while costing a sort of the larger set.
    context.lineWidth = 1;
    for (const edge of this.scene.edges || []) {
      const from = screen.get(edge.src);
      const to = screen.get(edge.dst);
      if (!from || !to) continue;
      const share = haze((from.at.depth + to.at.depth) / 2, span) * DEEPEST;
      context.strokeStyle = fade(palette.line, palette.surface, share);
      context.beginPath();
      context.moveTo(from.at.x, from.at.y);
      context.lineTo(to.at.x, to.at.y);
      context.stroke();
      tally.edges += 1;
    }

    // Painters' order, far to near, so a near mark covers a far one rather than
    // whichever happened to be iterated last.
    const ordered = [...screen.values()].sort((left, right) => right.at.depth - left.at.depth);
    for (const item of ordered) {
      const share = haze(item.at.depth, span) * DEEPEST;
      const radius = Math.max(1, item.at.scale * 2.2);
      context.fillStyle = fade(palette.ink, palette.surface, share);
      context.beginPath();
      context.arc(item.at.x, item.at.y, Math.min(radius, FINE * 4), 0, Math.PI * 2);
      context.fill();
      tally.marks += 1;
    }

    this.drawn = tally;
    return tally;
  },

  dispose() {
    this.context = null;
    this.scene = null;
    this.canvas = null;
    return this.drawn;
  },
};
