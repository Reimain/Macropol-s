/* Tables, built from a column spec.
 *
 * The column spec is where the density axis reaches something a token cannot
 * express. Nine columns is right on a wide screen in `bench` and wrong in
 * `reading`, and the answer is not a second table — it is a `density` field per
 * column and one filter. Data, not a fork.
 *
 * Every header carries `scope="col"`, which is the difference between a table a
 * screen reader can navigate and a grid of unlabelled cells.
 */

import { h } from "../core/dom.js";
import { density } from "./density.js";

/**
 * columns: [{key, label, render?, className?, density?}]
 *   density: "both" (default) | "bench" — a column only the dense register shows
 */
export function table(columns, rows, { caption = "", empty = "Nothing here." } = {}) {
  const register = density();
  const shown = columns.filter(
    (column) => !column.density || column.density === "both" || column.density === register,
  );

  if (!rows.length) return h("p", { class: "empty" }, empty);

  return h("table", {},
    caption ? h("caption", { class: "muted" }, caption) : null,
    h("thead", {}, h("tr", {},
      shown.map((column) => h("th", { scope: "col" }, column.label)))),
    h("tbody", {}, rows.map((row) => h("tr", {},
      shown.map((column) => h(
        "td",
        { class: column.className || "" },
        column.render ? column.render(row) : row[column.key],
      ))))),
  );
}

/** A scrolling wrapper, so a wide table never widens the page itself. */
export function scrolling(node) {
  return h("div", { class: "scroll" }, node);
}
