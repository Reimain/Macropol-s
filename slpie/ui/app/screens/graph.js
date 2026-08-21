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
import { card, panel } from "../components/panel.js";
import { bars, hero, stat } from "../components/chart.js";
import { CERTAINTY_FILL, share } from "../components/chart.js";
import { diagram, directness, key, spread } from "../components/graph.js";
import { CHOOSING, MEANS, machine } from "../engine/condition.js";
import { rail } from "../engine/route.js";
import { readout, upto } from "../engine/narrate.js";

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

/* --- the flight mode: choose, aim, ride ---------------------------------- */

/* The condition model owns what the screen is doing. It lives here rather than
 * in a loose variable because the *interaction* is the thing three prototypes
 * got wrong by tuning a scene first — and a machine can be asserted where "it
 * feels right" cannot. */
const flight = machine();
let route = null;

/**
 * `CHOOSING` draws no scene at all.
 *
 * This is the load-bearing rule of §32 and it is enforced here rather than
 * described: the chooser is a list of what is available and what is worth
 * looking at, and nothing spatial is rendered until a selection has earned it.
 * A graph before a question is a picture of an estate nobody asked about.
 */
function chooser(value) {
  const nodes = (value.nodes || []).slice(0, 24);
  return card("Choose what to look at",
    h("p", { class: "prose muted" },
      "Nothing is drawn yet, and that is deliberate. A field of scattered "
      + "points says only that there is a lot of data. Pick something and the "
      + "view becomes the answer to that question."),
    h("p", { class: "muted mono" }, MEANS[CHOOSING]),
    nodes.length
      ? h("ul", { class: "choices" }, nodes.map((node) => h("li", {},
        h("button", {
          type: "button",
          class: "chip",
          onclick: () => {
            picked = node.id;
            flight.send("select");
            redraw();
          },
        }, node.name || node.id))))
      : h("p", { class: "empty" }, "Nothing to choose from yet."));
}

/* --- the spatial view, drawn through the renderer seam ------------------- */

//: The engine currently drawing, and whether it needed anything outside this
//: repository. Held so the caption can say which — a console that does not
//: report what is rendering it cannot honestly claim to be air-gapped.
let drawing = null;

/**
 * Mount a renderer into a canvas and draw one frame.
 *
 * Asynchronous because resolving an engine may be a dynamic import, and the
 * canvas has to be in the document before a context exists — so the caption is
 * filled in when the engine answers rather than guessed at beforehand.
 *
 * A missing engine falls back to the native one **with its reason shown**,
 * which is the treatment §27 gives a missing binary: the answer still arrives
 * and says what it cost. Deleting `engine/vendor/` leaves this working.
 */
async function paint(canvas, caption, value, wanted) {
  const [contract, layout, palette, camera] = await Promise.all([
    import("../engine/contract.js"),
    import("../engine/layout.js"),
    import("../engine/palette.js"),
    import("../engine/camera.js"),
  ]);

  const nodes = value.nodes || [];
  const edges = value.edges || [];
  if (!nodes.length) return;

  const scene = layout.place(nodes, edges, {
    regionOf: (node) => node.boundary || "",
  });
  // Severity rides on the point, because it is the one channel the renderer
  // reserves saturation for — a finding is the only vivid thing on the surface.
  for (const node of nodes) {
    const point = scene.placed.get(node.id);
    if (point) point.severity = node.severity || "";
  }
  scene.colouring = palette.colour(
    scene.regions.map((region) => region.name),
    layout.adjacency(scene.placed, edges),
  );

  const chosen = await contract.resolve(wanted || contract.DEFAULT);
  const engine = Object.create(chosen.engine);

  // Framed to the box it is displayed in, not to the element's intrinsic size.
  // The renderer owns the backing store — it multiplies by the device ratio
  // itself — so setting `canvas.width` here as well multiplied the two
  // together and drew a scene four times the size of the surface.
  const box = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.round(box.width));
  const height = Math.max(200, Math.round(box.height));

  // A three-quarter view rather than the head-on default. The layout puts kind
  // in Z and degree in X, so looking straight down the Z axis stacks every lane
  // on top of the one behind it — the separation the layout encodes is exactly
  // what a yaw of zero throws away.
  //
  // Framed here rather than through `camera.frame`, which fits the *largest*
  // axis with a 1.4 margin — right for a cube-shaped scene and wrong for this
  // one, which is long in Z and shallow in Y, so fitting Z left the estate
  // occupying a quarter of the surface. The eye is placed to fit the diagonal
  // the reader actually sees, which is what fills the frame.
  const PITCH = 0.38;
  const YAW = -0.62;
  const extent = scene.extent;
  // Aimed at where the marks *are*, not at the middle of the box that contains
  // them. The layout leaves whole regions of the extent empty — kinds with two
  // members occupy a lane as wide as a kind with twenty — so the box's centre
  // is a place with nothing in it, and aiming there pushes the estate into a
  // corner.
  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (const point of scene.placed.values()) {
    sx += point.x; sy += point.y; sz += point.z;
  }
  const many = Math.max(1, scene.placed.size);
  const middle = camera.vector(sx / many, sy / many, sz / many);
  const across = Math.hypot(
    extent.max.x - extent.min.x, extent.max.z - extent.min.z,
  );
  const fov = Math.PI / 3;
  // 0.62 rather than 1.4: the diagonal already accounts for the two axes that
  // matter, and the marks have their own radius allowance in the renderer.
  const back = Math.max(60, (across / 2) / Math.tan(fov / 2) * 0.52);
  const view = camera.look(
    camera.vector(
      middle.x + back * Math.cos(PITCH) * Math.sin(YAW),
      middle.y + back * Math.sin(PITCH),
      middle.z + back * Math.cos(PITCH) * Math.cos(YAW),
    ),
    middle,
    { fov, width, height, near: Math.max(0.1, across / 1000) },
  );

  if (drawing && drawing.dispose) drawing.dispose();
  drawing = engine;
  engine.mount(canvas, scene);
  const tally = engine.draw(view);

  const named = contract.describe(chosen.engine);
  fill(caption,
    h("span", { class: "mono" }, `drawing with ${named.name}`),
    h("span", { class: "mono muted" }, named.label),
    // Counted by the renderer, never modelled: `marks` is what it actually put
    // on the surface, and `represented` is how many elements those marks stand
    // for once the far ones are aggregated.
    h("span", { class: "mono muted" },
      `${tally.marks} mark(s) for ${tally.represented} element(s), `
      + `${tally.edges} link(s)`),
    chosen.fallback
      ? h("span", { class: "mono" }, `— ${chosen.reason}`)
      : null);
}

/**
 * The estate as a surface rather than a diagram.
 *
 * The renderer tier shipped with `test_slpie_ui_engine.py` proving it and no
 * screen reaching it — recorded as §1.9 in `docs/AUDIT.md`. This is the screen
 * reaching it: the same seam, the same two engines, chosen by `?engine=`, with
 * the native one as the default so nothing outside this repository is needed
 * to see it work.
 */
function spatial(value, wanted) {
  const canvas = h("canvas", { class: "flight-canvas", width: 960, height: 560 });
  const caption = h("div", { class: "row flight-caption" },
    h("span", { class: "mono muted" }, "resolving a renderer…"));

  // After the node is in the document: a canvas with no context yet cannot be
  // drawn into, and `requestAnimationFrame` is the first moment it has one.
  requestAnimationFrame(() => {
    paint(canvas, caption, value, wanted).catch((error) => {
      fill(caption, h("span", { class: "mono" },
        `no renderer available: ${(error && error.message) || error}`));
    });
  });

  return card("The estate, in three dimensions", canvas, caption);
}

/** What the ride is worth, in the words the panel shows. */
function narration(evidenceFor) {
  if (!route) return null;
  const lines = upto(route, route.length - 1, evidenceFor);
  const numbers = readout(route, route.length - 1);
  return card("The reasoning, in the order it arrives",
    h("p", { class: "muted mono" },
      `${numbers.travelled} hop(s), bounded at ${numbers.floor.toFixed(2)}`
      + (numbers.inferred ? ` — ${numbers.inferred} inferred` : "")),
    h("ol", { class: "narration" }, lines.map((line) => h("li",
      { class: `narrate ${line.role}${line.slowing ? " slowing" : ""}` },
      line.text))));
}

export function mount(outlet, _params = {}, query = {}) {
  // The condition model gates the **flight mode**, not this screen.
  //
  // `#/graph` on its own is the holistic estate view, and §32 keeps that
  // deliberately: "show me everything" stays available, stays one control, and
  // is labelled as what it is — the whole estate, expensive, and rarely the
  // useful thing. It is a state you *choose*, not the state you land in by
  // accident, and gating it behind a chooser would remove a view somebody
  // asked for rather than stopping one nobody did.
  //
  // `#/graph?view=flight` is where `CHOOSING` applies, because that is the
  // view three prototypes opened into as a rendered field of scattered points.
  const flying = query.view === "flight";

  // A fresh visit starts fresh. The machine is module-level so the screen does
  // not keep the condition in a loose variable, and that means an old
  // condition can outlive the visit that produced it — a reader arriving at
  // the chooser and being shown last visit's scene would be the interface
  // remembering something they did not ask it to.
  if (!picked) flight.send("clear");

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

        // Nothing spatial before a selection — in flight mode. The chooser
        // replaces the scene rather than sitting beside an empty one.
        flying && !flight.draws ? chooser(value) : null,

        // In flight, once something is selected, the estate is a *surface*
        // drawn through the renderer seam. Outside flight it stays the SVG
        // diagram, which is the right instrument for a glance and the one that
        // needs no canvas at all.
        flying && flight.draws ? spatial(value, query.engine) : null,

        !flying || flight.draws ? card("The estate",
          key(),
          diagram(value.nodes || [], value.edges || [], {
            selected: picked,
            onPick: (id) => { picked = picked === id ? "" : id; redraw(); },
          })) : null,

        flying ? narration(() => []) : null,

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
  route = null;
  flight.send("clear");
}

/** For the ride, once an `impact` answer is in hand. */
export function aim(payload) {
  route = rail(payload);
  flight.send("aim");
  redraw();
  return route;
}

export const needs = ["*"];
