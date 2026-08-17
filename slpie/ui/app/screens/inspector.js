/* The generated screen, for every capability nobody has authored a screen for.
 *
 * Deliberately utilitarian. Four finished screens and a set of honest
 * inspectors reads as a platform; thirty-six half-designed screens reads as a
 * broken product, and the difference is entirely in whether the unfinished ones
 * are *obviously* generated.
 *
 * What it guarantees is the thing that matters: **no capability is unreachable.**
 * A verb with no designed home still has a place to be run from, with its
 * parameters, its type signature and its examples — which is §24's rule applied
 * to the surface rather than to the registry.
 */

import { fill, h } from "../core/dom.js";
import { VERBS } from "../data/client.js";
import { verb as runVerb } from "../data/queries.js";
import { card, fault, loading } from "../ui/panel.js";
import { chip, pill } from "../ui/pill.js";

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

function signature(name) {
  const spec = VERBS[name];
  return h("span", { class: "stage-flow mono" },
    `${spec.consumes} → ${spec.produces}`);
}

export function mount(outlet, params) {
  const screen = params.__screen;
  const names = screen.verbs.length
    ? screen.verbs
    : Object.keys(VERBS).filter((name) => VERBS[name].group === screen.key.replace("group-", ""));

  const output = h("div", { class: "stack" });

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
      h("div", { class: "row" }, signature(name),
        spec.mutates ? pill("mutates", "warn") : null,
        spec.source ? pill("source", "") : null),
      spec.params.length ? row : null,
      h("div", { class: "row" }, go),
      spec.examples.length
        ? h("p", { class: "mono muted" }, spec.examples[0])
        : null);
  });

  fill(outlet,
    h("div", { class: "stack" },
      h("p", { class: "muted prose" },
        "A generated screen. Every capability the platform has is reachable "
        + "from somewhere, and this is where the ones without a designed home "
        + "live — the parameters, the type signature and an executable example, "
        + "straight from the registry."),
      h("div", { class: "grid" }, panels),
      output));
}

export const needs = [];
