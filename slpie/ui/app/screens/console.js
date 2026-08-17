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
import { card, fault, loading, panel } from "../ui/panel.js";
import { pill, target } from "../ui/pill.js";

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
  return h("div", {}, found.map((gap) => h("div", { class: "gap" },
    h("div", { class: "what" }, gap.kind || "gap"),
    h("div", { class: "fix" }, gap.detail || gap.reason || ""))));
}

function turn(entry) {
  if (entry.error) {
    return h("div", { class: "turn" },
      h("div", { class: "q" }, entry.question), fault(entry.error));
  }
  const answer = entry.answer || {};
  return h("div", { class: "turn" },
    h("div", { class: "q" }, entry.question),
    h("div", { class: "a reading" },
      typeof answer.answer === "string"
        ? answer.answer
        : JSON.stringify(answer.answer)),
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

      h("div", { class: "grid" },
        card("Environment", panel(held, (value) => h("div", {},
          h("div", { class: "metric" }, value.environment || "—"),
          h("div", { class: "row" }, target(value.target))), {
          sentence: "No environment is open — the catalogue and the manual "
            + "still answer.",
          rows: 2,
        })),
        card("Gaps limiting every answer",
          latest && latest.answer ? gaps(latest.answer.gaps) : gaps([]))),

      card("Conversation",
        pending ? loading(2) : null,
        turns.length
          ? h("div", {}, turns.map(turn))
          : h("p", { class: "empty" }, "Ask a question to begin.")),

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
