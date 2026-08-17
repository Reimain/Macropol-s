/* The rail, built from the screen manifest and what the caller may use.
 *
 * Two rules, and the second is the one worth stating plainly.
 *
 * **The menu is rendered from what the platform said is permitted**, in one
 * call, rather than by probing each route and hiding the ones that 403. Probing
 * is slow, noisy in the audit log, and wrong the moment a grant changes
 * mid-session.
 *
 * **Hiding a screen is a convenience, never the control.** A screen absent from
 * this menu is still refused at the route if somebody types the URL, and a test
 * asserts exactly that. Treating a hidden link as a security boundary is how an
 * interface ends up being the only thing standing between a caller and data.
 *
 * The rail lists *screens* under *section headings*, rather than listing six
 * sections and hiding their contents behind a second click. With thirty-six
 * destinations the reader needs to see the whole shape of the product at once;
 * that is the trade a vertical rail buys and a horizontal strip cannot.
 */

import { h, link } from "../core/dom.js";

const LABELS = {
  console: "Console",
  operate: "Operate",
  build: "Build",
  catalog: "Catalog",
  api: "API",
  admin: "Admin",
};

/* No per-item icon, deliberately. The references give each rail item its own
 * icon, which works because each of theirs means something different. Here the
 * only glyph available per item without shipping thirty hand-maintained SVGs is
 * the *section's* glyph, which would put the same mark beside all eleven Operate
 * items — a column of identical characters that carries no information and costs
 * a column of width. Grouping plus indentation does the wayfinding instead, and
 * a repeated icon is worse than none. */

/** Screens grouped by section, in manifest order. */
function sections(screens) {
  const grouped = new Map();
  for (const screen of screens) {
    if (!grouped.has(screen.section)) grouped.set(screen.section, []);
    grouped.get(screen.section).push(screen);
  }
  return grouped;
}

function href(screen) {
  return `#${screen.path.replace(/\/:[^/]*/g, "")}`;
}

export function nav(screens, { permitted = null, current = "" } = {}) {
  const here = screens.find((screen) => screen.key === current);
  // A view highlights its destination. Looking at a node, the rail should show
  // Graph as active — the reader is in Graph, several levels down.
  const active = here && here.parent ? here.parent : current;

  const allowed = (permitted
    ? screens.filter((screen) => permitted.has(screen.action) || !screen.action)
    : screens)
    // Destinations only. Views are reached from their parent's page, which is
    // the whole reason `Screen.parent` exists.
    .filter((screen) => !screen.parent);

  const node = h("nav", { "aria-label": "sections" });

  for (const [section, members] of sections(allowed)) {
    // A section with one screen needs no heading above it — the heading and the
    // item would say the same word twice.
    if (members.length > 1) {
      node.appendChild(h("div", { class: "group" }, LABELS[section] || section));
    }
    for (const screen of members) {
      const on = screen.key === active;
      node.appendChild(link(href(screen), {
        class: on ? "active" : "",
        "aria-current": on ? "page" : null,
        title: screen.authored ? "" : "generated inspector",
      }, screen.title));
    }
  }
  return node;
}

/**
 * Tabs across the views of the *one thing* this page is about.
 *
 * Ruled underneath, which is the references' detail-page pattern — Overview,
 * Sample Data, Details, Permissions, History for a single table. That is the
 * whole distinction from the rail, and it is worth holding: the rail moves you
 * between parts of the product, tabs move you between views of the object you
 * are already looking at.
 *
 * This deliberately does *not* render a screen's section siblings. That was
 * tried, and it put every Operate screen in a strip directly beneath a rail
 * already listing every Operate screen — the same eleven links twice on one
 * page, the second copy teaching the reader nothing and costing a row of
 * vertical space on every screen in the section. The tabs are the destination
 * and its declared children, and nothing else.
 *
 * A destination with more children than fit as tabs gets none: twenty tabs is a
 * list wearing a tab bar, and the parent screen should render its own list.
 */
const TAB_LIMIT = 7;

export function tabs(screens, current) {
  const here = screens.find((screen) => screen.key === current);
  if (!here) return null;

  const rootKey = here.parent || here.key;
  const root = screens.find((screen) => screen.key === rootKey);
  if (!root) return null;

  // A view whose path takes a *required* subject is not reachable as a tab: its
  // link would resolve to `#/impact/` with nothing to be the impact of, and a
  // tab that lands on an empty screen is worse than an absent one. Those views
  // are reached by picking a subject — from the graph, from a finding — and
  // once you are on one, the tab stays so you can get back.
  //
  // Optional params (`:severity?`) are fine: the screen has a meaningful
  // subject-less state, which is exactly what the `?` declares.
  const needsSubject = (screen) =>
    /\/:[^/?]+(?!\?)(?:\/|$)/.test(screen.path) && screen.key !== current;

  const family = [root, ...screens.filter((screen) => screen.parent === rootKey)]
    .filter((screen) => !needsSubject(screen));
  if (family.length < 2 || family.length > TAB_LIMIT) return null;

  return h("nav", { class: "tabs", "aria-label": `${root.title} views` },
    family.map((screen) => link(href(screen), {
      class: screen.key === current ? "active" : "",
      "aria-current": screen.key === current ? "page" : null,
      title: screen.authored ? "" : "generated inspector",
    }, screen.key === rootKey ? "Overview" : screen.title)));
}

/** Breadcrumbs from the manifest, so a pasted deep link shows the same trail a
 *  click-through does. Deriving them from history would give a different
 *  answer depending on how you arrived, which is the wrong kind of context. */
export function crumbs(screens, current) {
  const here = screens.find((screen) => screen.key === current);
  if (!here || !here.crumbs.length) return null;

  const trail = here.crumbs
    .map((key) => screens.find((screen) => screen.key === key))
    .filter(Boolean);

  return h("nav", { class: "crumbs", "aria-label": "breadcrumb" },
    trail.map((screen) => [
      link(href(screen), {}, screen.title),
      h("span", { class: "sep", "aria-hidden": "true" }, "/"),
    ]),
    h("span", {}, here.title));
}

/** The page head: where you are, what this is, and what you can do about it.
 *
 *  Every screen gets one, and it is built here rather than by each screen, so
 *  thirty-six screens cannot disagree about where a title sits. `actions` is
 *  the screen's primary control — at most one filled button, because a screen
 *  with three has told the reader nothing about which one they came for. */
export function pageHead(screens, current, { subtitle = "", actions = null } = {}) {
  const here = screens.find((screen) => screen.key === current);
  if (!here) return null;

  return h("div", { class: "page-head" },
    h("div", { class: "page-title" },
      crumbs(screens, current),
      h("h1", {}, here.title),
      subtitle ? h("div", { class: "sub" }, subtitle) : null),
    actions ? h("div", { class: "acts" }, actions) : null);
}
