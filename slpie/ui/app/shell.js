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
import { invalidate } from "./core/store.js";
import { connect, watchVisibility } from "./data/live.js";
import { manifest, mount } from "./screens/index.js";
import { control } from "./ui/density.js";
import { connection } from "./ui/pill.js";
import { crumbs, nav, subnav } from "./ui/nav.js";

const screens = manifest();

for (const screen of screens) route(screen.path, screen);

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
  const header = document.querySelector("header");
  if (!header) return;

  const existing = header.querySelector("nav");
  const next = nav(screens, { current: screen.key });
  if (existing) existing.replaceWith(next);
  else header.insertBefore(next, header.children[1] || null);

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

  const body = outlet();
  const trail = crumbs(screens, screen.key);
  const sub = subnav(screens, screen.key);
  const target = h("div", {});
  fill(body, trail, sub, target);
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

start(draw);
watchVisibility();
connect();
