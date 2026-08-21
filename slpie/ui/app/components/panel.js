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
import { ago, certainty, CERTAINTY, CERTAINTY_MEANS } from "../core/format.js";
import { NO_ENVIRONMENT } from "../core/result.js";

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

/**
 * Ground the platform did not survey.
 *
 * Not `empty`. Blank means "there is nothing here"; this means "I did not look
 * here", and the whole product rests on those being different answers —
 * `Coverage.UNKNOWN`, `INDETERMINATE`, a refused capability, a quarantined
 * document. A dashboard that renders all four as an empty panel has quietly
 * turned "I could not see" into "there was nothing", which is the one mistake
 * this platform exists to refuse.
 *
 * `next` is the thing that would make it look — the console's own suggestion
 * mechanism, at the moment a reader is most stuck. An unknown-state card that
 * only reports is a dead end; one that offers the composition is a step.
 */
export function unsurveyed(what, why, next = null) {
  return h("div", { class: "unsurveyed", role: "note" },
    h("div", { class: "glyph", "aria-hidden": "true" }, "⊘"),
    h("div", { class: "what" }, what),
    why ? h("div", { class: "why" }, why) : null,
    next);
}

/**
 * A claim, carrying the mark for how it was arrived at.
 *
 * The text never dims. Rendering a 0.25 claim at a quarter opacity is the
 * obvious move and it makes the thing unreadable, so the uncertainty lives in
 * the trailing chip and the ink stays at full contrast.
 */
export function claim(value, confidence) {
  const band = certainty(confidence);
  return h("span", {
    dataset: { certainty: band },
    title: `${CERTAINTY_MEANS[band]}${
      confidence == null ? "" : ` (${Number(confidence).toFixed(2)})`
    }`,
  }, value);
}

/** The key, shown once per screen that uses the marks — never per row. */
export function key() {
  return h("div", { class: "key" },
    CERTAINTY.map((band) =>
      h("span", {},
        h("span", { dataset: { certainty: band } }),
        h("span", { class: "means" }, CERTAINTY_MEANS[band]))));
}

/** A status: a glyph, a word, a colour. Never colour alone — that is unreadable
 *  to a colour-blind reviewer and to a screenshot pasted into a ticket, which
 *  is where most of these end up. */
export function status(word, tone = "none") {
  return h("span", { class: `status ${tone}` }, word);
}

/** A proportion, drawn as one whole divided. `parts` is `[[count, tone], …]`. */
export function bar(parts) {
  const total = parts.reduce((sum, [count]) => sum + Number(count || 0), 0);
  if (!total) return h("div", { class: "bar" }, h("div", { class: "rest", style: { width: "100%" } }));
  return h("div", { class: "bar" },
    parts.filter(([count]) => count > 0).map(([count, tone]) =>
      h("div", { class: tone, style: { width: `${(count / total) * 100}%` } })));
}

/** The metric card both references open every page with: label, big number,
 *  and — where the number is a proportion — the bar and its legend. */
export function metric(label, value, { unit = "", when = "", parts = null } = {}) {
  return h("div", { class: "metric-card" },
    h("div", { class: "head" },
      h("span", {}, label),
      when ? h("span", { class: "when" }, when) : null),
    h("div", { class: "metric" }, String(value),
      unit ? h("small", {}, unit) : null),
    parts ? bar(parts) : null,
    parts
      ? h("div", { class: "legend" }, parts.filter(([count]) => count > 0)
        .map(([count, tone, name]) =>
          h("span", {}, h("i", { class: tone }), `${count} ${name || tone}`)))
      : null);
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
    // A 409 is the platform saying it could not look — no environment is open,
    // no control plane is attached. That is unsurveyed ground, not an empty
    // result, and rendering it as one italic sentence is exactly the
    // conflation this console exists to refuse. Handling it here rather than
    // in each screen makes every screen honest by construction instead of by
    // remembering.
    if (error.kind === NO_ENVIRONMENT) {
      return unsurveyed(error.heading || "not surveyed", error.message);
    }
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
