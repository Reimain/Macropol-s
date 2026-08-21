/* Dashboard: a screen chosen for the demand, not chosen from a menu.
 *
 * Every other screen here answers a question somebody already knew to ask. This
 * one answers "show me what I need", which is the question a reader who does
 * not yet know the product actually has — and the reason the platform picks the
 * layout rather than offering thirty-six of them and hoping.
 *
 * Three things it does that a dashboard normally does not:
 *
 * * **It says why it chose.** The selection carries which axes matched and
 *   which did not, and below a floor it says the best match was not good
 *   enough — rather than dressing a generic grid up as the right answer.
 * * **It draws from the panel, not from a component list.** Panels arrive as
 *   data — a component key, its rows, its columns — and `components/dictionary`
 *   resolves them. So a template added in Python appears here with no
 *   JavaScript written, which is the §24 projection applied to layout.
 * * **It names the composition it ran.** The pipeline is on screen and links
 *   into Compose, because a dashboard nobody can reproduce is a picture.
 */

import { fill, h, link } from "../core/dom.js";
import { VERBS } from "../data/client.js";
import { compose } from "../components/dictionary.js";
import { card, refusal, unsurveyed } from "../components/panel.js";
import { cell } from "../core/store.js";
import { run, status as loadStatus } from "../data/queries.js";

//: The three axes, read off the verb's own parameters rather than restated
//: here. A domain added in Python becomes a control with no edit to this file;
//: a control hard-coded here would be the thirty-seventh thing to drift.
const AXES = [
  { param: "utility", label: "doing" },
  { param: "for", label: "reading it" },
  { param: "domain", label: "about" },
];

let held = { state: "idle", answer: null, demand: {} };
let redraw = () => {};

function choicesFor(param) {
  const spec = ((VERBS.dashboard || {}).params || [])
    .find((item) => item.name === param);
  return (spec && spec.choices) || [];
}

/**
 * The source verb, and why the choice is not cosmetic.
 *
 * `scan` reads the elements the manifest declared and something attached to;
 * `discover .` reads a tree. They are different answers, not two spellings of
 * one — a scan is bounded by what was declared and a discover is bounded by
 * what is on disk — so the screen picks the one that matches the situation and
 * *shows which it used*, rather than always firing a full tree walk at a
 * console somebody opened to glance at a number.
 */
function sourceFor() {
  const held = cell("status");
  const open = held && held.value && held.value.environment;
  return open ? "scan" : "discover .";
}

function pipelineFor(demand) {
  const flags = Object.entries(demand)
    .filter(([, value]) => value)
    .map(([key, value]) => `--${key} ${value}`)
    .join(" ");
  // `--govern` always. Half the templates read the findings star, and the
  // rules are what fills it — a security board that renders three zeros
  // because nobody ran them is worse than no board, since a zero reads as
  // "nothing is wrong" rather than as "nothing was checked".
  return `${sourceFor()} | dashboard --govern${flags ? ` ${flags}` : ""}`;
}

function controls() {
  const row = AXES.map(({ param, label }) => {
    const options = choicesFor(param);
    const select = h("select", {
      "aria-label": label,
      onchange: (event) => {
        held.demand = { ...held.demand, [param]: event.target.value };
        // Reactive by construction: changing the demand re-selects the
        // template, so the screen answers the new question rather than
        // re-sorting the old one's answer.
        ask();
      },
    }, [
      h("option", { value: "" }, `any ${label}`),
      ...options.map((name) => h("option", {
        value: name,
        selected: held.demand[param] === name ? "selected" : null,
      }, name)),
    ]);
    return h("label", { class: "param", title: `what you are ${label}` },
      h("span", {}, label), select);
  });

  return h("div", { class: "row" }, row);
}

function chosen(answer) {
  const template = answer.template || {};
  const selection = answer.selection || {};
  const weak = selection.confident === false;

  return card(template.title || template.key || "Dashboard",
    h("p", { class: "muted prose" }, template.doc || ""),
    h("div", { class: "row" },
      h("span", { class: "mono muted" }, template.key || ""),
      weak ? h("span", { class: "chip" }, "closest, not right") : null),
    selection.reason
      ? h("p", { class: "muted" }, selection.reason)
      : null,
    // Not red. A selection the engine is unsure of is the platform declining
    // to pretend, which is a refusal and reads as an accent everywhere else.
    weak
      ? refusal({
        heading: "No template answers this well",
        message: selection.reason || "",
        obligation: "What follows is the closest one rather than the right one.",
      })
      : null);
}

function drawPanel(item) {
  // A panel whose source this ring cannot reach draws as unsurveyed ground
  // rather than vanishing. An absent panel and a panel with nothing in it look
  // identical on a screen, and only one of them is true.
  const source = (item.options || {}).source;
  if (source && !(item.data || []).length && !Object.keys(item.values || {}).length) {
    return card(item.title || item.component, unsurveyed(
      `${source} not reachable from here`,
      `This panel reads the ${source}, which lives in the enterprise ring. `
      + `The console is drawing it empty rather than hiding it, so nobody `
      + `reads "no jobs" off a panel that was never connected.`,
    ));
  }

  const measures = Object.entries(item.values || {});
  if (item.component === "stat" && measures.length) {
    const [name, measure] = measures[0];
    // The measure's own sentence rides under the number. A stat with no
    // definition is the thing `measures.py` exists to prevent: two screens
    // showing "findings" and meaning different sets.
    const block = {
      ...item, select: "",
      options: { ...(item.options || {}), note: measure.doc || name },
    };
    return compose([block], () => measure.value);
  }
  return compose([{ ...item, select: "" }], () => item.data || []);
}

function gaps(list) {
  if (!list || !list.length) return null;
  return card("What limits this",
    h("ul", { class: "reasoning" }, list.map((gap) => h("li", {},
      h("div", { class: "what" }, gap.subject || gap.kind || "gap"),
      h("div", { class: "muted" }, gap.detail || "")))));
}

function body() {
  if (held.state === "asking") {
    return h("p", { class: "muted" }, "Reading the estate…");
  }
  if (held.state === "error") {
    const error = held.error || {};
    return unsurveyed(error.heading || "could not build",
      error.message || "The composition did not complete.");
  }
  if (!held.answer) {
    return h("p", { class: "empty" },
      "Pick what you are doing, where you are reading it, or what it is "
      + "about — any one of the three is enough to choose a board.");
  }

  const answer = held.answer;
  // Grouping consecutive tiles into a row is `compose()`'s job, and this screen
  // draws its panels one at a time because each carries its own data — so the
  // grouping is applied here in the same shape rather than reimplemented.
  const drawn = [];
  let tiles = [];
  const flush = () => {
    if (tiles.length) drawn.push(h("div", { class: "grid" }, tiles));
    tiles = [];
  };

  for (const panel of answer.panels || []) {
    const node = drawPanel(panel);
    if (!node) continue;
    if (panel.component === "stat") tiles.push(node);
    else { flush(); drawn.push(node); }
  }
  flush();

  return h("div", { class: "stack" },
    chosen(answer),
    drawn.length
      ? h("div", { class: "stack" }, drawn)
      : h("p", { class: "empty" }, "The template filled no panels."),
    gaps(held.gaps));
}

/** Whether the reader has said anything yet. */
function stated() {
  return Object.values(held.demand || {}).some(Boolean);
}

async function ask() {
  // Nothing is chosen until something is asked. An empty demand ties across
  // every template and lands below the floor, so running it would greet a
  // first-time reader with "no template answers this well" — the engine
  // correctly reporting that no question was put to it, in the voice of a
  // failure. The chooser is the answer to an empty demand.
  if (!stated()) {
    held = { ...held, state: "idle", answer: null, gaps: [] };
    redraw();
    return;
  }
  held = { ...held, state: "asking" };
  redraw();

  const pipeline = pipelineFor(held.demand);
  const answer = await run(pipeline);
  if (!answer.ok) {
    held = { ...held, state: "error", error: answer.error || {} };
    redraw();
    return;
  }
  const flow = (answer.body && answer.body.flow) || answer.body || {};
  held = {
    ...held,
    state: "ready",
    answer: flow.value || null,
    gaps: flow.gaps || [],
  };
  redraw();
}

export function mount(outlet, params, query) {
  held = {
    state: "idle",
    answer: null,
    gaps: [],
    // Deep-linkable, because "look at this board" is something people send to
    // each other, and a dashboard that always opens on its default is one
    // nobody can share.
    demand: {
      utility: query.utility || "",
      for: query.for || "",
      domain: query.domain || "",
      template: query.template || "",
    },
  };

  redraw = () => {
    const pipeline = pipelineFor(held.demand);
    fill(outlet, h("div", { class: "stack" },
      card("What do you need?",
        h("p", { class: "muted prose" },
          "Three axes, because they vary independently: monitoring security in "
          + "a console and reporting on it in a document share a subject and "
          + "almost nothing else."),
        controls()),
      body(),
      card("The composition behind it",
        h("p", { class: "mono scroll" }, pipeline),
        h("div", { class: "row" },
          link(`#/compose?pipeline=${encodeURIComponent(pipeline)}`, {},
            "open it in Compose"),
          // Explicit, because the refresh records observations and an
          // event-driven one would trigger itself. A button the reader presses
          // is the honest version of "live" for an action that writes.
          h("button", {
            type: "button", class: "go",
            onclick: () => ask(),
          }, "Run it again")))));
  };

  redraw();
  // The status cell decides which source verb the composition opens with, so
  // it is fetched before the first run rather than after it.
  Promise.resolve(loadStatus()).then(ask, ask);
}

export function unmount() {
  held = { state: "idle", answer: null, demand: {} };
  redraw = () => {};
}

//: Deliberately empty. Refreshing this board runs a source verb that records
//: observations, so waking on `observation_recorded` would have each refresh
//: trigger the next one. The manifest says the same thing, and this is the
//: half a reader of the screen file would otherwise have to infer.
export const needs = [];
