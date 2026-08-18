/* The data grid — the dense register's reason to exist.
 *
 * ── What "dense" is supposed to mean ─────────────────────────────────────
 *
 * The two registers were, for a while, the same interface at two sizes. That is
 * not a register, it is a zoom control. The people this platform is for spend
 * their day in rich desktop clients — the kind of grid a Windows enterprise
 * application ships, where a screenful of ruled rows is the working surface and
 * the keyboard drives it. `calm` is for reading one answer; **`dense` is for
 * working a list all day**, and those want genuinely different instruments, not
 * the same one at two point sizes.
 *
 * So the difference here is structural rather than metric:
 *
 *   dense   ruled columns, zebra rows, a sticky header you sort by clicking, a
 *           selected row, arrow-key navigation, and a status bar that says what
 *           is in view. Numbers right-aligned on tabular figures so digits line
 *           up down the column and a wrong order of magnitude is visible without
 *           reading a single value.
 *   calm    the same data with the vertical rules gone, the stripes gone, and
 *           room around it — a document rather than an instrument.
 *
 * One component, one column spec, one keyboard model. The register changes what
 * it *is*, not how big it is, and no screen knows which register it is in.
 *
 * ── Why the vertical rules come back here ────────────────────────────────
 *
 * `base.css` drops vertical rules from ordinary tables on purpose: without them
 * the eye reads rows, which is what somebody scanning a list wants. That is
 * right for a table you read and wrong for a grid you *work*, where the task is
 * comparing the same field down a column and the rule is what keeps the eye in
 * it. Both are true; which applies is exactly what the register decides.
 */

import { h } from "../core/dom.js";
import { density } from "./density.js";

const ASC = "asc";
const DESC = "desc";

/** The value a column sorts on — its own if it declares one, else its cell. */
function sortValue(column, row) {
  if (column.sortValue) return column.sortValue(row);
  const raw = row[column.key];
  return raw === null || raw === undefined ? "" : raw;
}

function compare(column, direction) {
  const sign = direction === DESC ? -1 : 1;
  return (left, right) => {
    const a = sortValue(column, left);
    const b = sortValue(column, right);
    if (typeof a === "number" && typeof b === "number") return (a - b) * sign;
    return String(a).localeCompare(String(b), undefined, { numeric: true }) * sign;
  };
}

/**
 * A grid.
 *
 * columns: [{key, label, align?, width?, render?, sortValue?, density?, sortable?}]
 *   align:   "right" for quantities — see the note on tabular figures above
 *   density: "both" (default) | "dense" — a column only the dense register shows
 *
 * `onOpen(row)` fires on Enter or a double click: selecting a row and *acting*
 * on it are different intentions, and collapsing them means a reader cannot look
 * without also navigating away.
 */
export function grid(columns, rows, {
  caption = "",
  empty = "Nothing here.",
  onOpen = null,
  rowKey = (row, index) => String(row.id ?? index),
  status = true,
} = {}) {
  const register = density();
  const dense = register === "bench";
  const shown = columns.filter(
    (column) => !column.density || column.density === "both"
      || column.density === register || (column.density === "dense" && dense),
  );

  if (!rows.length) return h("p", { class: "empty" }, empty);

  let sorted = null;          // {column, direction}
  let picked = 0;

  const body = h("tbody", {});
  const foot = h("div", { class: "grid-status" });
  const head = h("thead", {});

  function order() {
    if (!sorted) return rows.slice();
    return rows.slice().sort(compare(sorted.column, sorted.direction));
  }

  function drawHead() {
    head.replaceChildren(h("tr", {},
      shown.map((column) => {
        const on = sorted && sorted.column === column;
        const sortable = column.sortable !== false;
        const cell = h("th", {
          scope: "col",
          class: `${column.align === "right" ? "right" : ""}${sortable ? " sortable" : ""}`.trim(),
          style: column.width ? { width: column.width } : {},
          // `aria-sort` is what tells a screen-reader user the grid is ordered
          // and by what. Without it the sort is a visual-only affordance.
          "aria-sort": on ? (sorted.direction === ASC ? "ascending" : "descending") : "none",
          tabindex: sortable ? "0" : null,
          onclick: sortable ? () => resort(column) : null,
          onkeydown: sortable
            ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                resort(column);
              }
            }
            : null,
        }, column.label,
        sortable
          ? h("span", { class: "sort", "aria-hidden": "true" },
            on ? (sorted.direction === ASC ? "▲" : "▼") : "")
          : null);
        return cell;
      })));
  }

  function resort(column) {
    sorted = sorted && sorted.column === column
      ? { column, direction: sorted.direction === ASC ? DESC : ASC }
      : { column, direction: ASC };
    picked = 0;
    drawHead();
    drawBody();
  }

  function drawBody() {
    const view = order();
    body.replaceChildren(...view.map((row, index) => h("tr", {
      class: index === picked ? "picked" : "",
      // One tab stop for the whole grid, then arrows within it. Thirty rows
      // each taking a tab stop is how a keyboard user ends up unable to get
      // past a table to the thing after it.
      tabindex: index === picked ? "0" : "-1",
      "aria-selected": index === picked ? "true" : "false",
      onclick: () => { picked = index; drawBody(); },
      ondblclick: onOpen ? () => onOpen(row) : null,
      onkeydown: (event) => keys(event, view, index),
      dataset: { key: rowKey(row, index) },
    },
    shown.map((column) => h("td", {
      class: `${column.align === "right" ? "right" : ""} ${column.className || ""}`.trim(),
    }, column.render ? column.render(row) : row[column.key]))
    )));

    if (status) drawStatus(view);
    const active = body.children[picked];
    if (active && document.activeElement
        && body.contains(document.activeElement)) active.focus();
  }

  function keys(event, view, index) {
    const moves = {
      ArrowDown: Math.min(index + 1, view.length - 1),
      ArrowUp: Math.max(index - 1, 0),
      Home: 0,
      End: view.length - 1,
      PageDown: Math.min(index + 10, view.length - 1),
      PageUp: Math.max(index - 10, 0),
    };
    if (event.key in moves) {
      event.preventDefault();
      picked = moves[event.key];
      drawBody();
      return;
    }
    if (event.key === "Enter" && onOpen) {
      event.preventDefault();
      onOpen(view[index]);
    }
  }

  /* The status bar, which is the other half of a rich client: a grid that does
     not say how much it is showing, what it is sorted by, or where you are in
     it leaves the reader counting rows by hand. */
  function drawStatus(view) {
    foot.replaceChildren(
      h("span", {}, `${view.length} row${view.length === 1 ? "" : "s"}`),
      sorted
        ? h("span", {},
          `sorted by ${sorted.column.label} ${sorted.direction === ASC ? "↑" : "↓"}`)
        : h("span", { class: "muted" }, "unsorted"),
      h("span", { class: "spacer" }),
      h("span", { class: "muted" }, `row ${Math.min(picked + 1, view.length)} of ${view.length}`),
      onOpen ? h("span", { class: "muted" }, "Enter to open") : null,
    );
  }

  drawHead();
  drawBody();

  return h("div", { class: "grid-wrap" },
    h("div", { class: "grid-scroll" },
      h("table", {
        class: "datagrid",
        role: "grid",
        "aria-rowcount": String(rows.length),
      },
      caption ? h("caption", { class: "muted" }, caption) : null,
      head, body)),
    status ? foot : null);
}
