/* Graph — the screen this platform should be judged on.
 *
 * It leads with the one number no rival reports: **how much of what you are
 * being told was read directly**, rather than joined, inferred or guessed. Every
 * dependency tool on the market answers "how many dependencies do you have".
 * None of them answers "and how much of that do you actually know", because none
 * of them tracks the evidence to answer it.
 *
 * Below that, the graph itself, drawn with the evidence encoded in the stroke.
 * Then the kinds, as bars, so a reader can see the shape of the estate.
 *
 * Selecting a node dims everything it does not touch and offers the two
 * compositions worth running next — its blast radius, and its history. Every
 * screen here is a saved composition, and this one says so.
 */

import { fill, h, link } from "../core/dom.js";
import { cell, subscribe } from "../core/store.js";
import { count as fmt, percent, short } from "../core/format.js";
import { graph as loadGraph } from "../data/queries.js";
import { card, panel } from "../ui/panel.js";
import { bars, hero, stat } from "../ui/chart.js";
import { CERTAINTY_FILL, share } from "../ui/chart.js";
import { diagram, directness, key, spread } from "../ui/graph.js";

const stops = [];
let picked = "";
let redraw = () => {};

/** The ladder, in order, as the stacked share expects it. */
function certaintyParts(bands) {
  return [
    ["surveyed", bands.surveyed, CERTAINTY_FILL.surveyed],
    ["recorded", bands.recorded, CERTAINTY_FILL.recorded],
    ["inferred", bands.inferred, CERTAINTY_FILL.inferred],
    ["guessed", bands.guessed, CERTAINTY_FILL.guessed],
    ["unknown", bands.unknown, CERTAINTY_FILL.unknown],
  ];
}

function headline(value) {
  const edges = value.edges || [];
  const counts = value.counts || {};
  const { share: read, read: direct, total } = directness(edges);

  // The sentence is built from the split rather than written once, because a
  // fixed "…the rest were inferred" is false whenever nothing was — and it
  // rendered exactly that under a 100% headline, which is the kind of caption
  // that costs a reader their trust in every other number on the page.
  const indirect = total - direct;
  const note = !total
    ? "Nothing measured yet."
    : indirect === 0
      ? `Every one of the ${fmt(total)} relationships came from a pin, a `
        + `manifest or a trace. None of this picture is inferred.`
      : `${fmt(direct)} of ${fmt(total)} relationships came from a pin, a `
        + `manifest or a trace; ${fmt(indirect)} were inferred or guessed.`;

  return h("div", { class: "kpi" },
    hero("Read directly", percent(read), { note }),
    stat("Nodes", fmt(counts.nodes ?? (value.nodes || []).length)),
    stat("Relationships", fmt(counts.edges ?? edges.length)),
    stat("Evidence", fmt(counts.evidence ?? 0), {
      note: "file and line, for every claim",
    }),
    counts.retired_nodes
      ? stat("Retired", fmt(counts.retired_nodes), {
        note: "superseded, never deleted",
      })
      : null);
}

/** What the reader can do with the node they just picked. */
function selection(value) {
  const node = (value.nodes || []).find((item) => item.id === picked);
  if (!node) return null;

  const edges = (value.edges || []).filter(
    (edge) => edge.src === picked || edge.dst === picked,
  );

  return card(null,
    h("div", { class: "row" },
      h("h2", {}, short(node.name || node.identity, 40)),
      h("span", { class: "muted" }, node.kind),
      h("span", { class: "spacer" }),
      h("button", {
        type: "button", class: "go quiet",
        onclick: () => { picked = ""; redraw(); },
      }, "Clear")),

    h("div", { class: "facts" },
      h("div", { class: "fact" },
        h("span", { class: "k" }, "identity"),
        h("span", { class: "v" }, short(node.identity, 28))),
      h("div", { class: "fact" },
        h("span", { class: "k" }, "confidence"),
        h("span", { class: "v" }, Number(node.confidence).toFixed(2))),
      h("div", { class: "fact" },
        h("span", { class: "k" }, "validation"),
        h("span", { class: "v" }, node.validation || "unverified")),
      h("div", { class: "fact" },
        h("span", { class: "k" }, "connections"),
        h("span", { class: "v" }, String(edges.length)))),

    // Not buttons that do a hidden thing: each is the composition, named, and
    // the link carries it so it can be copied, shared and run from a shell.
    h("div", { class: "row" },
      link(`#/impact/${encodeURIComponent(node.id)}`, { class: "go" },
        "Blast radius"),
      link(`#/node/${encodeURIComponent(node.id)}`, { class: "go quiet" },
        "Evidence"),
      link(`#/compose?pipeline=${encodeURIComponent(`impact ${node.id}`)}`,
        { class: "go quiet" }, "Open as a pipeline")));
}

export function mount(outlet) {
  redraw = () => {
    const held = cell("graph");

    fill(outlet, h("div", { class: "stack" },
      panel(held, (value) => h("div", { class: "stack" },
        headline(value),

        card("How this was learned",
          h("p", { class: "prose muted" },
            "Every relationship carries the evidence it came from. This is the "
            + "split across the ladder — and the honest answer to how much of "
            + "the picture below is a fact rather than an inference."),
          share(certaintyParts(spread(value.edges || [])),
            { total: (value.edges || []).length })),

        selection(value),

        card("The estate",
          key(),
          diagram(value.nodes || [], value.edges || [], {
            selected: picked,
            onPick: (id) => { picked = picked === id ? "" : id; redraw(); },
          })),

        Object.keys(value.by_kind || {}).length
          ? card("By kind",
            bars(Object.entries(value.by_kind)
              .sort((left, right) => right[1] - left[1])))
          : null),
      {
        sentence: "The graph is empty — read a folder from the console first.",
        rows: 6,
      })));
  };

  stops.push(subscribe("graph", redraw));
  redraw();
  loadGraph(400);
}

export function unmount() {
  while (stops.length) stops.pop()();
  redraw = () => {};
  picked = "";
}

export const needs = ["*"];
