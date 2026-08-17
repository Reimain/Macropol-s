/* Panel states, implemented once.
 *
 * A screen returns what it wants drawn and never draws its own empty text. Only
 * the *sentence* is per-screen — "no findings at this severity" rather than "no
 * data" — because the sentence is the part that carries information and the
 * chrome around it is the part that should not vary.
 *
 * The states are the ones the platform actually has, which is more than the
 * usual three: an answer can be refused, or stale, or behind the world, or
 * partial, and flattening those into "error" throws away the distinctions the
 * whole system is built to make.
 */

import { h } from "../core/dom.js";
import { ERROR, LOADING, READY, REFUSED } from "../core/store.js";
import { ago } from "../core/format.js";

/** Skeleton rows at the current row height, not a spinner.
 *
 *  A spinner says "something is happening". A skeleton says "a table of this
 *  shape is arriving", which is the difference between waiting and knowing what
 *  you are waiting for — and it does not move, which matters to a reader who
 *  asked for reduced motion. */
export function loading(rows = 4) {
  return h("div", { class: "skeleton", "aria-busy": "true" },
    Array.from({ length: rows }, () => h("i", {})));
}

export function empty(sentence) {
  return h("p", { class: "empty" }, sentence);
}

export function refusal(error) {
  return h("div", { class: "refusal", role: "note" },
    h("h3", {}, error.heading || "Refused"),
    h("p", {}, error.message),
    error.obligation ? h("p", { class: "obligation" }, error.obligation) : null,
    error.retryAfter
      ? h("p", { class: "obligation" }, `Try again in ${error.retryAfter}s.`)
      : null);
}

export function fault(error) {
  return h("div", { class: "fault", role: "alert" },
    h("h3", {}, error.heading || "Platform fault"),
    h("p", {}, error.message),
    // Named so a support conversation can start from the exception rather than
    // from a description of the exception.
    error.detail ? h("p", { class: "mono muted" }, error.detail) : null);
}

/** Shown above a rendered answer, never instead of it. */
export function staleness(cell) {
  if (cell.stale) {
    return h("p", { class: "stale" },
      "Served from cache while offline — this may be behind the platform.");
  }
  if (cell.ledger && cell.version && cell.ledger > cell.version) {
    return h("p", { class: "stale" },
      `The world has moved on since this answer (ledger ${cell.ledger}, `
      + `answered at ${cell.version}).`);
  }
  return null;
}

/**
 * Render a cell. `body(value, cell)` is called only when there is something to
 * draw, so a screen never has to check for null.
 */
export function panel(cell, body, { sentence = "Nothing here.", rows = 4 } = {}) {
  if (cell.status === LOADING && cell.value === null) return loading(rows);
  if (cell.status === REFUSED) return refusal(cell.error || {});
  if (cell.status === ERROR && cell.value === null) {
    const error = cell.error || {};
    return error.className === "empty"
      ? empty(error.message || sentence)
      : fault(error);
  }
  if (cell.value === null || cell.value === undefined) return empty(sentence);

  const drawn = body(cell.value, cell);
  const notice = staleness(cell);
  return notice ? h("div", {}, notice, drawn) : drawn;
}

/** A card with a heading, which is how nearly every panel is framed. */
export function card(heading, ...children) {
  return h("section", { class: "card" },
    heading ? h("h2", {}, heading) : null,
    ...children);
}

export function freshness(cell) {
  if (!cell.fetchedAt) return "";
  return ago((Date.now() - cell.fetchedAt) / 1000);
}
