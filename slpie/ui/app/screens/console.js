/* The console: ask, and get an answer with its reasoning and its gaps.
 *
 * Never a bare value — invariant 5, at the surface it was written for. An
 * answer arrives with the steps that produced it, each terminating in a file
 * and a line, and with the gaps that limit it. The gaps are not a footnote:
 * they are the difference between a low-confidence answer and a misleading one,
 * and they are rendered next to the answer rather than below the fold.
 */

import { fill, h, link } from "../core/dom.js";
import { on } from "../core/bus.js";
import { cell, subscribe } from "../core/store.js";
import { cite, confidence } from "../core/format.js";
import { ask, status as loadStatus } from "../data/queries.js";
import { card, claim, fault, key, loading, panel, unsurveyed } from "../components/panel.js";
import { opener } from "../components/opener.js";
import { pill, target } from "../components/pill.js";

const stops = [];
const turns = [];
const feed = [];
const FEED_LIMIT = 60;

function reasoning(path) {
  const steps = (path && path.steps) || [];
  if (!steps.length) return null;
  return h("ol", { class: "reasoning" },
    steps.map((step) => h("li", {},
      h("div", {}, step.claim || step.detail || ""),
      (step.evidence || []).length
        ? h("div", { class: "cite mono" },
          (step.evidence || []).map((piece) => cite(piece.location)).join("  "))
        : null)));
}

function gaps(found) {
  if (!found || !found.length) {
    return h("p", { class: "empty" }, "Nothing limits this answer.");
  }
  // A gap *is* unsurveyed ground — a capability that was refused, a tree
  // nothing read, a region the platform declined to rule on. It gets the
  // hatch, because that is what the hatch means; rendering it as one more
  // bullet in a list is how a gap becomes a footnote nobody reads, and the
  // gaps are the difference between a low-confidence answer and a misleading
  // one.
  return h("div", { class: "stack" },
    found.map((gap) => unsurveyed(
      gap.kind || "gap",
      gap.detail || gap.reason || "",
    )));
}

function turn(entry) {
  if (entry.error) {
    return h("div", { class: "turn" },
      h("div", { class: "q" }, entry.question), fault(entry.error));
  }
  const answer = entry.answer || {};
  return h("div", { class: "turn" },
    h("div", { class: "q" }, entry.question),
    // The answer carries its own mark. A reader who has learned the rule can
    // tell a pinned answer from an inferred one before reading either.
    h("div", { class: "a reading" },
      claim(
        typeof answer.answer === "string"
          ? answer.answer
          : JSON.stringify(answer.answer),
        answer.confidence,
      )),
    h("div", { class: "row" },
      pill(`confidence ${confidence(answer.confidence)}`,
        Number(answer.confidence) >= 0.8 ? "ok" : "warn")),
    reasoning(answer.reasoning),
    (answer.next_questions || []).length
      ? h("div", { class: "row" },
        (answer.next_questions || []).slice(0, 4).map((next) =>
          h("button", {
            type: "button",
            class: "chip usable",
            onclick: () => submit(next.text || next),
          }, next.text || next)))
      : null);
}

let redraw = () => {};
let pending = false;

/** Whether an environment is actually open, rather than whether the status call
 *  happened to succeed. A 409 answers, and answering is not the same as having
 *  something to answer about. */
function open(held) {
  return Boolean(held.value && held.value.environment);
}

async function submit(question) {
  if (!question || pending) return;
  pending = true;
  redraw();
  const answer = await ask(question);
  pending = false;
  turns.unshift(
    answer.error
      ? { question, error: answer.error }
      : { question, answer: answer.body },
  );
  redraw();
}

/**
 * What a first-time reader sees, and the only thing on this screen before they
 * ask anything.
 *
 * It is the legend for every mark the rest of the console uses, and it is here
 * rather than in a help page because a legend nobody finds is a cipher. It
 * disappears the moment there is a real answer to read — a landing state that
 * persists after you have started working is furniture.
 */
function legend() {
  return h("div", { class: "stack" },
    h("p", { class: "reading" },
      "Every claim carries a mark saying how it was arrived at — read from a "
      + "lockfile pin, or joined from a config reference, or guessed from a "
      + "name. The text never dims: a claim the platform is unsure of is still "
      + "one you have to be able to read."),
    key(),
    h("p", { class: "reading muted" },
      "And where the platform did not look at all — a refused capability, a "
      + "tree nothing read, a verdict it declined to reach — it says so rather "
      + "than showing you an empty panel. Finding nothing and not looking are "
      + "different answers, and this console will never show you one as the "
      + "other."));
}

function line(event) {
  return h("div", { class: `event new ${event.operational ? "operational" : ""}` },
    h("span", { class: "seq" }, String(event.sequence ?? "")),
    h("span", { class: "kind" }, event.kind || ""),
    h("span", { class: "subject" }, event.subject || ""));
}

export function mount(outlet) {
  const field = h("input", {
    type: "text",
    id: "question",
    "aria-label": "ask about this environment",
    placeholder: "Ask about this environment — what breaks if lodash 5 lands?",
    autocomplete: "off",
    onkeydown: (raw) => {
      if (raw.key === "Enter") submit(field.value.trim());
    },
  });

  redraw = () => {
    const held = cell("status");
    const latest = turns[0];

    fill(outlet, h("div", { class: "stack" },
      h("div", { class: "ask" }, field,
        h("button", {
          type: "button", class: "go", disabled: pending,
          onclick: () => submit(field.value.trim()),
        }, pending ? "Asking…" : "Ask")),

      // Nothing is open yet, so the first thing on the page is the way to open
      // something. A console whose landing state explains itself but offers no
      // way in has made the reader read a paragraph to learn they are stuck.
      open(held)
        ? null
        : card("Point the platform at a folder",
          opener({ onOpened: () => loadStatus() })),

      // The environment tile only once there is an environment. With nothing
      // open the opener directly above already says so, and rendering a second
      // panel to repeat it makes the console look like it is reporting a fault
      // rather than waiting to be pointed somewhere.
      open(held) || (latest && latest.answer)
        ? h("div", { class: "grid" },
          open(held)
            ? card("Environment", panel(held, (value) => h("div", {},
              h("div", { class: "metric" }, value.environment || "—"),
              h("div", { class: "row" }, target(value.target))), { rows: 2 }))
            : null,
          // Only once there is an answer. Before that there is nothing for a
          // gap to limit, and "nothing limits this answer" about an answer
          // nobody asked for is a sentence that means nothing.
          latest && latest.answer
            ? card("Gaps limiting this answer", gaps(latest.answer.gaps))
            : null)
        : null,

      card("Conversation",
        pending ? loading(2) : null,
        turns.length
          ? h("div", {}, turns.map(turn))
          : legend()),

      card("Live activity",
        feed.length
          ? h("div", { id: "feed" }, feed.map(line))
          : h("p", { class: "empty" }, "Waiting for events…"))));
  };

  stops.push(subscribe("status", redraw));
  stops.push(on("event", (event) => {
    // The events are kept, not the nodes they render to. Storing nodes and
    // re-rendering them would insert the same element twice, and the second
    // insertion silently moves it out of the first — a class of bug that only
    // shows up once two panels want the same row.
    //
    // New rows go on the front and stay put: a feed that reorders under the
    // cursor is the specific way a live console becomes unusable.
    feed.unshift(event);
    if (feed.length > FEED_LIMIT) feed.length = FEED_LIMIT;
    redraw();
  }));

  redraw();
  loadStatus();
}

export function unmount() {
  while (stops.length) stops.pop()();
  redraw = () => {};
}

export const needs = ["*"];
