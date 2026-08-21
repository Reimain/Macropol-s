/* The dependency graph, drawn.
 *
 * ── Why this screen is the product ───────────────────────────────────────
 *
 * Every competitor draws a topology. AppDynamics has its flow map; every
 * lineage tool has its boxes and arrows. In all of them **every edge is equally
 * true** — the picture asserts that the system knows how these things connect,
 * and says nothing about how it found out.
 *
 * This platform's entire claim is that it does know the difference. An edge from
 * a lockfile pin is a fact; an edge from a name heuristic is a guess capped at
 * 0.60; and a region nothing scanned is neither. So the drawing encodes it: the
 * **stroke** of every edge says how it was learned. Solid means read directly,
 * dashed means joined by inference, dotted means guessed.
 *
 * That is a picture no rival can currently draw, and it is the one thing worth
 * putting on a screen behind a person presenting this.
 *
 * ── Layout is deterministic, and that is a product property ──────────────
 *
 * No force simulation. A physics layout settles somewhere different on every
 * run, so the same graph is a different picture each time you open it — you
 * cannot point at it in a review, cannot compare two screenshots, and cannot
 * tell "the architecture changed" from "the simulation landed elsewhere".
 *
 * Instead: nodes are grouped into columns by kind, ordered within a column by
 * degree, and placed on a fixed grid. Same graph in, same picture out — which is
 * the same property the snapshot digest gives the data, applied to the drawing
 * of it. It is also the only layout that costs nothing to compute and needs no
 * library, which is what keeps invariant 4 intact.
 *
 * Edges route as quadratic curves so that parallel runs between the same two
 * columns stay separable, and every curve bows the same way, so direction is
 * readable without following an arrowhead.
 */

import { h, svg } from "../core/dom.js";
import { certainty } from "../core/format.js";
import { count as fmt, short } from "../core/format.js";

/* The stroke per certainty band. Dash patterns rather than only colour: this
 * survives a colour-blind reader, a greyscale print, and the screenshot pasted
 * into a ticket, which is where most of these end up. Borrowed from contour
 * drawing, where a dashed line has meant "interpolated" for two centuries. */
const STROKE = {
  surveyed: { dash: "", width: 1.6, fill: "var(--ramp-4)" },
  recorded: { dash: "", width: 1.2, fill: "var(--ramp-3)" },
  inferred: { dash: "5 3", width: 1.2, fill: "var(--ramp-2)" },
  guessed: { dash: "2 3", width: 1.2, fill: "var(--ramp-1)" },
  unknown: { dash: "1 4", width: 1, fill: "var(--ramp-none)" },
};

const COLUMN_W = 172;
const ROW_H = 30;
const PAD_X = 26;
const PAD_Y = 46;
const DOT = 5;

/* How tall a column may get before a kind wraps into a second column of its own.
 *
 * Kind populations are power-law: in a real estate one kind holds most of the
 * nodes and a dozen hold one each. A strict column-per-kind therefore drew a
 * seventeen-deep spike beside twelve stubs, leaving three-quarters of the canvas
 * empty and the tall column's labels colliding with every edge crossing it.
 * Wrapping keeps the drawing roughly rectangular whatever the distribution,
 * which is the only thing that makes it legible on a projector. */
const MAX_ROWS = 11;

/** Columns of nodes, grouped by kind, wrapped, and ordered by how connected
 *  they are.
 *
 *  Degree ordering puts the busy nodes at the top of each column, which is where
 *  the eye lands, and keeps the long edges short. Ties break on name and then on
 *  id so the order is *total* — without the id the many nodes that share a name
 *  (`index`, `orders`) would sort arbitrarily and the "same graph, same picture"
 *  guarantee would quietly not hold. */
function layout(nodes, edges) {
  const degree = new Map();
  for (const edge of edges) {
    degree.set(edge.src, (degree.get(edge.src) || 0) + 1);
    degree.set(edge.dst, (degree.get(edge.dst) || 0) + 1);
  }

  const kinds = new Map();
  for (const node of nodes) {
    if (!kinds.has(node.kind)) kinds.set(node.kind, []);
    kinds.get(node.kind).push(node);
  }

  const byPopulation = [...kinds.entries()].sort(
    (left, right) => right[1].length - left[1].length || left[0].localeCompare(right[0]),
  );

  // Each kind becomes one or more columns; only the first carries the heading,
  // so a wrapped kind still reads as one group rather than as two kinds.
  const columns = [];
  for (const [kind, members] of byPopulation) {
    const ordered = members.sort((left, right) =>
      (degree.get(right.id) || 0) - (degree.get(left.id) || 0)
      || String(left.name).localeCompare(String(right.name))
      || String(left.id).localeCompare(String(right.id)));

    for (let start = 0; start < ordered.length; start += MAX_ROWS) {
      columns.push({
        kind,
        total: ordered.length,
        head: start === 0,
        members: ordered.slice(start, start + MAX_ROWS),
      });
    }
  }

  const placed = new Map();
  columns.forEach((column, index) => {
    column.members.forEach((node, row) => {
      placed.set(node.id, {
        node,
        x: PAD_X + index * COLUMN_W,
        y: PAD_Y + row * ROW_H,
        degree: degree.get(node.id) || 0,
      });
    });
  });

  const tallest = Math.max(1, ...columns.map((column) => column.members.length));
  return {
    placed,
    columns,
    width: PAD_X * 2 + Math.max(1, columns.length) * COLUMN_W,
    height: PAD_Y + tallest * ROW_H + PAD_Y,
  };
}

/**
 * Draw it.
 *
 * `onPick(id)` fires when a node is chosen, by click or by keyboard — the nodes
 * are focusable, because a diagram that can only be operated with a mouse is a
 * diagram half the reviewers cannot operate at all.
 */
export function diagram(nodes, edges, { onPick = () => {}, selected = "" } = {}) {
  if (!nodes || !nodes.length) {
    return h("p", { class: "empty" }, "No nodes yet — read a folder first.");
  }

  const { placed, columns, width, height } = layout(nodes, edges || []);

  // Everything touching the selection, so picking a node dims the rest rather
  // than hiding it: the shape of what is *not* connected is information too.
  const near = new Set();
  if (selected) {
    near.add(selected);
    for (const edge of edges || []) {
      if (edge.src === selected) near.add(edge.dst);
      if (edge.dst === selected) near.add(edge.src);
    }
  }

  const wires = (edges || []).map((edge) => {
    const from = placed.get(edge.src);
    const to = placed.get(edge.dst);
    if (!from || !to) return null;   // an edge to a node outside this page

    const band = certainty(edge.confidence);
    const style = STROKE[band] || STROKE.unknown;
    const midX = (from.x + to.x) / 2;
    const bow = Math.min(38, Math.abs(to.y - from.y) / 2 + 12);

    return svg("path", {
      class: "wire",
      d: `M ${from.x} ${from.y} Q ${midX} ${(from.y + to.y) / 2 - bow} ${to.x} ${to.y}`,
      fill: "none",
      stroke: style.fill,
      "stroke-width": style.width,
      "stroke-dasharray": style.dash,
      "stroke-linecap": "round",
      opacity: selected && !(near.has(edge.src) && near.has(edge.dst)) ? 0.12 : 0.75,
    }, svg("title", {},
      `${edge.kind}: ${band} (${Number(edge.confidence).toFixed(2)})`));
  });

  const dots = [...placed.values()].map(({ node, x, y, degree }) => {
    const band = certainty(node.confidence);
    const dim = selected && !near.has(node.id);
    return svg("g", {
      class: `node${node.id === selected ? " picked" : ""}`,
      transform: `translate(${x} ${y})`,
      opacity: dim ? 0.25 : 1,
      tabindex: "0",
      role: "button",
      "aria-label": `${node.kind} ${node.name}, ${band}`,
      onclick: () => onPick(node.id),
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onPick(node.id);
        }
      },
    },
    // Size carries degree — how much depends on this — which is the question a
    // reader actually brings to a topology. Square-rooted, because area is what
    // the eye compares and a linear radius would exaggerate a hub fourfold.
    svg("circle", {
      r: DOT + Math.sqrt(degree) * 1.6,
      fill: STROKE[band].fill,
      stroke: "var(--panel)",
      "stroke-width": "2",
    }),
    svg("text", { x: DOT + 8, y: 4, class: "node-label" },
      short(node.name || node.identity, 16)),
    // A generous, invisible hit target, last so it sits above the wires.
    //
    // Without it a click near a node lands on an *edge stroke* instead: SVG hit
    // testing follows the painted stroke, and every edge terminates exactly at
    // the node it connects. The dot is 7–13px across and the thing a reader is
    // aiming at, so the target is sized for a finger rather than for the mark.
    svg("circle", { class: "hit", r: DOT + 11, fill: "transparent" }),
    svg("title", {},
      `${node.name}\n${node.kind} · ${band} (${Number(node.confidence).toFixed(2)})`
      + `\n${degree} connection${degree === 1 ? "" : "s"}`));
  });

  // Only the first column of a wrapped kind is labelled: repeating the heading
  // over the continuation would read as two kinds of the same name.
  const heads = columns.map((column, index) => (column.head
    ? svg("text", {
      x: PAD_X + index * COLUMN_W - DOT,
      y: PAD_Y - 22,
      class: "column-label",
    }, `${column.kind}  ${column.total}`)
    : null));

  return h("div", { class: "diagram scroll" },
    svg("svg", {
      width, height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label":
        `${nodes.length} nodes and ${(edges || []).length} edges, grouped by kind`,
    },
    svg("g", { class: "wires" }, wires),
    svg("g", { class: "heads" }, heads),
    svg("g", { class: "dots" }, dots)));
}

/** The key. Shown once beside the drawing, never repeated per edge. */
export function key() {
  const sample = (band, meaning) => {
    const style = STROKE[band];
    return h("span", { class: "wire-key" },
      svg("svg", { width: 34, height: 10, "aria-hidden": "true" },
        svg("line", {
          x1: 1, y1: 5, x2: 33, y2: 5,
          stroke: style.fill,
          "stroke-width": style.width + 0.4,
          "stroke-dasharray": style.dash,
          "stroke-linecap": "round",
        })),
      h("b", {}, band),
      meaning);
  };

  return h("div", { class: "legend wire-legend" },
    sample("surveyed", "a lockfile pin or a runtime trace"),
    sample("recorded", "a manifest or a static import"),
    sample("inferred", "joined from configuration"),
    sample("guessed", "reflection or a name"),
    h("span", { class: "muted" },
      "Thickness and size carry how much depends on a node."));
}

/** How the edges divide across the ladder — the number nobody else reports. */
export function spread(edges) {
  const bands = { surveyed: 0, recorded: 0, inferred: 0, guessed: 0, unknown: 0 };
  for (const edge of edges || []) bands[certainty(edge.confidence)] += 1;
  return bands;
}

/** The share of relationships the platform read directly rather than inferred.
 *
 *  This is the board-level number: not "how many dependencies do you have",
 *  which every tool answers, but "how much of what this thing is telling you did
 *  it actually see". */
export function directness(edges) {
  const total = (edges || []).length;
  if (!total) return { share: 0, read: 0, total: 0 };
  const bands = spread(edges);
  const read = bands.surveyed + bands.recorded;
  return { share: read / total, read, total };
}

export { fmt };
