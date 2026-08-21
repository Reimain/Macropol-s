/* The screen registry, and the contract every screen honours.
 *
 * A screen exports:
 *
 *   mount(outlet, params, query)   draw into `outlet`; return nothing
 *   unmount()                      optional; drop subscriptions
 *   needs                          optional; cell keys to refetch on an event
 *
 * The registry is keyed by the `key` in the generated manifest, so a screen
 * file and a manifest entry cannot drift: a key with no file is an inspector,
 * and a file with no key is never reached — both are visible rather than
 * silent, because `Screen.authored` is read off the filesystem rather than
 * declared.
 */

import { fill, h } from "../core/dom.js";
import { CAPABILITIES, SCREENS, SHELL, missingFor } from "../data/client.js";
import * as consoleScreen from "./console.js";
import * as composeScreen from "./compose.js";
import * as findingsScreen from "./findings.js";
import * as graphScreen from "./graph.js";
import * as verbsScreen from "./verbs.js";
import * as catalogScreen from "./catalog.js";
import * as workspacesScreen from "./workspaces.js";
import * as inspector from "./inspector.js";

export const AUTHORED = {
  console: consoleScreen,
  compose: composeScreen,
  findings: findingsScreen,
  graph: graphScreen,
  verbs: verbsScreen,
  catalog: catalogScreen,
  workspaces: workspacesScreen,
};

export function manifest() {
  return SCREENS;
}

export function find(key) {
  return SCREENS.find((screen) => screen.key === key) || null;
}

/** The screen for a path, authored or generated. */
export function screenFor(key) {
  return AUTHORED[key] || inspector;
}

/**
 * Whether this build can draw that screen, and what it would take.
 *
 * The block manifest has a real ceiling. Direct manipulation, panes the reader
 * arranges and a scrubbable axis over the ledger are code rather than a
 * declaration, and pretending otherwise turns the manifest into a bad
 * framework. So a screen names what it *needs* and this shell names what it
 * *gives*, and the difference is reported.
 *
 * **Not omitted — reported.** A screen the console silently left out would be a
 * capability the platform has and one surface cannot reach, which is exactly
 * the drift §24 exists to prevent; hidden by the interface rather than by the
 * registry, but hidden all the same.
 */
export function unmeetable(screen) {
  return missingFor(screen, SHELL);
}

export function drawable(screen) {
  return unmeetable(screen).length === 0;
}

/** The refusal, rendered like every other refusal: accent, never danger. */
function unavailable(screen) {
  const missing = unmeetable(screen);
  return h("div", { class: "refusal", role: "note" },
    h("h3", {}, `${screen.title} needs a shell this one is not`),
    h("p", { class: "prose" },
      `This console is the ${SHELL} shell. It runs with no build step, no `
      + "package manager and no network, which is what makes it the one that "
      + "works inside an air-gapped estate — and that is also why it stops "
      + "here."),
    h("ul", {}, missing.map((name) => h("li", {},
      h("code", { class: "mono" }, name),
      h("span", { class: "muted" }, ` — ${CAPABILITIES[name] || "not described"}`)))),
    h("p", { class: "prose muted" },
      "Everything behind this screen is reachable from here as data: its "
      + "routes answer, its verbs run, and the composition it stands for is "
      + "one you can type. What is missing is the surface, not the answer."));
}

let mounted = null;

export function mount(outlet, screen, params, query) {
  if (mounted && mounted.unmount) {
    try {
      mounted.unmount();
    } catch (error) {
      console.error("a screen failed while unmounting", error);
    }
  }
  if (!drawable(screen)) {
    mounted = null;
    fill(outlet, unavailable(screen));
    return;
  }

  const chosen = screenFor(screen.key);
  mounted = chosen;
  try {
    chosen.mount(outlet, { ...params, __screen: screen }, query || {});
  } catch (error) {
    // A screen that throws must not take the shell with it. The reader still
    // has the navigation, and the fault says which screen rather than leaving
    // a blank page and a console message nobody has open.
    console.error(`screen ${screen.key} failed to mount`, error);
    fill(outlet, h("div", { class: "fault", role: "alert" },
      h("h3", {}, `The ${screen.title} screen failed`),
      h("p", {}, String(error && error.message ? error.message : error))));
  }
}

export function current() {
  return mounted;
}
