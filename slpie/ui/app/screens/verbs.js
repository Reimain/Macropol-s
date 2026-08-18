/* Verbs — every capability this build has, as a working list.
 *
 * The dense register's showcase, and not arbitrarily: forty-eight typed
 * capabilities with six attributes each is exactly the shape a rich-client grid
 * exists for. Sort by what a verb consumes to see the type graph's layers; sort
 * by group to see the product's own decomposition; filter to find the one you
 * half-remember the name of. None of that is possible in a list of cards.
 *
 * It is also the §24 thesis made checkable by eye: this table is a projection of
 * the one registry, so a verb here is a CLI subcommand, an HTTP route, a manual
 * page and a planner vocabulary entry — and if it were not, the suite would have
 * failed before this screen rendered.
 */

import { fill, h, link } from "../core/dom.js";
import { GROUPS, VERBS } from "../data/client.js";
import { card } from "../ui/panel.js";
import { grid } from "../ui/grid.js";
import { stat } from "../ui/chart.js";

let redraw = () => {};

function rows() {
  return Object.entries(VERBS).map(([name, spec]) => ({
    id: name,
    name,
    group: spec.group,
    // The contract spells kinds in lower case ("nothing", "observations").
    // Comparing against "NOTHING" silently matched nothing at all, and the
    // source count read 0 beside a column plainly full of them.
    consumes: String(spec.consumes || "nothing").toLowerCase(),
    produces: spec.produces,
    mutates: spec.mutates,
    params: (spec.params || []).length,
    summary: spec.summary || "",
  }));
}

export function mount(outlet) {
  const filter = h("input", {
    type: "search",
    placeholder: "filter by name, group or kind…",
    "aria-label": "filter the verbs",
    oninput: () => redraw(),
  });

  redraw = () => {
    const needle = filter.value.trim().toLowerCase();
    const all = rows();
    const shown = needle
      ? all.filter((row) => `${row.name} ${row.group} ${row.consumes} ${row.produces} ${row.summary}`
        .toLowerCase().includes(needle))
      : all;

    const columns = [
      {
        key: "name",
        label: "Verb",
        width: "var(--kind-w)",
        render: (row) => h("span", { class: "mono" }, row.name),
      },
      { key: "group", label: "Group", width: "var(--label-w)" },
      {
        key: "consumes",
        label: "Consumes",
        className: "mono",
        // A source verb starts a pipeline. Rendering that as the literal
        // "NOTHING" reads as a missing value rather than as the fact that it
        // takes no input, which is the single most useful thing about it.
        render: (row) => row.consumes === "nothing"
          ? h("span", { class: "muted" }, "— source")
          : row.consumes,
      },
      { key: "produces", label: "Produces", className: "mono" },
      {
        key: "params",
        label: "Params",
        align: "right",
        density: "dense",
        render: (row) => String(row.params),
      },
      {
        key: "mutates",
        label: "Changes",
        density: "dense",
        sortValue: (row) => (row.mutates ? 0 : 1),
        render: (row) => row.mutates
          ? h("span", { class: "status warn" }, "environment")
          : h("span", { class: "muted" }, "read-only"),
      },
      { key: "summary", label: "What it does", density: "dense" },
    ];

    fill(outlet, h("div", { class: "stack" },
      h("div", { class: "kpi" },
        stat("Verbs", String(all.length), { note: "each one a CLI command, a route and a manual page" }),
        stat("Groups", String(Object.keys(GROUPS).length)),
        stat("Sources", String(all.filter((row) => row.consumes === "nothing").length), {
          note: "can start a pipeline",
        }),
        stat("Mutating", String(all.filter((row) => row.mutates).length), {
          note: "behind the confirm gate",
        })),

      card(null,
        h("div", { class: "row toolbar" },
          filter,
          h("span", { class: "muted" },
            needle ? `${shown.length} of ${all.length}` : `${all.length} verbs`),
          h("span", { class: "spacer" }),
          link("#/compose", { class: "go quiet" }, "Build a pipeline")),
        grid(columns, shown, {
          empty: `No verb matches “${needle}”.`,
          onOpen: (row) => {
            window.location.hash = `#/compose?pipeline=${encodeURIComponent(row.name)}`;
          },
        }))));
  };

  redraw();
}

export function unmount() {
  redraw = () => {};
}

export const needs = [];
