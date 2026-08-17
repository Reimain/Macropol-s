/* Pills, chips and the connection indicator.
 *
 * Every state carries a word as well as a colour, and `components.css` adds a
 * glyph. Three channels rather than one, because a severity readable only by
 * hue is unreadable to a colour-blind reviewer and unreadable in a screenshot
 * pasted into a ticket — which is where most of these end up.
 */

import { h } from "../core/dom.js";

const SEVERITY = {
  critical: "bad", high: "bad", medium: "warn", low: "ok", info: "ok",
};

export function pill(text, tone = "") {
  return h("span", { class: `pill ${tone}`.trim() }, text);
}

export function severity(name) {
  return pill(name || "info", SEVERITY[String(name).toLowerCase()] || "");
}

export function target(name) {
  return pill(name || "simulated", name === "live" ? "live" : "simulated");
}

/* The feed's own words. Never "live", which is what a *target* is: two pills
 * sitting side by side reading "SIMULATED" and "LIVE" is a contradiction on
 * the screen even though both are true of different things. */
const FEED = {
  live: "streaming",
  connecting: "connecting",
  reconnecting: "reconnecting",
  offline: "not streaming",
};

/**
 * The connection indicator, which says which sequence it last saw.
 *
 * A tab that has quietly fallen behind looks exactly like one that is current,
 * and that is the failure §23 names `STALE_REPLICA` on the server. The same
 * honesty applies to a browser: the sequence is shown so a reader can tell.
 */
export function connection({ state, lastSequence }) {
  const tone = state === "live" ? "ok" : state === "offline" ? "bad" : "warn";
  const node = pill(FEED[state] || state, tone);
  node.id = "connection";
  node.title = lastSequence
    ? `last event seen: ${lastSequence}`
    : "no events seen yet";
  return node;
}

export function chip(text, { usable = true, danger = false, onclick, title = "" } = {}) {
  return h("button", {
    type: "button",
    class: `chip ${usable ? "usable" : "blocked"}${danger ? " danger" : ""}`,
    disabled: !usable,
    title,
    onclick: usable ? onclick : null,
  }, text);
}
