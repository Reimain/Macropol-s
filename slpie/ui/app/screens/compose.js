/* Compose: the type graph, made something you can build in.
 *
 * The palette shows every verb, and disables the ones that cannot follow what
 * is currently flowing rather than hiding them. That is deliberate: hiding an
 * unusable verb hides the type graph, and the shape of what can follow what is
 * the thing worth learning about this platform.
 *
 * The check runs here as you build, and the server checks again before running
 * anything. Two checks, one rule — `validate()` comes from the generated client
 * rather than being written twice, which is the point of `contract.javascript()`.
 */

import { fill, h } from "../core/dom.js";
import { GROUPS, VERBS, producedKind, validate } from "../data/client.js";
import { plan as askPlan, run as runPipeline } from "../data/queries.js";
import { card, fault, loading } from "../components/panel.js";
import { chip, pill } from "../components/pill.js";
import { cite } from "../core/format.js";

let stages = [];
let outcome = null;
let busy = false;
let redraw = () => {};

function pipeline() {
  return stages.map((stage) => {
    const params = Object.entries(stage.params)
      .filter(([, value]) => value !== "" && value != null)
      .map(([name, value]) => `--${name} ${value}`)
      .join(" ");
    return params ? `${stage.verb} ${params}` : stage.verb;
  }).join(" | ");
}

function names() {
  return stages.map((stage) => stage.verb);
}

function palette() {
  const flowing = producedKind(names());
  return h("div", {},
    Object.entries(GROUPS).map(([group, members]) =>
      h("div", { class: "palette-group" },
        h("span", { class: "palette-label" }, group),
        members.map((name) => {
          const spec = VERBS[name];
          const allowed = validate([...names(), name]) === null;
          return chip(name, {
            usable: allowed,
            danger: spec.mutates,
            title: allowed
              ? `${spec.consumes} → ${spec.produces} · ${spec.summary}`
              : `${name} consumes ${spec.consumes}; ${flowing} is flowing`,
            onclick: () => {
              stages.push({ verb: name, params: {} });
              redraw();
            },
          });
        }))));
}

function stageCard(stage, index) {
  const spec = VERBS[stage.verb];
  const upto = names().slice(0, index + 1);
  return h("div", { class: "stage" },
    h("div", { class: "stage-head" },
      h("span", { class: "stage-index" }, String(index + 1)),
      h("span", { class: "stage-name mono" }, stage.verb),
      h("span", { class: "stage-flow mono" },
        `${spec.consumes} → ${producedKind(upto)}`),
      spec.mutates ? pill("mutates", "warn") : null,
      h("button", {
        type: "button", class: "drop", title: "remove this stage",
        "aria-label": `remove ${stage.verb}`,
        onclick: () => {
          stages.splice(index, 1);
          redraw();
        },
      }, "×")),
    h("div", { class: "stage-summary muted" }, spec.summary),
    spec.params.length
      ? h("div", { class: "stage-params" }, spec.params.map((param) => {
        const field = param.choices.length
          ? h("select", {
            name: param.name,
            onchange: (raw) => {
              stage.params[param.name] = raw.target.value;
              redraw();
            },
          },
          h("option", { value: "" }, "—"),
          param.choices.map((choice) => h("option", {
            value: choice, selected: stage.params[param.name] === choice,
          }, choice)))
          : h("input", {
            type: "text",
            name: param.name,
            value: stage.params[param.name] || "",
            placeholder: param.type,
            oninput: (raw) => {
              stage.params[param.name] = raw.target.value;
            },
          });
        return h("label", { class: "param", title: param.help },
          h("span", {}, `--${param.name}`), field);
      }))
      : null);
}

function provenance(flow) {
  const steps = (flow.reasoning && flow.reasoning.steps) || [];
  const gaps = flow.gaps || [];
  return h("div", {},
    steps.length
      ? h("ol", { class: "reasoning" }, steps.map((step) => h("li", {},
        h("div", {}, step.claim || ""),
        (step.evidence || []).length
          ? h("div", { class: "cites mono" },
            (step.evidence || []).map((piece) => cite(piece.location)).join("  "))
          : null)))
      : null,
    // A gap raised at stage one is present in the final flow. That is invariant
    // 5 holding *through composition*, and it is the property that makes a long
    // pipeline trustworthy rather than merely convenient — so it is rendered
    // with the answer, never below it.
    gaps.length
      ? h("div", {}, gaps.map((gap) => h("div", { class: "gap" },
        h("div", { class: "what" }, gap.kind || "gap"),
        h("div", { class: "fix" }, gap.detail || gap.reason || ""))))
      : h("p", { class: "muted" }, "No gap limits this answer."));
}

function result() {
  if (busy) return loading(3);
  if (!outcome) return null;
  if (outcome.error && !outcome.partial) return fault(outcome.error);

  const body = outcome.body || {};
  const flow = body.flow || body;
  return card("Result",
    h("div", { class: "result-head" },
      pill(body.ok === false ? "stopped" : "ok",
        body.ok === false ? "bad" : "ok"),
      h("span", { class: "mono muted" }, flow.kind || ""),
      (flow.stages || []).length
        ? h("span", { class: "mono muted" }, (flow.stages || []).join(" | "))
        : null),
    body.ok === false && body.error
      ? h("p", { class: "muted" }, body.error)
      : null,
    provenance(flow));
}

export function mount(outlet, params, query) {
  if (query.pipeline && !stages.length) {
    // A composition arrives from another screen as a link — "here is what
    // produced this view, run it yourself". Parsed loosely: the server checks
    // it again before anything executes.
    stages = query.pipeline.split("|").map((part) => {
      const [verb, ...rest] = part.trim().split(/\s+/);
      const values = {};
      for (let index = 0; index < rest.length; index += 2) {
        if (rest[index].startsWith("--")) {
          values[rest[index].slice(2)] = rest[index + 1] || "";
        }
      }
      return { verb, params: values };
    }).filter((stage) => VERBS[stage.verb]);
  }

  const question = h("input", {
    type: "text",
    placeholder: "what breaks if lodash 5 lands?",
    "aria-label": "ask for a composition",
  });

  const text = h("input", {
    type: "text", class: "mono", "aria-label": "the composition, as text",
    style: { flex: "1" },
  });
  const confirm = h("input", { type: "checkbox" });

  redraw = () => {
    const problem = validate(names());
    text.value = pipeline();

    fill(outlet, h("div", { class: "stack" },
      card("Ask, and it writes the composition",
        h("div", { class: "ask" }, question,
          h("button", {
            type: "button", class: "go",
            onclick: async () => {
              const answer = await askPlan(question.value.trim());
              const written = answer.body && answer.body.pipeline;
              if (!written) return;
              stages = written.split("|").map((part) => ({
                verb: part.trim().split(/\s+/)[0], params: {},
              })).filter((stage) => VERBS[stage.verb]);
              redraw();
            },
          }, "Plan"))),

      card("Or build one",
        h("p", { class: "muted prose" },
          "Verbs that cannot follow what is currently flowing are disabled "
          + "rather than hidden, so the shape of the type graph stays visible."),
        palette()),

      card("Pipeline",
        stages.length
          ? h("div", { class: "stack" }, stages.map((stage, index) => [
            index ? h("div", { class: "pipe-arrow" }, "↓") : null,
            stageCard(stage, index),
          ]))
          : h("p", { class: "empty" }, "Pick a source verb to begin."),
        h("div", { class: "row" }, text,
          h("button", {
            type: "button", class: "go",
            disabled: !stages.length || problem !== null || busy,
            onclick: async () => {
              busy = true;
              outcome = null;
              redraw();
              outcome = await runPipeline(pipeline(), {
                confirmed: confirm.checked,
              });
              busy = false;
              redraw();
            },
          }, "Run"),
          h("button", {
            type: "button", class: "go quiet",
            onclick: () => {
              stages = [];
              outcome = null;
              redraw();
            },
          }, "Clear")),
        stages.some((stage) => VERBS[stage.verb].mutates)
          ? h("label", { class: "param" }, confirm,
            h("span", {}, "this composition changes the environment"))
          : null,
        // Inline, at the point of the mistake, and never as a modal — the
        // reader needs to see *which* stage, and a modal hides exactly that.
        problem
          ? h("p", { class: "gap" }, h("span", { class: "fix" }, problem))
          : null),

      result()));
  };

  redraw();
}

export function unmount() {
  redraw = () => {};
}

export const needs = [];
