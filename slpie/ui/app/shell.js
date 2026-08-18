/* The composition root: routes, navigation, the one live connection.
 *
 * Small on purpose. It owns three things nothing else can own — the route
 * table, the single `EventSource`, and the outlet — and it owns no screen. A
 * screen is mounted by key from the generated manifest, so adding one is
 * adding a file rather than editing this.
 */

import { el, fill, h } from "./core/dom.js";
import { on } from "./core/bus.js";
import { navigate, parse, route, start } from "./core/router.js";
import { cell, invalidate, subscribe } from "./core/store.js";
import { connect, watchVisibility } from "./data/live.js";
import { status as loadStatus } from "./data/queries.js";
import { manifest, mount } from "./screens/index.js";
import { control } from "./ui/density.js";
import { connection, target } from "./ui/pill.js";
import { nav, pageHead, tabs } from "./ui/nav.js";

const screens = manifest();

for (const screen of screens) route(screen.path, screen);

/* The title block's two fixed facts: which environment this sheet describes,
 * and what is answering for it. They were markup placeholders reading "—" and
 * "simulated" that nothing ever wrote to, which on a surface whose whole claim
 * is that a blank field and an unknown one are different answers is the exact
 * mistake it exists to prevent — a field permanently displaying a value nobody
 * computed. Filled from the status cell, on every screen rather than only on
 * the console, because the header is on every screen. */
function titleBlock(state) {
  const name = el("environment");
  if (name) {
    const open = state.value && state.value.environment;
    // "none open" rather than a dash. A field showing a value nobody computed
    // is the one mistake a console about evidence cannot make.
    name.textContent = open || "none open";
    name.classList.toggle("unfilled", !open);
  }

  const slot = el("target");
  if (slot && state.value) slot.replaceWith(withId(target(state.value.target)));
}

function withId(node) {
  node.id = "target";
  return node;
}

function outlet() {
  let found = el("outlet");
  if (!found) {
    found = h("div", { id: "outlet" });
    const main = document.querySelector("main");
    if (main) fill(main, found);
    else document.body.appendChild(found);
  }
  return found;
}

function chrome(screen) {
  const rail = document.querySelector(".rail");
  if (rail) {
    const existing = rail.querySelector("nav");
    const next = nav(screens, { current: screen.key });
    if (existing) existing.replaceWith(next);
    else rail.appendChild(next);
  }

  const slot = el("appearance");
  if (slot && !slot.childElementCount) fill(slot, control());
}

function draw({ params, query, route: entry }) {
  const screen = entry ? entry.definition : screens[0];
  if (!entry) {
    // An unknown hash is not an error worth a page for. Land on the console,
    // replacing the history entry so Back does not bounce between them.
    navigate(screens[0].path.replace(/\/:[^/]*/g, ""), { replace: true });
    return;
  }
  chrome(screen);

  // Breadcrumb, title and section tabs are the shell's, not the screen's, so
  // thirty-six screens cannot disagree about where a title sits. A screen
  // renders only its own content into `target`.
  const body = outlet();
  const target = h("div", { class: "stack" });
  fill(body,
    pageHead(screens, screen.key, { subtitle: screen.summary || "" }),
    tabs(screens, screen.key),
    target);
  mount(target, screen, params, query);
}

/* The events that make a screen's answer out of date are declared per screen in
 * the generated manifest. This is the whole invalidation chain — the previous
 * interface matched event kinds against the current view name with a
 * `startsWith` chain, which a new kind silently fell off. */
on("event", (event) => {
  const here = parse(window.location.hash);
  const screen = here.route && here.route.definition;
  if (!screen) return;
  const wanted = screen.events || [];
  if (wanted.includes("*") || wanted.includes(event.kind)) {
    invalidate(screen.reads || []);
    draw(here);
  }
});

/* Switching register re-renders the current screen, it does not merely restyle
 * it. Most of the difference between `calm` and `dense` is tokens and needs no
 * JavaScript — but not all of it: which *columns* a grid shows is decided in
 * `ui/grid.js` at render time, because column count is the one thing a token
 * cannot express. Without this the register switched, the geometry changed, and
 * the dense-only columns stayed exactly as they were until the reader happened
 * to navigate — a control that half worked, which is worse than one that does
 * not, because the reader concludes the columns are simply missing. */
on("density", () => draw(parse(window.location.hash)));

on("connection", (state) => {
  const existing = el("connection");
  if (existing) existing.replaceWith(connection(state));
});

on("dropped", ({ missed }) => {
  // A gap means everything held is suspect. Applying the tail of a partial
  // replay to a state missing its head is how a screen ends up confidently
  // wrong, so the honest response is to draw again from scratch.
  console.warn(`missed ${missed} events; refetching`);
  draw(parse(window.location.hash));
});

subscribe("status", titleBlock);
titleBlock(cell("status"));

start(draw);
watchVisibility();
connect();
loadStatus();
