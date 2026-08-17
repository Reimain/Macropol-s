/* Routing as a table, not an if-chain.
 *
 * Hash routing (`#/catalog/acme/prod/orders`), deliberately, and not the
 * History API. Two reasons, both concrete:
 *
 *  - `server.py` resolves every non-`/api/` path to a file, so `/findings`
 *    would 404. A SPA fallback would mean loosening the path-containment guard
 *    that stops a crafted request reading the disk, and that is a bad trade for
 *    a prettier URL.
 *  - PWA-first. A cached `index.html` serves every hash route with the network
 *    unplugged and no service-worker cooperation at all. History routes need
 *    the worker to rewrite navigations, which is a second mechanism to get
 *    right for no gain.
 *
 * Patterns are `/catalog/:tenant/:realm?/:dataset?`, where `:name?` is optional
 * and `*rest` swallows the remainder. Sixty lines, and every list row can be a
 * real `<a href="#/...">` — which is where most hand-rolled consoles fail:
 * a `<div onclick>` cannot be middle-clicked, cannot be copied, and cannot be
 * read by a screen reader as the link it plainly is.
 */

const routes = [];
let listener = null;
let active = null;

export function route(pattern, definition) {
  routes.push({ pattern, parts: pattern.split("/").filter(Boolean), definition });
  return definition;
}

export function navigate(path, { replace = false } = {}) {
  const target = path.startsWith("#") ? path : `#${path}`;
  if (replace) window.location.replace(target);
  else window.location.hash = target;
}

export function current() {
  return active;
}

/** `#/catalog/acme?limit=20` → {path, params, query} */
export function parse(hash) {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const [path, search = ""] = raw.split("?");
  const query = Object.fromEntries(new URLSearchParams(search).entries());
  const parts = path.split("/").filter(Boolean);

  for (const entry of routes) {
    const params = match(entry.parts, parts);
    if (params) return { path, params, query, route: entry };
  }
  return { path, params: {}, query, route: null };
}

function match(pattern, parts) {
  const params = {};
  let index = 0;

  for (const segment of pattern) {
    if (segment.startsWith("*")) {
      params[segment.slice(1) || "rest"] = parts.slice(index).join("/");
      return params;
    }
    const optional = segment.endsWith("?");
    const name = segment.replace(/^:/, "").replace(/\?$/, "");
    const value = parts[index];

    if (value === undefined) {
      if (!optional) return null;
      index += 1;
      continue;
    }
    if (!segment.startsWith(":")) {
      if (segment !== value) return null;
    } else {
      params[name] = decodeURIComponent(value);
    }
    index += 1;
  }
  // A deeper path is a different route, not this one with extra on the end —
  // otherwise `/graph` would answer for `/graph/anything`.
  return index >= parts.length ? params : null;
}

export function start(onChange) {
  listener = onChange;
  window.addEventListener("hashchange", fire);
  fire();
}

function fire() {
  active = parse(window.location.hash);
  if (listener) listener(active);
}

/** For tests and for a full re-render after the feed reports a gap. */
export function reset() {
  routes.length = 0;
  active = null;
}
