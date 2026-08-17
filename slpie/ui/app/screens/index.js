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
import { SCREENS } from "../data/client.js";
import * as consoleScreen from "./console.js";
import * as composeScreen from "./compose.js";
import * as findingsScreen from "./findings.js";
import * as graphScreen from "./graph.js";
import * as catalogScreen from "./catalog.js";
import * as workspacesScreen from "./workspaces.js";
import * as inspector from "./inspector.js";

export const AUTHORED = {
  console: consoleScreen,
  compose: composeScreen,
  findings: findingsScreen,
  graph: graphScreen,
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

let mounted = null;

export function mount(outlet, screen, params, query) {
  if (mounted && mounted.unmount) {
    try {
      mounted.unmount();
    } catch (error) {
      console.error("a screen failed while unmounting", error);
    }
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
