/* Building nodes, not strings.
 *
 * `innerHTML` appears in zero files, and that is a structural rule rather than a
 * review habit — `tests/test_slpie_ui_assets.py` enforces it. The reason is the
 * Developer Portal: it renders API descriptions somebody else authored, and
 * `script-src 'self'` does nothing about a string that becomes markup. There is
 * no safe subset to remember, so there is no exception to make.
 *
 * `h` is the whole interface. Everything above builds from it.
 */

/** h("div", {class: "card"}, h("h2", {}, "Findings"), rows) */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }

  append(node, children);
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * `h`, for SVG.
 *
 * A separate function rather than a flag on `h`, because the difference is not
 * cosmetic: `createElement("circle")` yields an `HTMLUnknownElement` that lays
 * out as nothing and renders as nothing, with no error anywhere. An SVG tree has
 * to be built in its namespace or it silently does not exist, and a silent
 * nothing is the worst failure mode a drawing can have.
 *
 * Attributes only — SVG has no `className` string property and no useful style
 * shorthand, so `class` is set as an attribute like everything else.
 */
export function svg(tag, props = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }

  append(node, children);
  return node;
}

/** Text, a node, or any nesting of arrays of those. */
export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace a node's children in one step, so a redraw never shows a half-state. */
export function fill(node, ...children) {
  return append(clear(node), children);
}

export function el(id) {
  return document.getElementById(id);
}

/** A link that is a real link. Middle-click and copy-link work, which is where
 *  most hand-rolled consoles fail: a `<div onclick>` cannot be opened in a new
 *  tab and cannot be shared. */
export function link(href, props, ...children) {
  return h("a", { href, ...props }, ...children);
}

/** Delegated listening, so a redrawn table does not need its handlers rebound. */
export function on(root, event, selector, handler) {
  root.addEventListener(event, (raw) => {
    const found = raw.target.closest(selector);
    if (found && root.contains(found)) handler(raw, found);
  });
}
