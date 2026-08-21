/* The dictionary. Components addressed by key, so a screen can be data.
 *
 * `ui/` holds about thirty-five exported functions and most of them take a
 * shape only hand-written JavaScript can build — `claim(value, confidence)`
 * wants a number no payload carries. What is *addressable* is the subset a
 * block can drive with data alone, and `COMPONENTS` below is exactly that set.
 * `slpie/ui/contract.py` holds the same set, and a test asserts they are equal
 * in both directions: a name Python knows with no implementation here renders
 * nothing, and an implementation here Python cannot name is unreachable.
 *
 * This is the micro-framework half of the thesis. A framework ships `Table` and
 * `List` and every product wears the framework's vocabulary; here the component
 * is a key, the data comes from the kernel, and the labels come from the
 * reader's own lexicon. Same layout, their words.
 *
 * Rendering behaviour that a block cannot carry — a cell renderer is a function
 * — becomes a *named* format, resolved in `FORMATS`. Naming the behaviour is
 * the same move as naming the component, one level down.
 */

import { h, link } from "../core/dom.js";
import { cite, count as fmtCount } from "../core/format.js";
import { label as resolveLabel, t } from "../core/lexicon.js";
import { bars as barList, stat as statTile } from "./chart.js";
import { grid } from "./grid.js";
import { card, metric } from "./panel.js";
import { pill, severity as severityPill } from "./pill.js";
import { table as plainTable } from "./table.js";

/* --- cell formats ------------------------------------------------------- */

const FORMATS = {
  "": (value) => (value === null || value === undefined ? "—" : String(value)),
  mono: (value) => h("span", { class: "mono" }, String(value ?? "—")),
  severity: (value) => severityPill(String(value || "info")),
  pill: (value) => pill(String(value ?? "—")),
  cite: (value) => h("span", { class: "cite mono" }, cite(value)),
  // A list renders as its length; a number renders as itself. Both are "how
  // many", and a block should not have to say which the payload happens to use.
  count: (value) => String(Array.isArray(value) ? value.length : (value ?? 0)),
  confidence: (value) => h("span", { class: "mono" },
    Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "—"),
  link: (value) => String(value ?? "—"),
};

/** `#/node/:id` with `:id` filled from the row. */
function href(template, row) {
  return template.replace(/:(\w+)/g, (whole, key) =>
    encodeURIComponent(String(row[key] ?? "")) || whole);
}

function cellFor(column) {
  const format = FORMATS[column.format] || FORMATS[""];
  return (row) => {
    // An empty column key means the row *is* the value — a payload of plain
    // strings, which several routes return. Without this a list of route names
    // renders a column of blanks, which looks like a broken screen rather than
    // a payload with no fields.
    const value = column.key ? row[column.key] : row;
    const drawn = format(value);
    if (!column.link) return drawn;
    return link(href(column.link, row), {}, drawn);
  };
}

function columnsFor(specs) {
  return specs.map((column) => ({
    key: column.key,
    label: resolveLabel(column.label || column.key),
    align: column.align || "",
    density: column.density || "",
    render: cellFor(column),
    sortValue: column.format === "count"
      ? (row) => {
        const value = column.key ? row[column.key] : row;
        return Array.isArray(value) ? value.length : Number(value) || 0;
      }
      : undefined,
  }));
}

/* --- reading the payload ------------------------------------------------ */

/** A dotted path into the body. `""` means the body itself. */
export function select(body, path) {
  if (!path) return body;
  let current = body;
  for (const part of path.split(".")) {
    if (current === null || current === undefined) return null;
    current = current[part];
  }
  return current === undefined ? null : current;
}

function rowsOf(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    // One list among the fields is the common envelope: `{findings: [...]}`.
    const lists = Object.values(value).filter(Array.isArray);
    if (lists.length === 1) return lists[0];
  }
  return null;
}

function fieldsOf(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return Object.entries(value).filter(
    ([, item]) => item === null || typeof item !== "object",
  );
}

/** Column specs inferred from rows, when nobody declared any. */
function inferColumns(rows) {
  const sample = rows.slice(0, 20);
  if (sample.every((row) => row === null || typeof row !== "object")) {
    return [{ key: "", label: "Value", format: "mono" }];
  }
  const keys = [];
  for (const row of sample) {
    for (const key of Object.keys(row || {})) {
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys.slice(0, 9).map((key, index) => ({
    key,
    label: key.replace(/_/g, " "),
    // Beyond the fourth column the value is usually detail rather than
    // identity, so it belongs in the dense register only. Nine is where
    // `grid.js` stops being readable, which is why the slice is there too.
    density: index > 3 ? "dense" : "",
    format: sample.some((row) => Array.isArray((row || {})[key])) ? "count" : "",
  }));
}

/* --- the components ----------------------------------------------------- */

function gridBlock(block, data) {
  const rows = rowsOf(select(data, block.select));
  if (!rows) return null;
  const specs = block.columns && block.columns.length
    ? block.columns
    : inferColumns(rows);
  return grid(columnsFor(specs), rows, {
    empty: `Nothing here yet.`,
  });
}

function tableBlock(block, data) {
  const rows = rowsOf(select(data, block.select)) || [];
  const specs = block.columns && block.columns.length
    ? block.columns
    : inferColumns(rows);
  return plainTable(columnsFor(specs), rows);
}

function metricsBlock(block, data) {
  const fields = fieldsOf(select(data, block.select));
  if (!fields || !fields.length) return null;
  // `.grid` is the card container the rest of the interface already uses, and
  // twelve is where a wall of numbers stops being readable — a payload with
  // forty scalar fields is a table, not a set of metrics, and `auto` will have
  // sent it here only because it had no rows to make one from.
  return h("div", { class: "grid" }, fields.slice(0, 12).map(([key, value]) =>
    metric(resolveLabel(key.replace(/_/g, " ")), String(value ?? "—"))));
}

function statBlock(block, data) {
  const value = select(data, block.select);
  return statTile(
    resolveLabel(block.title || block.select || "value"),
    String(Array.isArray(value) ? value.length : (value ?? "—")),
    { note: block.options && block.options.note ? String(block.options.note) : "" },
  );
}

function barsBlock(block, data) {
  const rows = rowsOf(select(data, block.select)) || [];
  const labelKey = (block.options && block.options.label) || "label";
  const valueKey = (block.options && block.options.value) || "value";
  // `chart.bars` destructures `[name, value]` pairs. This handed it objects,
  // so every block naming `bars` threw `object is not iterable` the first time
  // one was actually declared — the component was in the dictionary and had
  // never been drawn.
  return barList(rows.map((row) => [
    String(row[labelKey] ?? ""),
    Number(row[valueKey]) || 0,
  ]));
}

function proseBlock(block) {
  return h("p", { class: "muted prose" }, resolveLabel(block.title || ""));
}

/**
 * Render by shape, when nobody could declare one.
 *
 * Python cannot know the body of an arbitrary route, and declaring columns for
 * each by hand would be a list that drifts the first time a payload changes. So
 * this looks at what actually arrived: rows become a table, fields become
 * metrics, and anything else says it could not be laid out rather than
 * pretending. That last branch matters — a silent `<pre>` labelled as a screen
 * is how a product looks finished and is not.
 */
function autoBlock(block, data) {
  const value = select(data, block.select);
  if (value === null || value === undefined) return null;

  const rows = rowsOf(value);
  if (rows && rows.length) return gridBlock(block, data);

  const fields = fieldsOf(value);
  if (fields && fields.length) return metricsBlock(block, data);

  return h("div", { class: "stack" },
    h("p", { class: "muted prose" },
      "This payload is not rows and is not a flat set of fields, so there is "
      + "no honest table for it. It is shown as it arrived."),
    h("pre", { class: "mono scroll" }, JSON.stringify(value, null, 2)));
}

/**
 * The verb forms. The one component that is a control rather than a view.
 *
 * Injected by the screen rather than imported, because importing `screens/`
 * from `ui/` would break the ring rule the browser code follows — `ui/` may
 * import `core/` and nothing else. The screen holds the runner and passes it in.
 */
function runnerBlock(block, data, options) {
  const draw = options && options.runner;
  if (typeof draw !== "function") return null;
  return draw(block);
}

//: Components that already show `block.title` inside themselves.
const SELF_LABELLING = new Set(["stat"]);

export const COMPONENTS = {
  auto: autoBlock,
  grid: gridBlock,
  table: tableBlock,
  metrics: metricsBlock,
  stat: statBlock,
  bars: barsBlock,
  prose: proseBlock,
  runner: runnerBlock,
};

/**
 * Draw one block.
 *
 * A component nobody implements renders as a named absence rather than as
 * nothing: a blank area is a bug somebody has to reproduce, and a sentence
 * saying which key was missing is one they can fix. The manifest is validated
 * in Python, so this should be unreachable — which is exactly when a silent
 * failure would go unnoticed longest.
 */
export function render(block, data, options = {}) {
  const draw = COMPONENTS[block.component];
  if (!draw) {
    return h("p", { class: "empty" },
      `No component named "${block.component}" — the screen manifest asks for `
      + `one this build does not have.`);
  }
  const body = draw(block, data, options);
  if (body === null || body === undefined) return null;
  // A stat draws its own label from the same field, so wrapping it in a titled
  // card printed the word twice, one above the other. Components that label
  // themselves are not wrapped.
  if (SELF_LABELLING.has(block.component)) return body;
  const heading = resolveLabel(block.title || "");
  return heading ? card(heading, body) : body;
}

/** Every block of a screen, in order, skipping the ones with nothing to say. */
export function compose(blocks, dataFor, options = {}) {
  const drawn = (blocks || [])
    .map((block) => render(block, dataFor(block), options))
    .filter(Boolean);
  return h("div", { class: "stack" }, drawn);
}

export { t };
