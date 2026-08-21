/* The composed screen — every capability nobody hand-built a screen for.
 *
 * It used to print `JSON.stringify(body, null, 2)` under a heading. That is
 * honest and it is not a product: thirty-two screens showing raw payloads reads
 * as unfinished no matter how good the four authored ones are.
 *
 * Now it composes. The screen manifest carries `blocks` — component keys with a
 * source, a selection and, where Python could declare them, columns — and this
 * module resolves each key against `ui/components.js` and draws it. Same data,
 * same components the authored screens use, same lexicon on the labels.
 *
 * What it still guarantees is what it always did: **no capability is
 * unreachable.** A verb with no designed home has somewhere to be run from,
 * with its parameters, its type signature and an executable example, straight
 * from the registry.
 *
 * Authored beats composed beats dumped. `screens/index.js` resolves a hand-built
 * module first, so nothing here touches a screen somebody designed.
 */

import { fill, h } from "../core/dom.js";
import { cell, subscribe } from "../core/store.js";
import { VERBS } from "../data/client.js";
import { query } from "../data/http.js";
import { verb as runVerb } from "../data/queries.js";
import { compose } from "../components/dictionary.js";
import { card, fault, loading, panel } from "../components/panel.js";
import { pill } from "../components/pill.js";

const stops = [];

/* --- the runner, which is a control rather than a view ------------------ */

function parameters(name) {
  const fields = {};
  const spec = VERBS[name];
  const row = h("div", { class: "stage-params" },
    spec.params.map((param) => {
      const field = param.choices.length
        ? h("select", { name: param.name },
          h("option", { value: "" }, "—"),
          param.choices.map((choice) => h("option", { value: choice }, choice)))
        : h("input", { type: "text", name: param.name, placeholder: param.type });
      fields[param.name] = field;
      return h("label", { class: "param", title: param.help },
        h("span", {}, `--${param.name}`), field);
    }));
  return { row, fields };
}

function runner(names, output) {
  const panels = names.filter((name) => VERBS[name]).map((name) => {
    const spec = VERBS[name];
    const { row, fields } = parameters(name);

    const go = h("button", {
      type: "button",
      class: "go",
      onclick: async () => {
        fill(output, loading(2));
        const sent = {};
        for (const [key, field] of Object.entries(fields)) {
          if (field.value) sent[key] = field.value;
        }
        if (spec.mutates) sent.confirmed = true;
        const answer = await runVerb(name, sent);
        fill(output, card(
          `${name} — result`,
          answer.error
            ? fault(answer.error)
            : h("pre", { class: "mono scroll" },
              JSON.stringify(answer.body, null, 2)),
        ));
      },
    }, spec.mutates ? "Run (changes the environment)" : "Run");

    return card(name,
      h("p", { class: "muted" }, spec.summary),
      h("div", { class: "row" },
        h("span", { class: "stage-flow mono" },
          `${spec.consumes} → ${spec.produces}`),
        spec.mutates ? pill("mutates", "warn") : null,
        spec.source ? pill("source", "") : null),
      spec.params.length ? row : null,
      h("div", { class: "row" }, go),
      spec.examples.length
        ? h("p", { class: "mono muted" }, spec.examples[0])
        : null);
  });

  return h("div", { class: "grid" }, panels);
}

/* --- reading the blocks' sources ---------------------------------------- */

/** `"GET /api/apim/apis"` → the cell key the store holds it under. */
function keyFor(source) {
  return (source || "").replace(/^GET\s+/, "");
}

export function mount(outlet, params) {
  const screen = params.__screen;
  const blocks = screen.blocks || [];
  const output = h("div", { class: "stack" });

  const names = screen.verbs.length
    ? screen.verbs
    : Object.keys(VERBS).filter(
      (name) => VERBS[name].group === screen.key.replace("group-", ""));

  // One fetch per distinct source, not one per block: two blocks over the same
  // route are two views of one answer, and fetching twice would let them
  // disagree about which version they are showing.
  const sources = [...new Set(blocks.map((block) => keyFor(block.source)))]
    .filter(Boolean);

  function draw() {
    // A screen whose blocks all read one route reports that route's state —
    // loading, refused, stale — through the shared panel rather than inventing
    // its own. A screen with several shows each block as it arrives.
    const body = sources.length === 1
      ? panel(cell(sources[0]), () => composed(), {
        sentence: "Nothing here yet.", rows: 6,
      })
      : composed();

    fill(outlet, h("div", { class: "stack" },
      h("p", { class: "muted prose" },
        "A composed screen. Nobody hand-built this one, so it is drawn from "
        + "the screen manifest — the same components and the same words the "
        + "designed screens use, assembled from data rather than written."),
      body,
      output));
  }

  function composed() {
    return compose(
      blocks,
      (block) => cell(keyFor(block.source)).value,
      { runner: () => runner(names, output) },
    );
  }

  for (const source of sources) {
    stops.push(subscribe(source, draw));
  }
  draw();
  for (const source of sources) {
    // The cell key *is* the path, which is the store's own convention — so two
    // screens asking the same question share one answer and one version.
    query(source, source).catch(() => {});
  }
}

export function unmount() {
  while (stops.length) {
    const stop = stops.pop();
    if (stop) stop();
  }
}

export const needs = [];
