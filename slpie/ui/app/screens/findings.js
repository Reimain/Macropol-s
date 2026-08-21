/* Findings: the list, the detail, and the evidence a reviewer can open.
 *
 * The pattern every other detail screen reuses: facets that are links rather
 * than buttons, so a severity is deep-linkable and shareable; a list that
 * refetches on the events that change it and on no others; and evidence that
 * terminates in `file:line`, because a finding a reviewer cannot check is a
 * claim rather than a finding.
 */

import { fill, h, link } from "../core/dom.js";
import { cell, subscribe } from "../core/store.js";
import { cite, count } from "../core/format.js";
import { findings as loadFindings } from "../data/queries.js";
import { card, claim, key, panel, unsurveyed } from "../components/panel.js";
import { severity } from "../components/pill.js";
import { grid } from "../components/grid.js";

const SEVERITIES = ["", "critical", "high", "medium", "low", "info"];

let stop = null;

function facets(chosen) {
  return h("div", { class: "row", role: "group", "aria-label": "severity" },
    SEVERITIES.map((name) => link(
      `#/findings${name ? `/${name}` : ""}`,
      {
        class: `chip ${name === chosen ? "usable" : ""}`,
        "aria-current": name === chosen ? "true" : null,
      },
      name || "all",
    )));
}

function evidence(item) {
  const found = item.evidence || [];
  if (!found.length) {
    // Not "no evidence" — the platform does not raise a finding without any.
    // An empty list here means this projection did not carry it, which is a
    // different statement and has to read as one.
    return unsurveyed(
      "evidence not carried",
      "This view did not fetch the evidence behind the finding. It exists — "
      + "the platform raises nothing without it — and it is not here.",
    );
  }
  return h("ul", { class: "reasoning" },
    found.map((piece) => h("li", {},
      // The kind of evidence *is* the certainty: a lockfile pin and a name
      // heuristic are both "evidence", and conflating them is the mistake the
      // whole confidence ladder exists to prevent.
      claim(piece.kind || "evidence", piece.confidence),
      h("div", { class: "cite mono" }, cite(piece.location)),
      piece.excerpt ? h("div", { class: "muted mono" }, piece.excerpt) : null)));
}

function detail(item) {
  return card(item.title || item.id,
    h("div", { class: "row" }, severity(item.severity), item.kind
      ? h("span", { class: "mono muted" }, item.kind) : null),
    item.detail ? h("p", { class: "reading" }, item.detail) : null,
    item.remediation
      ? h("div", { class: "gap" },
        h("div", { class: "what" }, "Remediation"),
        h("div", { class: "fix" }, item.remediation))
      : null,
    evidence(item),
    item.subject
      ? link(`#/node/${encodeURIComponent(item.subject)}`, {}, "open the subject")
      : null);
}

function list(items, chosen, open) {
  const RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const columns = [
    {
      key: "severity",
      label: "Severity",
      width: "var(--label-w)",
      // Sorted by *rank*, not alphabetically: "critical" before "high" is the
      // order that matters, and `c` before `h` agreeing with it is a
      // coincidence that stops holding at "info" and "low".
      sortValue: (row) => RANK[String(row.severity).toLowerCase()] ?? 9,
      render: (row) => severity(row.severity),
    },
    {
      key: "title",
      label: "Finding",
      render: (row) => link(
        `#/findings${chosen ? `/${chosen}` : ""}?open=${encodeURIComponent(row.id || "")}`,
        {}, row.title || row.id,
      ),
    },
    { key: "kind", label: "Kind", className: "mono", density: "dense" },
    {
      key: "subject",
      label: "Subject",
      className: "mono",
      density: "dense",
      render: (row) => row.subject || "\u2014",
    },
    {
      key: "evidence",
      label: "Evidence",
      align: "right",
      density: "dense",
      sortValue: (row) => (row.evidence || []).length,
      render: (row) => String((row.evidence || []).length),
    },
  ];

  return grid(columns, items, {
    empty: chosen
      ? `Nothing at ${chosen} severity.`
      : "No findings \u2014 nothing wrong is known yet.",
    onOpen: (row) => {
      window.location.hash =
        `#/findings${chosen ? `/${chosen}` : ""}?open=${encodeURIComponent(row.id || "")}`;
    },
  });
}

export function mount(outlet, params, query) {
  const chosen = params.severity || "";
  const cellKey = `findings:${chosen}`;
  const opened = query.open || "";

  function draw() {
    const held = cell(cellKey);
    const body = panel(held, (value) => {
      const items = (value && value.findings) || [];
      const open = items.find((item) => String(item.id) === opened);
      return h("div", { class: "stack" },
        h("p", { class: "muted" },
          `${count(items.length)} finding${items.length === 1 ? "" : "s"}`),
        list(items, chosen, open),
        open ? detail(open) : null,
        open ? key() : null);
    }, { sentence: "No findings.", rows: 6 });

    fill(outlet, h("div", { class: "stack" }, facets(chosen), card("", body)));
  }

  stop = subscribe(cellKey, draw);
  draw();
  loadFindings(chosen);
}

export function unmount() {
  if (stop) stop();
  stop = null;
}

//: The event kinds that make this screen's answer out of date. Declared rather
//: than matched with a `startsWith` chain, so a new kind wires itself.
export const needs = ["finding_raised", "constraint_violated"];
