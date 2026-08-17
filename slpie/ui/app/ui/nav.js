/* The navigation, built from the screen manifest and what the caller may use.
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

/** The first screen of each section, which is what a section header links to. */
function sections(screens) {
  const grouped = new Map();
  for (const screen of screens) {
    if (!grouped.has(screen.section)) grouped.set(screen.section, []);
    grouped.get(screen.section).push(screen);
  }
  return grouped;
}

export function nav(screens, { permitted = null, current = "" } = {}) {
  const allowed = permitted
    ? screens.filter((screen) => permitted.has(screen.action) || !screen.action)
    : screens;

  const node = h("nav", { "aria-label": "sections" });

  for (const [section, members] of sections(allowed)) {
    const first = members[0];
    node.appendChild(link(
      `#${first.path.replace(/\/:[^/]*/g, "")}`,
      {
        class: members.some((screen) => screen.key === current) ? "active" : "",
        "aria-current": members.some((s) => s.key === current) ? "page" : null,
      },
      LABELS[section] || section,
    ));
  }
  return node;
}

/** The screens within the current section, as a second row. */
export function subnav(screens, current) {
  const here = screens.find((screen) => screen.key === current);
  if (!here) return null;

  const siblings = screens.filter((screen) => screen.section === here.section);
  if (siblings.length < 2) return null;

  return h("nav", { "aria-label": LABELS[here.section] || here.section },
    siblings.map((screen) => link(
      `#${screen.path.replace(/\/:[^/]*/g, "")}`,
      {
        class: screen.key === current ? "active" : "",
        "aria-current": screen.key === current ? "page" : null,
        title: screen.authored ? "" : "generated inspector",
      },
      screen.title,
    )));
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

  return h("nav", { class: "row muted", "aria-label": "breadcrumb" },
    trail.map((screen) => [
      link(`#${screen.path.replace(/\/:[^/]*/g, "")}`, {}, screen.title),
      h("span", {}, "/"),
    ]),
    h("span", {}, here.title));
}
