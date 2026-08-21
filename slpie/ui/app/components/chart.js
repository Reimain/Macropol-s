/* Charts, as inline SVG and plain elements.
 *
 * ── The one encoding decision everything here rests on ───────────────────
 *
 * **Confidence is not goodness, so confidence is not a traffic light.**
 *
 * An edge the platform learned from a name heuristic scores 0.25. Painted red
 * beside a green lockfile pin, that reads as "something is wrong here" — and
 * nothing is wrong. It is a dependency the platform is *less sure of*, which is
 * a statement about the evidence, not about the architecture. A reader who
 * chases every amber edge is chasing the platform's own uncertainty.
 *
 * So the two scales are kept apart, which is also what stops a status colour
 * from ever impersonating a series:
 *
 *   certainty  →  an **ordinal blue ramp**, dark = known, pale = inferred, and
 *                 a neutral off-ramp for `unknown`, because "nothing was read
 *                 here" is not the low end of a scale — it is off it.
 *   severity   →  the reserved **status palette**, and only ever for a finding.
 *
 * The ramp is validated rather than eyeballed:
 *
 *     node scripts/validate_palette.js "#0b4f9e,#1f6fc4,#5192d6,#86b0e2" \
 *       --ordinal --mode light --surface "#ffffff"
 *     → ALL CHECKS PASS  (monotone L, adjacent ΔL ≥ 0.06, light end 2.25:1,
 *                          hue spread 5°)
 *
 * The same run against a green/amber/red certainty scale failed at deutan
 * ΔE 1.4 — amber and red are one colour to a red-green colourblind reviewer —
 * which is the measured reason this file does not use one.
 *
 * ── Rules held throughout ────────────────────────────────────────────────
 *
 *   · Colour is never the only channel: every segment and every legend entry
 *     carries a count and a word.
 *   · A 2px surface gap between adjacent fills, so stacked segments read as
 *     separate quantities rather than as one blurred bar.
 *   · Nominal categories get **one** hue, never a value-ramp — colouring
 *     nine node kinds nine colours burns the only free channel on information
 *     the bar length already carries.
 *   · Grid and axes are recessive; text wears text tokens, never a series
 *     colour.
 *   · No dependency, no build step. This is `document.createElementNS` and the
 *     tokens from `styles/`.
 */

import { h, svg } from "../core/dom.js";
import { count as fmt } from "../core/format.js";

/** The certainty ramp, in ladder order. `unknown` is deliberately off-ramp. */
export const CERTAINTY_FILL = {
  surveyed: "var(--ramp-4)",
  recorded: "var(--ramp-3)",
  inferred: "var(--ramp-2)",
  guessed: "var(--ramp-1)",
  unknown: "var(--ramp-none)",
};

/**
 * The number a screen leads with.
 *
 * A hero figure rather than a one-bar chart: a single current value has no
 * magnitude to compare against, so a bar would be an axis and a rectangle
 * carrying one number that the number already carries.
 */
export function hero(label, value, { unit = "", note = "" } = {}) {
  return h("div", { class: "hero" },
    h("div", { class: "hero-label" }, label),
    h("div", { class: "hero-value" }, String(value),
      unit ? h("span", { class: "hero-unit" }, unit) : null),
    note ? h("div", { class: "hero-note" }, note) : null);
}

/** A stat tile: one number, its label, and an optional qualifier. */
export function stat(label, value, { note = "", tone = "" } = {}) {
  return h("div", { class: "stat" },
    h("div", { class: "stat-label" }, label),
    h("div", { class: `stat-value ${tone}`.trim() }, String(value)),
    note ? h("div", { class: "stat-note" }, note) : null);
}

/**
 * An ordered-scale share, as one horizontal stacked bar.
 *
 * `parts` is `[[name, count, fill]]` in scale order. The reader's job is
 * part-to-whole across an ordered scale, which is a stacked bar — not a pie,
 * whose angles cannot be compared, and not five separate bars, which would hide
 * that they sum to something.
 */
export function share(parts, { total = 0 } = {}) {
  const sum = total || parts.reduce((acc, [, n]) => acc + Number(n || 0), 0);
  if (!sum) return h("p", { class: "empty" }, "Nothing measured yet.");

  const shown = parts.filter(([, n]) => Number(n) > 0);
  return h("div", { class: "share" },
    h("div", { class: "share-track" },
      shown.map(([name, n, fill]) => h("div", {
        class: "share-part",
        style: { width: `${(Number(n) / sum) * 100}%`, background: fill },
        title: `${name}: ${fmt(n)} of ${fmt(sum)}`,
      }))),
    h("div", { class: "legend" },
      shown.map(([name, n, fill]) => h("span", {},
        h("i", { style: { background: fill } }),
        h("b", {}, fmt(n)),
        name))));
}

/**
 * Part-to-whole across a handful of categories, as a ring.
 *
 * A ring rather than a pie, and this is the one place a circle earns its keep:
 * the reader's job here is "is this mostly one thing", which a single closed
 * shape answers at a glance, and the hole carries the total so the headline
 * number and the shape are the same object rather than two.
 *
 * **Five slices, then a rest.** Beyond five, angle differences stop being
 * comparable and a sixth colour stops being distinguishable — so the tail is
 * summed into one honest `rest` slice and the legend says how many it holds,
 * instead of a wheel of slivers nobody can read or name.
 *
 * Drawn with `stroke-dasharray` on one circle per slice: no arc path maths, no
 * rounding artefacts where slices meet, and every slice is a real element with
 * its own title, so hovering names it.
 */
export function donut(parts, { total = 0, unit = "" } = {}) {
  const sum = total || parts.reduce((acc, [, n]) => acc + Number(n || 0), 0);
  if (!sum) return h("p", { class: "empty" }, "Nothing measured yet.");

  const ordered = [...parts]
    .map(([name, n]) => [String(name), Number(n) || 0])
    .filter(([, n]) => n > 0)
    .sort((left, right) => right[1] - left[1]);
  const head = ordered.slice(0, 5);
  const tail = ordered.slice(5);
  const shown = tail.length
    ? [...head, [`rest (${tail.length})`, tail.reduce((acc, [, n]) => acc + n, 0)]]
    : head;

  // A circle of radius 15.9155 has a circumference of 100, so a dash length is
  // a percentage directly and no slice needs a conversion nobody can check.
  const RADIUS = 15.9155;
  const HUES = ["var(--ramp-4)", "var(--ramp-3)", "var(--ramp-2)",
                "var(--ramp-1)", "var(--ramp-none)", "var(--line-strong)"];

  let offset = 25;                       // 12 o'clock rather than 3 o'clock
  const rings = shown.map(([name, n], index) => {
    const slice = (n / sum) * 100;
    const ring = svg("circle", {
      class: "donut-slice", cx: 21, cy: 21, r: RADIUS, fill: "none",
      stroke: HUES[index % HUES.length], "stroke-width": 5.6,
      "stroke-dasharray": `${slice} ${100 - slice}`,
      "stroke-dashoffset": String(offset),
    }, svg("title", {}, `${name}: ${fmt(n)} of ${fmt(sum)}`));
    offset -= slice;
    return ring;
  });

  return h("div", { class: "donut" },
    svg("svg", {
      viewBox: "0 0 42 42", class: "donut-ring", role: "img",
      "aria-label": `${fmt(sum)} ${unit || "total"} across ${shown.length} groups`,
    },
    svg("circle", {
      class: "donut-hole", cx: 21, cy: 21, r: RADIUS, fill: "none",
      stroke: "var(--sunk)", "stroke-width": 5.6,
    }),
    rings),
    h("div", { class: "donut-centre" },
      h("b", {}, fmt(sum)), unit ? h("span", {}, unit) : null),
    h("div", { class: "legend" },
      shown.map(([name, n], index) => h("span", {},
        h("i", { style: { background: HUES[index % HUES.length] } }),
        h("b", {}, fmt(n)),
        name))));
}

/**
 * Magnitude across nominal categories, as horizontal bars.
 *
 * Horizontal because the categories are long-named words rather than dates;
 * a vertical column chart would either truncate them or rotate them 45°, and
 * a rotated label is one nobody reads.
 *
 * **One hue for every bar.** The categories here — node kinds, verb groups —
 * have no natural order, so shading them by value would double-encode length
 * as colour and spend the only free channel restating what the bar says.
 */
export function bars(rows, { max = 0, href = null } = {}) {
  const top = max || Math.max(1, ...rows.map(([, n]) => Number(n || 0)));
  return h("div", { class: "bars" },
    rows.map(([name, n]) => {
      const label = href
        ? h("a", { href: href(name), class: "bar-name" }, name)
        : h("span", { class: "bar-name" }, name);
      return h("div", { class: "bar-row", title: `${name}: ${fmt(n)}` },
        label,
        h("div", { class: "bar-track" },
          h("div", {
            class: "bar-fill",
            style: { width: `${(Number(n) / top) * 100}%` },
          })),
        h("span", { class: "bar-value" }, fmt(n)));
    }));
}

/**
 * A sparkline. No axes, no grid — it is a shape, read for its direction.
 *
 * `values` are plotted in order. Returns null under three points, because two
 * points is a line segment and a line segment is not a trend.
 */
export function spark(values, { width = 120, height = 28 } = {}) {
  const series = (values || []).map(Number).filter((v) => !Number.isNaN(v));
  if (series.length < 3) return null;

  const low = Math.min(...series);
  const high = Math.max(...series);
  const span = high - low || 1;
  const step = width / (series.length - 1);
  const points = series.map((value, index) => {
    const x = index * step;
    const y = height - ((value - low) / span) * (height - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return svg("svg", {
    class: "spark", width, height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `trend from ${series[0]} to ${series[series.length - 1]}`,
  },
  svg("polyline", {
    points: points.join(" "),
    fill: "none",
    stroke: "var(--accent)",
    "stroke-width": "2",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  }));
}
