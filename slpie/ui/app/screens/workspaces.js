/* Workspaces: tenancy, quota and headroom.
 *
 * `ControlPlane.status()` and `Quota.headroom()` both describe themselves in
 * source as "what an administrator's console renders". This is that console.
 *
 * Headroom is rendered as a bar against its ceiling rather than as a number
 * alone, because the useful question is never "how many CPUs are in use" — it
 * is "how close is this tenant to being refused", and a ratio answers that at a
 * glance where two numbers do not.
 */

import { fill, h } from "../core/dom.js";
import { cell, subscribe } from "../core/store.js";
import { count } from "../core/format.js";
import { workspaces as loadWorkspaces } from "../data/queries.js";
import { card, panel } from "../components/panel.js";
import { pill } from "../components/pill.js";
import { scrolling, table } from "../components/table.js";

let stop = null;

/** A bar plus its numbers. Colour is never the only channel, so the ratio is
 *  written out beside it. */
function headroom(left, ceiling) {
  const used = Math.max(0, Number(ceiling) - Number(left));
  const share = ceiling ? Math.min(1, used / Number(ceiling)) : 0;
  const tone = share >= 0.9 ? "bad" : share >= 0.75 ? "warn" : "ok";
  return h("div", {},
    h("div", { class: "bar" },
      h("div", { style: { width: `${Math.round(share * 100)}%` } })),
    h("span", { class: "mono muted" }, `${count(used)} / ${count(ceiling)}`),
    " ",
    pill(`${Math.round(share * 100)}%`, tone));
}

function tenants(rows) {
  const columns = [
    { key: "tenant", label: "Tenant", className: "mono" },
    {
      key: "workspaces",
      label: "Workspaces",
      render: (row) => headroom(
        row.headroom.workspaces, row.quota.max_workspaces,
      ),
    },
    {
      key: "cpu",
      label: "CPU",
      render: (row) => headroom(row.headroom.cpu, row.quota.max_cpu),
    },
    {
      key: "memory",
      label: "Memory",
      density: "bench",
      render: (row) => headroom(row.headroom.memory_mb, row.quota.max_memory_mb),
    },
    {
      key: "disk",
      label: "Disk",
      density: "bench",
      render: (row) => headroom(row.headroom.disk_gb, row.quota.max_disk_gb),
    },
  ];
  return scrolling(table(columns, rows, {
    empty: "No tenants have been provisioned.",
  }));
}

export function mount(outlet) {
  function draw() {
    const held = cell("admin-workspaces");
    const body = panel(held, (value) => h("div", { class: "stack" },
      h("div", { class: "grid" },
        card("Region", h("div", { class: "metric" }, value.region || "—")),
        card("Runtime", h("div", { class: "metric" }, value.runtime || "in-process")),
        card("Workspaces", h("div", { class: "metric" },
          count(value.workspaces), h("small", {}, `${count(value.live)} live`))),
        card("Tenants", h("div", { class: "metric" },
          count((value.tenants || []).length)))),
      card("Quota and headroom", tenants(value.tenants || []))),
      {
        // The 409 the platform answers when no plane is attached is a state,
        // not a failure: a single-tenant install has no tenancy to administer.
        sentence: "No control plane is attached, so there is no tenancy here.",
        rows: 5,
      });

    fill(outlet, h("div", { class: "stack" },
      h("p", { class: "muted prose" },
        "Every tenant's ceiling and what is left of it. A workspace is refused "
        + "when its allocation would cross the ceiling, so this is the screen "
        + "that predicts a refusal before somebody hits one."),
      body));
  }

  stop = subscribe("admin-workspaces", draw);
  draw();
  loadWorkspaces();
}

export function unmount() {
  if (stop) stop();
  stop = null;
}

export const needs = [];
