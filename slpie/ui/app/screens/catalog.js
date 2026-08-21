/* The catalogue: four nested levels, deep-linkable at every one.
 *
 * Tenant → realm → dataset → object, with search across all of it. The shape is
 * the one a data catalogue has because it is the shape tenancy has, and the
 * router's optional parameters are what let one screen serve all four depths
 * rather than four screens serving one each.
 *
 * The breadcrumb comes from the manifest rather than from history, which is the
 * whole reason a deep link is worth having: a pasted URL must show the same
 * trail a click-through does, or the link is not a location.
 */

import { fill, h, link } from "../core/dom.js";
import { cell, subscribe } from "../core/store.js";
import { confidence, count, short } from "../core/format.js";
import { datasets as loadDatasets, search as loadSearch } from "../data/queries.js";
import { card, claim, key, panel, unsurveyed } from "../components/panel.js";
import { pill } from "../components/pill.js";
import { scrolling, table } from "../components/table.js";

const stops = [];

function watch(key, draw) {
  stops.push(subscribe(key, draw));
}

function trail(params) {
  const steps = [
    ["#/catalog", "Catalog"],
    params.tenant && [`#/catalog/${params.tenant}`, params.tenant],
    params.realm && [`#/catalog/${params.tenant}/${params.realm}`, params.realm],
    params.dataset && [
      `#/catalog/${params.tenant}/${params.realm}/${params.dataset}`,
      params.dataset,
    ],
  ].filter(Boolean);

  return h("nav", { class: "row muted", "aria-label": "breadcrumb" },
    steps.map(([href, label], index) => [
      index === steps.length - 1
        ? h("span", { "aria-current": "page" }, label)
        : link(href, {}, label),
      index === steps.length - 1 ? null : h("span", {}, "/"),
    ]));
}

function datasetRows(rows, params) {
  const columns = [
    {
      key: "name",
      label: "Dataset",
      render: (row) => link(
        `#/catalog/${row.dataset.scope.tenant}/${row.dataset.scope.realm || "-"}`
        + `/${encodeURIComponent(row.dataset.name)}`,
        {}, row.dataset.name,
      ),
    },
    {
      key: "visibility",
      label: "Visibility",
      render: (row) => pill(row.visibility || "private"),
    },
    { key: "tier", label: "Tier", className: "mono", density: "bench",
      render: (row) => row.dataset.tier || "—" },
    { key: "bytes", label: "Bytes", className: "mono right", density: "bench",
      render: (row) => count(row.dataset.bytes || 0) },
  ];
  return scrolling(table(columns, rows, {
    empty: params.tenant
      ? `No datasets are granted in ${params.tenant}.`
      : "No datasets are granted.",
  }));
}

function results(nodes) {
  const columns = [
    {
      key: "id",
      label: "Node",
      className: "mono",
      // The identity carries its own certainty as a rule beneath it, so a
      // reader scanning the column sees how each row was arrived at without
      // reading a single number.
      render: (row) => claim(
        link(`#/node/${encodeURIComponent(row.id)}`, {}, short(row.identity || row.id, 28)),
        row.confidence,
      ),
    },
    { key: "kind", label: "Kind", density: "bench" },
    {
      key: "confidence",
      label: "Score",
      className: "right mono",
      // Kept, and demoted to the dense register only. The number is what you
      // check once you have decided a row is worth checking; the mark is what
      // makes you decide.
      density: "bench",
      render: (row) => confidence(row.confidence),
    },
  ];
  return scrolling(table(columns, nodes, { empty: "Nothing matched." }));
}

export function mount(outlet, params, query) {
  const term = query.q || "";
  const datasetKey = `datasets:${params.tenant || ""}/${params.realm || ""}`;
  const searchKey = `search:${term}`;

  const field = h("input", {
    type: "text",
    value: term,
    placeholder: "search the graph…",
    "aria-label": "search",
  });
  const go = h("button", {
    type: "button",
    class: "go",
    onclick: () => {
      const wanted = field.value.trim();
      // A search is a location, so it goes in the URL. Somebody who found
      // something can send the link rather than describing the query.
      window.location.hash = `#/catalog${
        params.tenant ? `/${params.tenant}` : ""
      }${wanted ? `?q=${encodeURIComponent(wanted)}` : ""}`;
    },
  }, "Search");

  function draw() {
    const grants = cell(datasetKey);
    const found = cell(searchKey);

    fill(outlet, h("div", { class: "stack" },
      trail(params),
      h("div", { class: "ask" }, field, go),
      card("Datasets", panel(grants, (value) => {
        const rows = value.datasets || [];
        // A tenancy nobody has provisioned and a tenancy this build cannot see
        // are different answers, and only one of them is an empty table.
        return rows.length
          ? datasetRows(rows, params)
          : unsurveyed(
            "not catalogued",
            "No control plane is attached to this build, so nothing here has "
            + "been surveyed — this is not the same as there being no datasets.",
          );
      }, { sentence: "Nothing here has been catalogued." })),
      term
        ? card(`Matches for “${term}”`,
          panel(found, (value) => h("div", { class: "stack" },
            key(), results(value.results || [])),
          { sentence: "Nothing matched." }))
        : null));
  }

  watch(datasetKey, draw);
  watch(searchKey, draw);
  draw();

  loadDatasets(params.tenant || "", params.realm || "");
  if (term) loadSearch(term);
}

export function unmount() {
  while (stops.length) stops.pop()();
}

export const needs = ["node_asserted", "node_retired"];
